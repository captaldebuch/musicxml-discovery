from __future__ import annotations

import io
import unittest
import zipfile
from fractions import Fraction

from musicxml_discovery.quality import (
    MusicXMLAuditError,
    NoteEvent,
    QualityThresholds,
    audit_musicxml_payload,
    musicxml_from_payload,
    polyphony_evidence,
)


def make_score(
    *,
    part_count: int = 2,
    chromatic: bool = False,
    delay_second_part: bool = False,
) -> bytes:
    tonal_steps = [
        ("C", 0), ("D", 0), ("E", 0), ("F", 0),
        ("G", 0), ("A", 0), ("B", 0), ("C", 0),
    ]
    chromatic_steps = [
        ("C", 0), ("C", 1), ("D", 0), ("D", 1),
        ("E", 0), ("F", 0), ("F", 1), ("G", 0),
        ("G", 1), ("A", 0), ("A", 1), ("B", 0),
    ]
    palette = chromatic_steps if chromatic else tonal_steps
    parts = []
    for part_index in range(part_count):
        measures = []
        for measure_index in range(8):
            items = []
            if delay_second_part and part_index == 1 and measure_index == 0:
                items.append("<forward><duration>32</duration></forward>")
            for note_index in range(4):
                step, alter = palette[(measure_index * 4 + note_index) % len(palette)]
                alter_xml = f"<alter>{alter}</alter>" if alter else ""
                items.append(
                    "<note><pitch>"
                    f"<step>{step}</step>{alter_xml}<octave>{4 - part_index}</octave>"
                    "</pitch><duration>1</duration>"
                    f"<voice>{part_index + 1}</voice></note>"
                )
            attributes = (
                "<attributes><divisions>1</divisions><key><fifths>0</fifths>"
                "<mode>major</mode></key><time><beats>4</beats><beat-type>4</beat-type>"
                "</time></attributes>"
                if measure_index == 0
                else ""
            )
            measures.append(
                f'<measure number="{measure_index + 1}">{attributes}{"".join(items)}</measure>'
            )
        parts.append(f'<part id="P{part_index + 1}">{"".join(measures)}</part>')
    part_list = "".join(
        f'<score-part id="P{index + 1}"><part-name>Voice {index + 1}</part-name></score-part>'
        for index in range(part_count)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<score-partwise version="4.0"><work><work-title>Fixture</work-title></work>'
        '<identification><creator type="composer">Test Composer</creator></identification>'
        f"<part-list>{part_list}</part-list>{''.join(parts)}</score-partwise>"
    ).encode("utf-8")


def make_mxl(xml: bytes, root_path: str = "score.musicxml") -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container><rootfiles>'
            f'<rootfile full-path="{root_path}" media-type="application/vnd.recordare.musicxml+xml"/>'
            "</rootfiles></container>",
        )
        archive.writestr(root_path, xml)
    return payload.getvalue()


class QualityAuditTests(unittest.TestCase):
    def test_accepts_tonal_overlapping_two_voice_mxl(self) -> None:
        result = audit_musicxml_payload(make_mxl(make_score()), "fixture.mxl")
        self.assertEqual(result["quality_status"], "accepted")
        self.assertEqual(result["voice_count"], 2)
        self.assertGreaterEqual(result["max_pair_overlap_quarters"], 32)
        self.assertEqual(result["estimated_key"], "C")
        self.assertEqual(result["diatonic_ratio"], 1.0)

    def test_rejects_single_voice_score(self) -> None:
        result = audit_musicxml_payload(make_score(part_count=1), "fixture.musicxml")
        self.assertEqual(result["quality_status"], "rejected")
        self.assertIn("fewer_than_two_sounding_voices", result["quality_reasons"])

    def test_rejects_voice_streams_without_temporal_overlap(self) -> None:
        result = audit_musicxml_payload(
            make_score(delay_second_part=True),
            "fixture.musicxml",
            thresholds=QualityThresholds(min_overlap_quarters=1.0),
        )
        self.assertEqual(result["quality_status"], "rejected")
        self.assertIn("insufficient_concurrent_voice_overlap", result["quality_reasons"])

    def test_rejects_uniform_chromatic_pitch_distribution(self) -> None:
        result = audit_musicxml_payload(make_score(chromatic=True), "fixture.musicxml")
        self.assertEqual(result["quality_status"], "rejected")
        self.assertIn("weak_tonal_profile", result["quality_reasons"])
        self.assertIn("low_diatonic_pitch_ratio", result["quality_reasons"])

    def test_rejects_unsafe_mxl_rootfile_path(self) -> None:
        payload = make_mxl(make_score(), "../score.musicxml")
        with self.assertRaisesRegex(MusicXMLAuditError, "unsafe_mxl_member_path"):
            musicxml_from_payload(payload, "fixture.mxl")

    def test_rejects_xml_entity_declarations(self) -> None:
        payload = b'<!DOCTYPE score-partwise [<!ENTITY x "bad">]><score-partwise/>'
        with self.assertRaisesRegex(MusicXMLAuditError, "xml_entities_not_allowed"):
            audit_musicxml_payload(payload, "fixture.musicxml")

    def test_same_voice_number_on_two_staves_remains_two_streams(self) -> None:
        xml = (
            '<score-partwise version="4.0"><part-list><score-part id="P1">'
            "<part-name>Piano</part-name></score-part></part-list><part id=\"P1\">"
            '<measure number="1"><attributes><divisions>1</divisions></attributes>'
            "<note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration>"
            "<voice>1</voice><staff>1</staff></note><backup><duration>4</duration></backup>"
            "<note><pitch><step>C</step><octave>3</octave></pitch><duration>4</duration>"
            "<voice>1</voice><staff>2</staff></note></measure></part></score-partwise>"
        ).encode("utf-8")
        result = audit_musicxml_payload(
            xml,
            "piano.musicxml",
            thresholds=QualityThresholds(
                min_pitched_notes=2,
                min_measures=1,
                min_voices=2,
                min_overlap_quarters=1,
                min_pitch_classes=1,
                min_tonal_correlation=-1,
                min_diatonic_ratio=0,
            ),
        )
        self.assertEqual(result["quality_status"], "accepted")
        self.assertEqual(result["voice_count"], 2)
        self.assertEqual(result["max_pair_overlap_quarters"], 4.0)

    def test_chord_notes_do_not_multiply_overlap_duration(self) -> None:
        events = [
            NoteEvent("P1:voice-1", Fraction(0), Fraction(1), 60),
            NoteEvent("P1:voice-1", Fraction(0), Fraction(1), 64),
            NoteEvent("P2:voice-1", Fraction(0), Fraction(1), 55),
        ]
        evidence = polyphony_evidence(events)
        self.assertEqual(evidence["max_pair_overlap_quarters"], 1.0)


if __name__ == "__main__":
    unittest.main()
