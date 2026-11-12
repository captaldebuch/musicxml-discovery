"""Inventory and screen MusicXML from rights-approved GitHub repositories.

The default mode is read-only inventory.  ``--apply`` downloads a bounded or
complete selection into ``staging/`` and runs the deterministic quality gates.
Downloaded files remain in an isolated staging directory for downstream review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .quality import MusicXMLAuditError, QualityThresholds, audit_musicxml_payload


API_ROOT = "https://api.github.com"
RAW_ROOT = "https://raw.githubusercontent.com"
MAX_DOWNLOAD_BYTES = 15 * 1024 * 1024
USER_AGENT = "musicxml-discovery/0.1.0"
GITHUB_API_VERSION = "2026-03-10"


class GitHubAPIError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, token: str | None = None, timeout: int = 45) -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self.timeout = timeout

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        headers = {
            "Accept": accept,
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": USER_AGENT,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def get_json(self, endpoint: str, params: dict[str, str] | None = None) -> Any:
        url = endpoint if endpoint.startswith("https://") else f"{API_ROOT}{endpoint}"
        if params:
            url = f"{url}?{urlencode(params)}"
        data = self.get_bytes(url, api_request=True)
        try:
            return json.loads(data)
        except json.JSONDecodeError as exc:
            raise GitHubAPIError(f"non_json_response:{url}") from exc

    def get_bytes(self, url: str, *, api_request: bool = False) -> bytes:
        request = Request(url, headers=self._headers() if api_request else {
            "User-Agent": USER_AGENT
        })
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    declared = response.headers.get("Content-Length")
                    if declared and int(declared) > MAX_DOWNLOAD_BYTES:
                        raise GitHubAPIError("download_size_limit")
                    data = response.read(MAX_DOWNLOAD_BYTES + 1)
                    if len(data) > MAX_DOWNLOAD_BYTES:
                        raise GitHubAPIError("download_size_limit")
                    return data
            except HTTPError as exc:
                last_error = exc
                if exc.code not in {429, 500, 502, 503, 504}:
                    remaining = exc.headers.get("X-RateLimit-Remaining", "unknown")
                    raise GitHubAPIError(f"github_http_{exc.code}:rate_remaining={remaining}") from exc
                retry_after = int(exc.headers.get("Retry-After", "0") or 0)
                time.sleep(max(retry_after, 2 ** attempt))
            except URLError as exc:
                last_error = exc
                time.sleep(2 ** attempt)
        raise GitHubAPIError(f"github_request_failed:{last_error}")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def git_blob_sha(payload: bytes, expected: str) -> str:
    """Reproduce Git's blob object hash for transport-integrity verification."""

    framed = f"blob {len(payload)}\0".encode("ascii") + payload
    if len(expected) == 40:
        # SHA-1 is required by the Git object format here, not used as a
        # cryptographic trust decision. A separate SHA-256 is also recorded.
        return hashlib.sha1(framed).hexdigest()
    if len(expected) == 64:
        return hashlib.sha256(framed).hexdigest()
    raise ValueError("unsupported_git_blob_hash")


def source_id_for(source: dict) -> str:
    return source.get("id") or source["repository"].replace("/", "__")


def safe_relative_path(value: str) -> Path:
    """Convert a Git path to a local relative path without traversal."""

    normalized = value.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        raise ValueError("unsafe_source_path")
    return Path(*pure.parts)


def infer_path_metadata(path: str) -> tuple[str, str]:
    parts = path.split("/")
    if len(parts) >= 5 and parts[0] == "scores":
        raw_composer = parts[1].replace("_", " ")
        if "," in raw_composer:
            family, given = (item.strip() for item in raw_composer.split(",", 1))
            composer = " ".join(item for item in (given, family) if item)
        else:
            composer = raw_composer
        return parts[-2].replace("_", " "), composer
    return Path(path).stem.replace("_", " "), ""


def load_config(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != "1.0":
        raise ValueError("unsupported github source registry schema")
    if not isinstance(data.get("sources"), list):
        raise ValueError("github source registry has no sources list")
    return data


def validate_source(source: dict, repo_data: dict) -> None:
    required = {
        "repository", "ref", "rights_decision", "score_license", "license_path",
        "rights_evidence_path", "include_prefixes", "extensions",
        "redistribution_permitted", "derivatives_permitted", "commercial_use",
        "attribution_required",
    }
    missing = sorted(required - source.keys())
    if missing:
        raise ValueError(f"source_registry_missing:{','.join(missing)}")
    if source["rights_decision"] != "approved":
        raise ValueError("source_rights_not_approved")
    if source["redistribution_permitted"] is not True:
        raise ValueError("source_redistribution_not_permitted")
    if source["derivatives_permitted"] is not True:
        raise ValueError("source_derivatives_not_permitted")
    if source["commercial_use"] not in {"permitted", "prohibited"}:
        raise ValueError("invalid_commercial_use_policy")
    if not isinstance(source["attribution_required"], bool):
        raise ValueError("invalid_attribution_policy")
    if repo_data.get("private") or repo_data.get("archived") or repo_data.get("disabled"):
        raise ValueError("repository_not_active_public_source")
    expected = set(source.get("expected_github_spdx", []))
    observed = (repo_data.get("license") or {}).get("spdx_id")
    if expected and observed not in expected:
        raise ValueError(f"github_license_mismatch:{observed}")


def inventory_source(client: GitHubClient, source: dict) -> tuple[dict, list[dict]]:
    repository = source["repository"]
    encoded_ref = quote(source["ref"], safe="")
    repo_data = client.get_json(f"/repos/{repository}")
    validate_source(source, repo_data)
    commit = client.get_json(f"/repos/{repository}/commits/{encoded_ref}")
    commit_sha = commit["sha"]
    tree = client.get_json(
        f"/repos/{repository}/git/trees/{commit_sha}",
        params={"recursive": "1"},
    )
    if tree.get("truncated"):
        raise GitHubAPIError("recursive_tree_truncated")

    blob_paths = {
        item.get("path", "")
        for item in tree.get("tree", [])
        if item.get("type") == "blob"
    }
    for evidence_field in ("license_path", "rights_evidence_path"):
        if source[evidence_field] not in blob_paths:
            raise ValueError(f"source_evidence_not_found:{evidence_field}")

    prefixes = tuple(source["include_prefixes"])
    extensions = tuple(extension.lower() for extension in source["extensions"])
    excluded = tuple(source.get("exclude_prefixes", []))
    entries = []
    license_url = (
        f"{repo_data['html_url']}/blob/{commit_sha}/"
        f"{quote(source['license_path'], safe='/')}"
    )
    evidence_url = (
        f"{repo_data['html_url']}/blob/{commit_sha}/"
        f"{quote(source['rights_evidence_path'], safe='/')}"
        f"{source.get('rights_evidence_fragment', '')}"
    )
    for item in tree.get("tree", []):
        path = item.get("path", "")
        if item.get("type") != "blob":
            continue
        if prefixes and not path.startswith(prefixes):
            continue
        if excluded and path.startswith(excluded):
            continue
        if not path.lower().endswith(extensions):
            continue
        if int(item.get("size") or 0) > MAX_DOWNLOAD_BYTES:
            continue
        title, composer = infer_path_metadata(path)
        raw_url = f"{RAW_ROOT}/{repository}/{commit_sha}/{quote(path, safe='/')}"
        entries.append({
            "source_id": source_id_for(source),
            "repository": repository,
            "repository_url": repo_data["html_url"],
            "commit_sha": commit_sha,
            "commit_url": f"{repo_data['html_url']}/commit/{commit_sha}",
            "source_path": path,
            "blob_sha": item.get("sha"),
            "source_size": item.get("size"),
            "raw_url": raw_url,
            "source_page_url": f"{repo_data['html_url']}/blob/{commit_sha}/{quote(path, safe='/')}",
            "path_title": title,
            "path_composer": composer,
            "github_license_spdx": (repo_data.get("license") or {}).get("spdx_id"),
            "score_license": source["score_license"],
            "rights_decision": source["rights_decision"],
            "redistribution_permitted": source["redistribution_permitted"],
            "derivatives_permitted": source["derivatives_permitted"],
            "commercial_use": source["commercial_use"],
            "attribution_required": source["attribution_required"],
            "license_url": license_url,
            "rights_evidence_url": evidence_url,
            "license_assessment_note": source.get("license_assessment_note", ""),
            "attribution": source.get("attribution", ""),
        })
    entries.sort(key=lambda row: row["source_path"])
    return {
        "source_id": source_id_for(source),
        "repository": repository,
        "ref": source["ref"],
        "commit_sha": commit_sha,
        "tree_sha": tree.get("sha"),
        "commit_date": ((commit.get("commit") or {}).get("committer") or {}).get("date"),
        "tree_truncated": False,
        "github_license_spdx": (repo_data.get("license") or {}).get("spdx_id"),
        "score_license": source["score_license"],
        "commercial_use": source["commercial_use"],
        "attribution_required": source["attribution_required"],
        "license_url": license_url,
        "rights_evidence_url": evidence_url,
        "license_assessment_note": source.get("license_assessment_note", ""),
        "inventory_count": len(entries),
        "inventory_bytes": sum(int(entry.get("source_size") or 0) for entry in entries),
        "unique_blob_count": len({entry.get("blob_sha") for entry in entries}),
    }, entries


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.write_text(body, encoding="utf-8")


def mark_inventory_duplicates(rows: list[dict]) -> None:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        blob_sha = row.get("blob_sha")
        if not blob_sha:
            row["inventory_status"] = "quarantined_missing_blob_sha"
            continue
        groups.setdefault(blob_sha, []).append(row)
    for group in groups.values():
        if len(group) == 1:
            group[0]["inventory_status"] = "eligible"
            continue
        paths = sorted(row["source_path"] for row in group)
        for row in group:
            row["inventory_status"] = "quarantined_exact_duplicate_blob"
            row["duplicate_blob_paths"] = paths


def report_path(path: Path, output_dir: Path) -> str:
    try:
        return str(path.resolve().relative_to(output_dir.resolve()))
    except ValueError:
        return str(path)


def stage_entry(
    client: GitHubClient,
    entry: dict,
    staging_root: Path,
    thresholds: QualityThresholds,
    overwrite: bool,
) -> dict:
    relative = (
        safe_relative_path(entry["source_id"])
        / safe_relative_path(entry["commit_sha"])
        / safe_relative_path(entry["source_path"])
    )
    target = staging_root / relative
    retrieved_at = utc_now()
    blob_verified = False
    try:
        if target.exists() and not overwrite:
            payload = target.read_bytes()
            retrieval = "reused"
        else:
            payload = client.get_bytes(entry["raw_url"])
            retrieval = "downloaded"

        expected_blob_sha = entry.get("blob_sha")
        if not expected_blob_sha:
            raise MusicXMLAuditError("missing_git_blob_sha")
        observed_blob_sha = git_blob_sha(payload, expected_blob_sha)
        if observed_blob_sha != expected_blob_sha.lower():
            raise MusicXMLAuditError("git_blob_sha_mismatch")
        blob_verified = True

        if retrieval == "downloaded":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        audit = audit_musicxml_payload(
            payload,
            target.name,
            fallback_title=entry["path_title"],
            fallback_composer=entry["path_composer"],
            thresholds=thresholds,
        )
        status = "candidate" if audit["quality_status"] == "accepted" else "rejected"
        errors: list[str] = []
    except (GitHubAPIError, MusicXMLAuditError, OSError, ValueError) as exc:
        payload = b""
        retrieval = "failed"
        audit = {"quality_status": "rejected", "quality_reasons": [str(exc)]}
        status = "error"
        errors = [str(exc)]

    identity = f"github:{entry['repository']}@{entry['commit_sha']}:{entry['source_path']}"
    return {
        **entry,
        "discovery_id": f"gh-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}",
        "retrieved_at": retrieved_at,
        "retrieval": retrieval,
        "staged_path": str(Path("staging") / relative),
        "git_blob_verified": blob_verified,
        "source_sha256": sha256_bytes(payload) if payload else None,
        "status": status,
        "errors": errors,
        **audit,
    }


def deduplicate_candidates(rows: list[dict]) -> None:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("status") != "candidate":
            continue
        digest = row.get("musical_content_sha256")
        groups.setdefault(digest or "", []).append(row)
    for group in groups.values():
        if len(group) == 1:
            continue
        identities = sorted(row["discovery_id"] for row in group)
        paths = sorted(row["source_path"] for row in group)
        for row in group:
            row["status"] = "rejected"
            row["quality_status"] = "rejected"
            row.setdefault("quality_reasons", []).append("duplicate_musical_content")
            row["duplicate_content_group"] = identities
            row["duplicate_content_paths"] = paths


def write_markdown(report: dict, path: Path) -> None:
    lines = [
        "# MusicXML Discovery report",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Mode: **{report['mode']}**",
        f"- Rights-approved repositories inventoried: **{report['sources_succeeded']}**",
        f"- Eligible files discovered: **{report['inventory_count']}**",
        f"- Unique Git blobs discovered: **{report['unique_blob_count']}**",
        f"- Non-conflicting files eligible for audit: **{report['auditable_inventory_count']}**",
        f"- Exact-duplicate identity groups quarantined: **{report['duplicate_blob_group_count']}** "
        f"({report['duplicate_blob_path_count']} paths)",
        f"- Eligible compressed source size: **{report['inventory_mib']} MiB**",
        f"- Files audited: **{report['audited_count']}**",
        f"- Tonal, overlapping, 2+ voice candidates: **{report['candidate_count']}**",
        f"- Acceptance rate: **{report['acceptance_rate_percent']}%**",
        f"- Rejected by quality gates: **{report['rejected_count']}**",
        f"- Errors: **{report['error_count']}**",
        "",
        "## Source snapshots",
        "",
    ]
    for source in report["source_snapshots"]:
        lines.append(
            f"- `{source['repository']}@{source['commit_sha']}` — "
            f"{source['inventory_count']} files, `{source['score_license']}`"
        )
    if report["license_distribution"]:
        lines += ["", "## Discovered score licences", ""]
        for license_name, count in report["license_distribution"].items():
            lines.append(f"- `{license_name}`: {count} files")
    if report["commercial_use_distribution"]:
        lines += ["", "## Commercial-use conditions", ""]
        for condition, count in report["commercial_use_distribution"].items():
            lines.append(f"- `{condition}`: {count} files")
    if report["source_errors"]:
        lines += ["", "## Source errors", ""]
        for error in report["source_errors"]:
            lines.append(f"- `{error['repository']}`: `{error['error']}`")
    if report["rejection_reasons"]:
        lines += ["", "## Rejection reasons", ""]
        for reason, count in report["rejection_reasons"].items():
            lines.append(f"- `{reason}`: {count}")
    if report["accepted_voice_count_distribution"]:
        lines += ["", "## Accepted encoded voice streams", ""]
        for voice_count, count in report["accepted_voice_count_distribution"].items():
            lines.append(f"- `{voice_count}` voices: {count}")
    lines += [
        "",
        "Candidate status means eligible for downstream human review; it does not mean released.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry",
        type=Path,
        required=True,
        help="JSON registry of human-approved repositories and rights evidence",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Run manifests, reports, and isolated staging directory",
    )
    parser.add_argument("--source", action="append", help="Only process the named source id")
    parser.add_argument("--limit", type=int, default=0, help="Maximum downloads; 0 means all")
    parser.add_argument("--apply", action="store_true", help="Download and audit; default is inventory only")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--download-delay",
        type=float,
        default=0.15,
        help="Seconds between new raw-file downloads",
    )
    parser.add_argument("--min-notes", type=int, default=32)
    parser.add_argument("--min-measures", type=int, default=4)
    parser.add_argument("--min-voices", type=int, default=2)
    parser.add_argument("--min-overlap-quarters", type=float, default=1.0)
    parser.add_argument("--min-pitch-classes", type=int, default=5)
    parser.add_argument("--min-tonal-correlation", type=float, default=0.45)
    parser.add_argument("--min-diatonic-ratio", type=float, default=0.70)
    return parser


def main(arguments: list[str] | None = None) -> int:
    args = build_parser().parse_args(arguments)
    if args.limit < 0:
        raise SystemExit("--limit must be non-negative")
    if args.min_voices < 2:
        raise SystemExit("--min-voices must be at least 2")
    if args.min_notes < 1 or args.min_measures < 1 or args.min_pitch_classes < 1:
        raise SystemExit("count thresholds must be positive")
    if args.min_overlap_quarters <= 0:
        raise SystemExit("--min-overlap-quarters must be positive")
    if not -1.0 <= args.min_tonal_correlation <= 1.0:
        raise SystemExit("--min-tonal-correlation must be between -1 and 1")
    if not 0.0 <= args.min_diatonic_ratio <= 1.0:
        raise SystemExit("--min-diatonic-ratio must be between 0 and 1")
    registry = load_config(args.registry)
    selected = set(args.source or [])
    sources = [
        source for source in registry["sources"]
        if not selected or source_id_for(source) in selected
    ]
    if not sources:
        raise SystemExit("no registry source matched --source")

    output_dir = args.output_dir
    staging_dir = output_dir / "staging"
    inventory_path = output_dir / "inventory.jsonl"
    audit_path = output_dir / "audit.jsonl"
    accepted_path = output_dir / "accepted.jsonl"
    report_path_json = output_dir / "report.json"
    report_path_markdown = output_dir / "report.md"
    client = GitHubClient()
    all_inventory: list[dict] = []
    snapshots: list[dict] = []
    source_errors: list[dict] = []

    for source in sources:
        try:
            snapshot, entries = inventory_source(client, source)
            snapshots.append(snapshot)
            all_inventory.extend(entries)
            print(f"Inventoried {snapshot['repository']}@{snapshot['commit_sha']}: {len(entries)} files")
        except (GitHubAPIError, HTTPError, URLError, KeyError, ValueError) as exc:
            source_errors.append({"repository": source.get("repository", "unknown"), "error": str(exc)})
            print(f"Source failed: {source.get('repository', 'unknown')}: {exc}")

    all_inventory.sort(key=lambda row: (row["repository"], row["source_path"]))
    mark_inventory_duplicates(all_inventory)
    write_jsonl(inventory_path, all_inventory)

    candidate_rows: list[dict] = []
    if args.apply:
        unique_inventory = [row for row in all_inventory if row.get("inventory_status") == "eligible"]
        selected_inventory = unique_inventory[: args.limit] if args.limit else unique_inventory
        thresholds = QualityThresholds(
            min_pitched_notes=args.min_notes,
            min_measures=args.min_measures,
            min_voices=args.min_voices,
            min_overlap_quarters=args.min_overlap_quarters,
            min_pitch_classes=args.min_pitch_classes,
            min_tonal_correlation=args.min_tonal_correlation,
            min_diatonic_ratio=args.min_diatonic_ratio,
        )
        for index, entry in enumerate(selected_inventory, 1):
            row = stage_entry(client, entry, staging_dir, thresholds, args.overwrite)
            candidate_rows.append(row)
            print(f"[{index}/{len(selected_inventory)}] {entry['source_path']}: {row['status']}")
            if row.get("retrieval") == "downloaded" and args.download_delay > 0:
                time.sleep(args.download_delay)
        deduplicate_candidates(candidate_rows)
        write_jsonl(audit_path, candidate_rows)
        write_jsonl(accepted_path, [row for row in candidate_rows if row.get("status") == "candidate"])

    accepted_rows = [row for row in candidate_rows if row.get("status") == "candidate"]
    rejection_reasons = Counter(
        reason
        for row in candidate_rows
        if row.get("status") == "rejected"
        for reason in row.get("quality_reasons", [])
    )
    voice_distribution = Counter(row.get("voice_count") for row in accepted_rows)
    license_distribution = Counter(row.get("score_license") for row in all_inventory)
    commercial_distribution = Counter(row.get("commercial_use") for row in all_inventory)

    report = {
        "schema_version": "1.0",
        "generated_at": utc_now(),
        "mode": "download-and-audit" if args.apply else "inventory-only",
        "authenticated": bool(client.token),
        "sources_requested": len(sources),
        "sources_succeeded": len(snapshots),
        "source_snapshots": snapshots,
        "source_errors": source_errors,
        "inventory_count": len(all_inventory),
        "inventory_bytes": sum(int(row.get("source_size") or 0) for row in all_inventory),
        "inventory_mib": round(
            sum(int(row.get("source_size") or 0) for row in all_inventory) / 1024 / 1024,
            2,
        ),
        "unique_blob_count": len({row.get("blob_sha") for row in all_inventory}),
        "auditable_inventory_count": sum(
            row.get("inventory_status") == "eligible" for row in all_inventory
        ),
        "duplicate_blob_group_count": len({
            row.get("blob_sha") for row in all_inventory
            if row.get("inventory_status") == "quarantined_exact_duplicate_blob"
        }),
        "duplicate_blob_path_count": sum(
            row.get("inventory_status") == "quarantined_exact_duplicate_blob"
            for row in all_inventory
        ),
        "audited_count": len(candidate_rows),
        "candidate_count": len(accepted_rows),
        "candidate_source_bytes": sum(int(row.get("source_size") or 0) for row in accepted_rows),
        "acceptance_rate_percent": round(
            100 * len(accepted_rows) / len(candidate_rows), 2
        ) if candidate_rows else 0.0,
        "rejected_count": sum(row.get("status") == "rejected" for row in candidate_rows),
        "rejection_reasons": dict(rejection_reasons.most_common()),
        "license_distribution": dict(sorted(license_distribution.items())),
        "commercial_use_distribution": dict(sorted(commercial_distribution.items())),
        "accepted_voice_count_distribution": {
            str(key): value for key, value in sorted(voice_distribution.items())
        },
        "error_count": len(source_errors) + sum(row.get("status") == "error" for row in candidate_rows),
        "thresholds": {
            "minimum_sounding_voices": args.min_voices,
            "minimum_pitched_notes": args.min_notes,
            "minimum_measures": args.min_measures,
            "minimum_overlap_quarters": args.min_overlap_quarters,
            "minimum_pitch_classes": args.min_pitch_classes,
            "minimum_tonal_correlation": args.min_tonal_correlation,
            "minimum_diatonic_ratio": args.min_diatonic_ratio,
        },
        "inventory_manifest": report_path(inventory_path, output_dir),
        "audit_manifest": report_path(audit_path, output_dir) if args.apply else None,
        "accepted_manifest": report_path(accepted_path, output_dir) if args.apply else None,
    }
    report_path_json.parent.mkdir(parents=True, exist_ok=True)
    report_path_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_markdown(report, report_path_markdown)
    print(f"Inventory: {inventory_path}")
    print(f"Report: {report_path_json}")
    return 1 if source_errors or not snapshots else 0


if __name__ == "__main__":
    raise SystemExit(main())
