from __future__ import annotations

import unittest

from musicxml_discovery.local_cli import (
    deduplicate_musical_content,
    mark_exact_duplicates,
)


class LocalAuditTests(unittest.TestCase):
    def test_exact_duplicate_payloads_are_all_quarantined(self) -> None:
        rows = [
            {"source_path": "a.musicxml", "source_sha256": "same"},
            {"source_path": "b.musicxml", "source_sha256": "same"},
            {"source_path": "c.musicxml", "source_sha256": "unique"},
        ]

        mark_exact_duplicates(rows)

        self.assertEqual(rows[0]["inventory_status"], "quarantined_exact_duplicate_payload")
        self.assertEqual(rows[1]["inventory_status"], "quarantined_exact_duplicate_payload")
        self.assertEqual(rows[0]["duplicate_payload_paths"], ["a.musicxml", "b.musicxml"])
        self.assertEqual(rows[2]["inventory_status"], "eligible")

    def test_musical_content_conflicts_are_all_rejected(self) -> None:
        rows = [
            {
                "source_path": "edition-a.musicxml",
                "quality_status": "accepted",
                "status": "research_candidate",
                "quality_reasons": [],
                "musical_content_sha256": "same-music",
            },
            {
                "source_path": "edition-b.mxl",
                "quality_status": "accepted",
                "status": "research_candidate",
                "quality_reasons": [],
                "musical_content_sha256": "same-music",
            },
        ]

        deduplicate_musical_content(rows)

        for row in rows:
            self.assertEqual(row["status"], "rejected")
            self.assertIn("duplicate_musical_content", row["quality_reasons"])
            self.assertEqual(
                row["duplicate_content_paths"],
                ["edition-a.musicxml", "edition-b.mxl"],
            )


if __name__ == "__main__":
    unittest.main()
