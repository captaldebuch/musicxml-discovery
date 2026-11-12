"""Audit an already acquired local MusicXML collection without copying it."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .quality import MusicXMLAuditError, QualityThresholds, audit_musicxml_payload


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.write_text(body, encoding="utf-8")


def discover_files(root: Path, extensions: tuple[str, ...]) -> list[Path]:
    if not root.is_dir():
        raise ValueError(f"input_directory_not_found:{root}")
    return sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and not path.is_symlink() and path.suffix.lower() in extensions
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def inventory_files(root: Path, paths: list[Path], metadata: dict[str, object]) -> list[dict]:
    rows = []
    for path in paths:
        payload = path.read_bytes()
        rows.append(
            {
                **metadata,
                "source_path": path.relative_to(root).as_posix(),
                "source_size": len(payload),
                "source_sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return rows


def mark_exact_duplicates(rows: list[dict]) -> None:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["source_sha256"], []).append(row)
    for group in groups.values():
        if len(group) == 1:
            group[0]["inventory_status"] = "eligible"
            continue
        paths = sorted(row["source_path"] for row in group)
        for row in group:
            row["inventory_status"] = "quarantined_exact_duplicate_payload"
            row["duplicate_payload_paths"] = paths


def deduplicate_musical_content(rows: list[dict]) -> None:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("quality_status") != "accepted":
            continue
        digest = row.get("musical_content_sha256")
        if digest:
            groups.setdefault(digest, []).append(row)
    for group in groups.values():
        if len(group) == 1:
            continue
        paths = sorted(row["source_path"] for row in group)
        for row in group:
            row["quality_status"] = "rejected"
            row["status"] = "rejected"
            row.setdefault("quality_reasons", []).append("duplicate_musical_content")
            row["duplicate_content_paths"] = paths


def audit_rows(
    root: Path,
    inventory: list[dict],
    thresholds: QualityThresholds,
    limit: int,
) -> list[dict]:
    eligible = [row for row in inventory if row["inventory_status"] == "eligible"]
    selected = eligible[:limit] if limit else eligible
    audited = []
    for index, row in enumerate(selected, 1):
        path = root / row["source_path"]
        try:
            audit = audit_musicxml_payload(path.read_bytes(), path.name, thresholds=thresholds)
            if audit["quality_status"] == "accepted":
                status = (
                    "candidate"
                    if row["rights_decision"] == "approved"
                    else "research_candidate"
                )
            else:
                status = "rejected"
            errors: list[str] = []
        except (MusicXMLAuditError, OSError, ValueError) as exc:
            audit = {"quality_status": "rejected", "quality_reasons": [str(exc)]}
            status = "error"
            errors = [str(exc)]
        audited.append({**row, "status": status, "errors": errors, **audit})
        if index == 1 or index % 100 == 0 or index == len(selected):
            print(f"[{index}/{len(selected)}] {row['source_path']}: {status}")
    deduplicate_musical_content(audited)
    return audited


def write_markdown(report: dict, path: Path) -> None:
    candidate_label = (
        "Public-release candidates"
        if report["rights_decision"] == "approved"
        else "Research-only profile-compatible candidates"
    )
    lines = [
        "# Local MusicXML audit report",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Source: `{report['source_id']}`",
        f"- Rights decision: **{report['rights_decision']}**",
        f"- Files discovered: **{report['inventory_count']}**",
        f"- Exact duplicate paths quarantined: **{report['duplicate_path_count']}**",
        f"- Files audited: **{report['audited_count']}**",
        f"- {candidate_label}: **{report['candidate_count']}**",
        f"- Rejected: **{report['rejected_count']}**",
        f"- Errors: **{report['error_count']}**",
        "",
        "Research-candidate status is a musical-quality result, not permission to redistribute.",
    ]
    if report["rejection_reasons"]:
        lines += ["", "## Rejection reasons", ""]
        for reason, count in report["rejection_reasons"].items():
            lines.append(f"- `{reason}`: {count}")
    if report["error_details"]:
        lines += ["", "## Source-file errors", ""]
        for error in report["error_details"]:
            reasons = ", ".join(f"`{reason}`" for reason in error["errors"])
            lines.append(f"- `{error['source_path']}`: {reasons}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--source-url", default="")
    parser.add_argument("--commit-sha", default="")
    parser.add_argument("--score-license", default="unknown")
    parser.add_argument(
        "--rights-decision",
        choices=("research-only", "approved"),
        default="research-only",
    )
    parser.add_argument(
        "--commercial-use",
        choices=("permitted", "prohibited", "unclear"),
        default="unclear",
    )
    parser.add_argument("--redistribution-permitted", action="store_true")
    parser.add_argument("--derivatives-permitted", action="store_true")
    parser.add_argument("--attribution-required", action="store_true")
    parser.add_argument("--extensions", default=".xml,.musicxml,.mxl")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--min-notes", type=int, default=32)
    parser.add_argument("--min-measures", type=int, default=4)
    parser.add_argument("--min-voices", type=int, default=2)
    parser.add_argument("--min-overlap-quarters", type=float, default=1.0)
    parser.add_argument("--min-pitch-classes", type=int, default=5)
    parser.add_argument("--min-tonal-correlation", type=float, default=0.45)
    parser.add_argument("--min-diatonic-ratio", type=float, default=0.70)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.limit < 0:
        raise SystemExit("--limit must be non-negative")
    if args.rights_decision == "approved" and not args.redistribution_permitted:
        raise SystemExit("approved sources must permit redistribution")
    if args.rights_decision == "approved" and not args.derivatives_permitted:
        raise SystemExit("approved sources must permit derivatives")
    if args.rights_decision == "approved" and args.commercial_use == "unclear":
        raise SystemExit("approved sources must state the commercial-use condition")
    if args.min_voices < 2:
        raise SystemExit("--min-voices must be at least 2")

    extensions = tuple(
        item if item.startswith(".") else f".{item}"
        for item in (part.strip().lower() for part in args.extensions.split(","))
        if item
    )
    metadata: dict[str, object] = {
        "source_id": args.source_id,
        "source_url": args.source_url,
        "commit_sha": args.commit_sha,
        "score_license": args.score_license,
        "rights_decision": args.rights_decision,
        "commercial_use": args.commercial_use,
        "redistribution_permitted": args.redistribution_permitted,
        "derivatives_permitted": args.derivatives_permitted,
        "attribution_required": args.attribution_required,
        "rights_scope": (
            "public-open-nc"
            if args.rights_decision == "approved"
            else "transformative-research-only"
        ),
        "public_release_eligible": args.rights_decision == "approved",
    }
    paths = discover_files(args.input_dir, extensions)
    inventory = inventory_files(args.input_dir, paths, metadata)
    mark_exact_duplicates(inventory)
    write_jsonl(args.output_dir / "inventory.jsonl", inventory)

    thresholds = QualityThresholds(
        min_pitched_notes=args.min_notes,
        min_measures=args.min_measures,
        min_voices=args.min_voices,
        min_overlap_quarters=args.min_overlap_quarters,
        min_pitch_classes=args.min_pitch_classes,
        min_tonal_correlation=args.min_tonal_correlation,
        min_diatonic_ratio=args.min_diatonic_ratio,
    )
    audited = audit_rows(args.input_dir, inventory, thresholds, args.limit)
    candidates = [
        row for row in audited
        if row.get("status") in {"candidate", "research_candidate"}
    ]
    write_jsonl(args.output_dir / "audit.jsonl", audited)
    write_jsonl(args.output_dir / "candidates.jsonl", candidates)

    reasons = Counter(
        reason
        for row in audited
        if row.get("status") == "rejected"
        for reason in row.get("quality_reasons", [])
    )
    report = {
        "schema_version": "1.0",
        "generated_at": utc_now(),
        **metadata,
        "input_directory": str(args.input_dir),
        "extensions": list(extensions),
        "inventory_count": len(inventory),
        "inventory_bytes": sum(row["source_size"] for row in inventory),
        "unique_payload_count": len({row["source_sha256"] for row in inventory}),
        "duplicate_group_count": len(
            {
                row["source_sha256"]
                for row in inventory
                if row["inventory_status"] == "quarantined_exact_duplicate_payload"
            }
        ),
        "duplicate_path_count": sum(
            row["inventory_status"] == "quarantined_exact_duplicate_payload"
            for row in inventory
        ),
        "auditable_count": sum(row["inventory_status"] == "eligible" for row in inventory),
        "audited_count": len(audited),
        "candidate_count": len(candidates),
        "rejected_count": sum(row.get("status") == "rejected" for row in audited),
        "error_count": sum(row.get("status") == "error" for row in audited),
        "error_details": [
            {"source_path": row["source_path"], "errors": row["errors"]}
            for row in audited
            if row.get("status") == "error"
        ],
        "rejection_reasons": dict(reasons.most_common()),
        "thresholds": thresholds.__dict__,
    }
    (args.output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_markdown(report, args.output_dir / "report.md")
    print(f"Inventory: {args.output_dir / 'inventory.jsonl'}")
    print(f"Report: {args.output_dir / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
