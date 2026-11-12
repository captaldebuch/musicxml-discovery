from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from musicxml_discovery.cli import (
    deduplicate_candidates,
    git_blob_sha,
    inventory_source,
    mark_inventory_duplicates,
    safe_relative_path,
    stage_entry,
    validate_source,
)
from musicxml_discovery.quality import QualityThresholds


SOURCE = {
    "id": "fixture",
    "repository": "example/scores",
    "ref": "v1",
    "rights_decision": "approved",
    "score_license": "CC0-1.0",
    "redistribution_permitted": True,
    "derivatives_permitted": True,
    "commercial_use": "permitted",
    "attribution_required": False,
    "expected_github_spdx": ["CC0-1.0"],
    "license_path": "LICENSE",
    "rights_evidence_path": "README.md",
    "include_prefixes": ["scores/"],
    "exclude_prefixes": ["scores/drafts/"],
    "extensions": [".musicxml", ".mxl"],
    "attribution": "Fixture corpus",
}


class FakeGitHubClient:
    def get_json(self, endpoint: str, params: dict | None = None):
        if endpoint == "/repos/example/scores":
            return {
                "html_url": "https://github.com/example/scores",
                "private": False,
                "archived": False,
                "disabled": False,
                "license": {"spdx_id": "CC0-1.0"},
            }
        if endpoint == "/repos/example/scores/commits/v1":
            return {
                "sha": "abc123",
                "commit": {"committer": {"date": "2026-01-01T00:00:00Z"}},
            }
        if endpoint == "/repos/example/scores/git/trees/abc123":
            self.params = params
            return {
                "sha": "tree123",
                "truncated": False,
                "tree": [
                    {"path": "LICENSE", "type": "blob", "sha": "l", "size": 10},
                    {"path": "README.md", "type": "blob", "sha": "r", "size": 10},
                    {"path": "scores/a.musicxml", "type": "blob", "sha": "a", "size": 100},
                    {"path": "scores/b.MXL", "type": "blob", "sha": "b", "size": 120},
                    {"path": "scores/drafts/c.mxl", "type": "blob", "sha": "c", "size": 90},
                    {"path": "scores/notes.txt", "type": "blob", "sha": "n", "size": 40},
                ],
            }
        raise AssertionError(f"unexpected endpoint: {endpoint}")


class RegistryAndPipelineTests(unittest.TestCase):
    def test_git_blob_hash_matches_git_object_format(self) -> None:
        self.assertEqual(
            git_blob_sha(b"hello\n", "ce013625030ba8dba906f756967f9e9ca394464a"),
            "ce013625030ba8dba906f756967f9e9ca394464a",
        )

    def test_source_requires_approved_rights_and_matching_license(self) -> None:
        repo = {
            "private": False,
            "archived": False,
            "disabled": False,
            "license": {"spdx_id": "CC0-1.0"},
        }
        validate_source(SOURCE, repo)
        rejected = {**SOURCE, "rights_decision": "review"}
        with self.assertRaisesRegex(ValueError, "source_rights_not_approved"):
            validate_source(rejected, repo)

    def test_noncommercial_source_is_allowed_but_no_derivatives_is_not(self) -> None:
        repo = {
            "private": False,
            "archived": False,
            "disabled": False,
            "license": {"spdx_id": "CC0-1.0"},
        }
        noncommercial = {**SOURCE, "commercial_use": "prohibited"}
        validate_source(noncommercial, repo)
        no_derivatives = {**noncommercial, "derivatives_permitted": False}
        with self.assertRaisesRegex(ValueError, "source_derivatives_not_permitted"):
            validate_source(no_derivatives, repo)

    def test_inventory_is_commit_pinned_and_bounded(self) -> None:
        client = FakeGitHubClient()
        snapshot, rows = inventory_source(client, SOURCE)
        self.assertEqual(snapshot["commit_sha"], "abc123")
        self.assertEqual([row["source_path"] for row in rows], [
            "scores/a.musicxml",
            "scores/b.MXL",
        ])
        self.assertTrue(all("/abc123/" in row["raw_url"] for row in rows))
        self.assertEqual(client.params, {"recursive": "1"})

    def test_inventory_fails_when_rights_evidence_is_absent(self) -> None:
        client = FakeGitHubClient()
        missing = {**SOURCE, "rights_evidence_path": "RIGHTS.md"}
        with self.assertRaisesRegex(ValueError, "source_evidence_not_found"):
            inventory_source(client, missing)

    def test_all_exact_git_blob_paths_are_quarantined(self) -> None:
        rows = [
            {"blob_sha": "same", "source_path": "a.mxl"},
            {"blob_sha": "other", "source_path": "b.mxl"},
            {"blob_sha": "same", "source_path": "c.mxl"},
        ]
        mark_inventory_duplicates(rows)
        self.assertEqual(rows[0]["inventory_status"], "quarantined_exact_duplicate_blob")
        self.assertEqual(rows[2]["inventory_status"], "quarantined_exact_duplicate_blob")
        self.assertEqual(rows[0]["duplicate_blob_paths"], ["a.mxl", "c.mxl"])
        self.assertEqual(rows[1]["inventory_status"], "eligible")

    def test_all_normalized_content_paths_are_quarantined(self) -> None:
        rows = [
            {
                "discovery_id": "one",
                "source_path": "first.mxl",
                "status": "candidate",
                "quality_status": "accepted",
                "quality_reasons": [],
                "musical_content_sha256": "same",
            },
            {
                "discovery_id": "two",
                "source_path": "second.mxl",
                "status": "candidate",
                "quality_status": "accepted",
                "quality_reasons": [],
                "musical_content_sha256": "same",
            },
        ]
        deduplicate_candidates(rows)
        self.assertTrue(all(row["status"] == "rejected" for row in rows))
        self.assertEqual(rows[0]["duplicate_content_group"], ["one", "two"])
        self.assertEqual(rows[1]["duplicate_content_paths"], ["first.mxl", "second.mxl"])

    def test_staging_paths_reject_traversal(self) -> None:
        self.assertEqual(safe_relative_path("scores/a.mxl"), Path("scores/a.mxl"))
        for value in ("../secret", "scores/../../secret", "/absolute"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "unsafe_source_path"):
                    safe_relative_path(value)

    def test_stage_rejects_payload_that_does_not_match_git_blob(self) -> None:
        class DownloadClient:
            def get_bytes(self, url: str) -> bytes:
                return b"not the expected blob"

        entry = {
            "source_id": "fixture",
            "commit_sha": "abc123",
            "source_path": "scores/a.musicxml",
            "raw_url": "https://example.invalid/a.musicxml",
            "blob_sha": "ce013625030ba8dba906f756967f9e9ca394464a",
            "repository": "example/scores",
            "path_title": "A",
            "path_composer": "Composer",
        }
        with tempfile.TemporaryDirectory() as directory:
            row = stage_entry(
                DownloadClient(),
                entry,
                Path(directory) / "staging",
                QualityThresholds(),
                overwrite=False,
            )
            self.assertEqual(row["status"], "error")
            self.assertIn("git_blob_sha_mismatch", row["errors"])
            self.assertFalse(row["git_blob_verified"])
            self.assertFalse(any(Path(directory).rglob("*.musicxml")))


if __name__ == "__main__":
    unittest.main()
