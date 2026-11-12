"""Dependency-light quality gates for MusicXML discovery candidates.

This module deliberately does not promote files into ``records/``.  It parses a
MusicXML or compressed MXL payload, extracts a compact musical event model, and
returns the evidence needed by the GitHub staging pipeline.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import posixpath
import statistics
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from pathlib import PurePosixPath
from typing import Iterator
from xml.etree import ElementTree as ET


MAX_XML_BYTES = 50 * 1024 * 1024
MAX_MXL_MEMBERS = 10_000
MAX_MXL_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_MXL_COMPRESSION_RATIO = 250

PITCH_CLASS_NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
STEP_TO_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
MAJOR_SCALE = frozenset({0, 2, 4, 5, 7, 9, 11})
MINOR_SCALE = frozenset({0, 2, 3, 5, 7, 8, 10})

# Krumhansl-Kessler key profiles, starting on the tonic.
MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)


class MusicXMLAuditError(ValueError):
    """Raised when a payload cannot be safely interpreted as MusicXML."""


@dataclass(frozen=True)
class QualityThresholds:
    """Conservative defaults for a tonal, polyphonic MusicXML candidate."""

    min_pitched_notes: int = 32
    min_measures: int = 4
    min_voices: int = 2
    min_overlap_quarters: float = 1.0
    min_pitch_classes: int = 5
    min_tonal_correlation: float = 0.45
    min_diatonic_ratio: float = 0.70


@dataclass(frozen=True)
class NoteEvent:
    stream: str
    start: Fraction
    duration: Fraction
    midi: int

    @property
    def end(self) -> Fraction:
        return self.start + self.duration


def local_name(tag: str) -> str:
    """Return an XML local name with any namespace removed."""

    return tag.rsplit("}", 1)[-1]


def child(element: ET.Element, name: str) -> ET.Element | None:
    for item in element:
        if local_name(item.tag) == name:
            return item
    return None


def child_text(element: ET.Element, name: str, default: str = "") -> str:
    item = child(element, name)
    return (item.text or "").strip() if item is not None else default


def descendants(element: ET.Element, name: str) -> Iterator[ET.Element]:
    return (item for item in element.iter() if local_name(item.tag) == name)


def safe_xml_root(xml_bytes: bytes) -> ET.Element:
    if len(xml_bytes) > MAX_XML_BYTES:
        raise MusicXMLAuditError("xml_too_large")
    # MusicXML commonly includes an external DOCTYPE. ElementTree does not load
    # it, but custom entity declarations are unnecessary and risky here.
    if b"<!ENTITY" in xml_bytes.upper():
        raise MusicXMLAuditError("xml_entities_not_allowed")
    try:
        return ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise MusicXMLAuditError(f"invalid_xml:{exc}") from exc


def _safe_member_name(name: str) -> str:
    normalized = posixpath.normpath(name.replace("\\", "/"))
    path = PurePosixPath(normalized)
    if path.is_absolute() or normalized == ".." or normalized.startswith("../"):
        raise MusicXMLAuditError("unsafe_mxl_member_path")
    return normalized


def musicxml_from_payload(payload: bytes, filename: str = "score.mxl") -> tuple[bytes, str]:
    """Return raw MusicXML bytes and the selected member/source name."""

    is_zip = filename.lower().endswith(".mxl") or payload.startswith(b"PK\x03\x04")
    if not is_zip:
        return payload, filename

    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise MusicXMLAuditError("invalid_mxl_zip") from exc

    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_MXL_MEMBERS:
            raise MusicXMLAuditError("too_many_mxl_members")
        total_uncompressed = 0
        names: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            normalized = _safe_member_name(info.filename)
            if normalized in names:
                raise MusicXMLAuditError("duplicate_mxl_member_name")
            names[normalized] = info
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_MXL_UNCOMPRESSED_BYTES:
                raise MusicXMLAuditError("mxl_uncompressed_size_limit")
            if info.file_size and info.compress_size == 0:
                raise MusicXMLAuditError("mxl_invalid_compression_ratio")
            if info.compress_size and info.file_size / info.compress_size > MAX_MXL_COMPRESSION_RATIO:
                raise MusicXMLAuditError("mxl_compression_ratio_limit")

        container_info = names.get("META-INF/container.xml")
        if container_info is None:
            raise MusicXMLAuditError("mxl_container_missing")
        container_root = safe_xml_root(archive.read(container_info))
        rootfiles = list(descendants(container_root, "rootfile"))
        if not rootfiles:
            raise MusicXMLAuditError("mxl_rootfile_missing")
        root_path = _safe_member_name(rootfiles[0].attrib.get("full-path", ""))
        root_info = names.get(root_path)
        if root_info is None:
            raise MusicXMLAuditError("mxl_rootfile_not_found")
        if root_info.file_size > MAX_XML_BYTES:
            raise MusicXMLAuditError("xml_too_large")
        return archive.read(root_info), root_path


def _duration(element: ET.Element, divisions: int) -> Fraction:
    raw = child_text(element, "duration", "0")
    try:
        value = int(raw)
    except ValueError as exc:
        raise MusicXMLAuditError("invalid_duration") from exc
    return Fraction(value, max(divisions, 1))


def _midi_pitch(note: ET.Element) -> int | None:
    pitch = child(note, "pitch")
    if pitch is None:
        return None
    step = child_text(pitch, "step").upper()
    if step not in STEP_TO_PC:
        raise MusicXMLAuditError("invalid_pitch_step")
    try:
        octave = int(child_text(pitch, "octave"))
        alter = float(child_text(pitch, "alter", "0") or "0")
    except ValueError as exc:
        raise MusicXMLAuditError("invalid_pitch_value") from exc
    if not math.isclose(alter, round(alter), abs_tol=1e-9):
        raise MusicXMLAuditError("microtonal_pitch_outside_scope")
    midi = 12 * (octave + 1) + STEP_TO_PC[step] + int(round(alter))
    if not 0 <= midi <= 127:
        raise MusicXMLAuditError("midi_pitch_out_of_range")
    return midi


def _stream_id(part_id: str, note: ET.Element) -> str:
    voice = child_text(note, "voice")
    staff = child_text(note, "staff")
    if voice and staff:
        layer = f"staff-{staff}:voice-{voice}"
    elif voice:
        layer = f"voice-{voice}"
    elif staff:
        layer = f"staff-{staff}"
    else:
        layer = "voice-1"
    return f"{part_id}:{layer}"


def _parse_measure(
    measure_like: ET.Element,
    part_id: str,
    base: Fraction,
    divisions: int,
) -> tuple[list[NoteEvent], Fraction, int, list[int], list[str]]:
    events: list[NoteEvent] = []
    cursor = base
    furthest = base
    last_note_start = cursor
    fifths: list[int] = []
    modes: list[str] = []

    for element in measure_like:
        name = local_name(element.tag)
        if name == "attributes":
            divisions_text = child_text(element, "divisions")
            if divisions_text:
                try:
                    divisions = int(divisions_text)
                except ValueError as exc:
                    raise MusicXMLAuditError("invalid_divisions") from exc
                if divisions <= 0:
                    raise MusicXMLAuditError("nonpositive_divisions")
            for key in (item for item in element if local_name(item.tag) == "key"):
                raw_fifths = child_text(key, "fifths")
                if raw_fifths:
                    try:
                        fifths.append(int(raw_fifths))
                    except ValueError as exc:
                        raise MusicXMLAuditError("invalid_key_fifths") from exc
                mode = child_text(key, "mode").lower()
                if mode:
                    modes.append(mode)
        elif name == "backup":
            cursor -= _duration(element, divisions)
        elif name == "forward":
            cursor += _duration(element, divisions)
            furthest = max(furthest, cursor)
        elif name == "note":
            if child(element, "grace") is not None:
                continue
            duration = _duration(element, divisions)
            is_chord = child(element, "chord") is not None
            start = last_note_start if is_chord else cursor
            if not is_chord:
                last_note_start = start
                cursor += duration
            furthest = max(furthest, cursor, start + duration)
            midi = _midi_pitch(element)
            if midi is not None and duration > 0:
                events.append(NoteEvent(_stream_id(part_id, element), start, duration, midi))

    if cursor < base:
        raise MusicXMLAuditError("backup_before_measure_start")
    return events, furthest, divisions, fifths, modes


def _metadata(root: ET.Element) -> tuple[str, str]:
    title = ""
    for name in ("work-title", "movement-title"):
        item = next(descendants(root, name), None)
        if item is not None and (item.text or "").strip():
            title = (item.text or "").strip()
            break
    composer = ""
    for creator in descendants(root, "creator"):
        if creator.attrib.get("type", "").lower() == "composer" and (creator.text or "").strip():
            composer = (creator.text or "").strip()
            break
    return title, composer


def extract_musicxml_events(xml_bytes: bytes) -> dict:
    """Extract sounding pitch events without relying on a notation application."""

    root = safe_xml_root(xml_bytes)
    root_type = local_name(root.tag)
    if root_type not in {"score-partwise", "score-timewise"}:
        raise MusicXMLAuditError(f"not_musicxml_score:{root_type}")

    events: list[NoteEvent] = []
    all_fifths: list[int] = []
    all_modes: list[str] = []
    part_ids: set[str] = set()
    measure_count = 0

    if root_type == "score-partwise":
        parts = [item for item in root if local_name(item.tag) == "part"]
        for part_index, part in enumerate(parts, 1):
            part_id = part.attrib.get("id") or f"P{part_index}"
            part_ids.add(part_id)
            base = Fraction(0)
            divisions = 1
            measures = [item for item in part if local_name(item.tag) == "measure"]
            measure_count = max(measure_count, len(measures))
            for measure in measures:
                new_events, base, divisions, fifths, modes = _parse_measure(
                    measure, part_id, base, divisions
                )
                events.extend(new_events)
                all_fifths.extend(fifths)
                all_modes.extend(modes)
    else:
        grouped: dict[str, list[ET.Element]] = defaultdict(list)
        measures = [item for item in root if local_name(item.tag) == "measure"]
        measure_count = len(measures)
        for measure in measures:
            for part_index, part in enumerate(
                (item for item in measure if local_name(item.tag) == "part"), 1
            ):
                part_id = part.attrib.get("id") or f"P{part_index}"
                grouped[part_id].append(part)
                part_ids.add(part_id)
        for part_id, part_measures in grouped.items():
            base = Fraction(0)
            divisions = 1
            for measure_like in part_measures:
                new_events, base, divisions, fifths, modes = _parse_measure(
                    measure_like, part_id, base, divisions
                )
                events.extend(new_events)
                all_fifths.extend(fifths)
                all_modes.extend(modes)

    title, composer = _metadata(root)
    return {
        "root_type": root_type,
        "title": title,
        "composer": composer,
        "part_count": len(part_ids),
        "measure_count": measure_count,
        "events": events,
        "key_fifths": all_fifths,
        "key_modes": all_modes,
    }


def _pearson(left: list[float], right: list[float]) -> float:
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_energy = sum((a - left_mean) ** 2 for a in left)
    right_energy = sum((b - right_mean) ** 2 for b in right)
    denominator = math.sqrt(left_energy * right_energy)
    return numerator / denominator if denominator else 0.0


def estimate_tonality(events: list[NoteEvent]) -> dict:
    weights = [0.0] * 12
    for event in events:
        weights[event.midi % 12] += float(event.duration)
    total = sum(weights)
    if not total:
        return {
            "estimated_key": None,
            "estimated_mode": None,
            "tonal_correlation": 0.0,
            "correlation_gap": 0.0,
            "diatonic_ratio": 0.0,
            "pitch_class_count": 0,
        }

    candidates: list[tuple[float, int, str, frozenset[int]]] = []
    for tonic in range(12):
        for mode, profile, scale in (
            ("major", MAJOR_PROFILE, MAJOR_SCALE),
            ("minor", MINOR_PROFILE, MINOR_SCALE),
        ):
            rotated = [profile[(pitch_class - tonic) % 12] for pitch_class in range(12)]
            candidates.append((_pearson(weights, rotated), tonic, mode, scale))
    candidates.sort(reverse=True, key=lambda item: item[0])
    best, runner_up = candidates[0], candidates[1]
    correlation, tonic, mode, scale = best
    in_scale = sum(weights[pitch_class] for pitch_class in range(12) if (pitch_class - tonic) % 12 in scale)
    return {
        "estimated_key": PITCH_CLASS_NAMES[tonic],
        "estimated_mode": mode,
        "tonal_correlation": round(correlation, 6),
        "correlation_gap": round(correlation - runner_up[0], 6),
        "diatonic_ratio": round(in_scale / total, 6),
        "pitch_class_count": sum(1 for value in weights if value > 0),
    }


def _merged_intervals(events: list[NoteEvent]) -> list[tuple[Fraction, Fraction]]:
    """Union a stream's sounding intervals so chord notes do not multiply time."""

    merged: list[tuple[Fraction, Fraction]] = []
    for event in sorted(events, key=lambda item: (item.start, item.end)):
        if not merged or event.start > merged[-1][1]:
            merged.append((event.start, event.end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], event.end))
    return merged


def _interval_overlap(left_events: list[NoteEvent], right_events: list[NoteEvent]) -> Fraction:
    left = _merged_intervals(left_events)
    right = _merged_intervals(right_events)
    i = j = 0
    overlap = Fraction(0)
    while i < len(left) and j < len(right):
        start = max(left[i][0], right[j][0])
        end = min(left[i][1], right[j][1])
        if end > start:
            overlap += end - start
        if left[i][1] <= right[j][1]:
            i += 1
        else:
            j += 1
    return overlap


def polyphony_evidence(events: list[NoteEvent]) -> dict:
    by_stream: dict[str, list[NoteEvent]] = defaultdict(list)
    for event in events:
        by_stream[event.stream].append(event)
    streams = sorted(by_stream)
    best_overlap = Fraction(0)
    overlapping_pairs = 0
    for left_index, left_stream in enumerate(streams):
        for right_stream in streams[left_index + 1 :]:
            overlap = _interval_overlap(by_stream[left_stream], by_stream[right_stream])
            if overlap > 0:
                overlapping_pairs += 1
                best_overlap = max(best_overlap, overlap)
    return {
        "voice_count": len(streams),
        "overlapping_voice_pairs": overlapping_pairs,
        "max_pair_overlap_quarters": round(float(best_overlap), 6),
    }


def musical_content_hash(events: list[NoteEvent]) -> str:
    streams = {name: index for index, name in enumerate(sorted({event.stream for event in events}))}
    canonical = [
        [
            str(event.start),
            str(event.duration),
            event.midi,
            streams[event.stream],
        ]
        for event in sorted(
            events,
            key=lambda item: (item.start, item.duration, item.midi, streams[item.stream]),
        )
    ]
    payload = json.dumps(canonical, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def audit_musicxml_payload(
    payload: bytes,
    filename: str,
    *,
    fallback_title: str = "",
    fallback_composer: str = "",
    thresholds: QualityThresholds | None = None,
) -> dict:
    """Audit one payload and return a JSON-serializable decision record."""

    thresholds = thresholds or QualityThresholds()
    xml_bytes, member_name = musicxml_from_payload(payload, filename)
    extracted = extract_musicxml_events(xml_bytes)
    events: list[NoteEvent] = extracted.pop("events")
    tonal = estimate_tonality(events)
    polyphony = polyphony_evidence(events)
    duration = max((event.end for event in events), default=Fraction(0))
    key_fifths = extracted["key_fifths"]

    reasons: list[str] = []
    if len(events) < thresholds.min_pitched_notes:
        reasons.append("too_few_pitched_notes")
    if extracted["measure_count"] < thresholds.min_measures:
        reasons.append("too_few_measures")
    if polyphony["voice_count"] < thresholds.min_voices:
        reasons.append("fewer_than_two_sounding_voices")
    if polyphony["max_pair_overlap_quarters"] < thresholds.min_overlap_quarters:
        reasons.append("insufficient_concurrent_voice_overlap")
    if tonal["pitch_class_count"] < thresholds.min_pitch_classes:
        reasons.append("too_few_pitch_classes_for_tonal_test")
    if tonal["tonal_correlation"] < thresholds.min_tonal_correlation:
        reasons.append("weak_tonal_profile")
    if tonal["diatonic_ratio"] < thresholds.min_diatonic_ratio:
        reasons.append("low_diatonic_pitch_ratio")
    if any(value < -7 or value > 7 for value in key_fifths):
        reasons.append("implausible_key_signature")

    return {
        "quality_status": "accepted" if not reasons else "rejected",
        "quality_reasons": reasons,
        "musicxml_member": member_name,
        "title": extracted["title"] or fallback_title,
        "composer": extracted["composer"] or fallback_composer,
        "root_type": extracted["root_type"],
        "part_count": extracted["part_count"],
        "measure_count": extracted["measure_count"],
        "pitched_note_count": len(events),
        "duration_quarters": round(float(duration), 6),
        "key_fifths_observed": sorted(set(key_fifths)),
        "key_modes_observed": sorted(set(extracted["key_modes"])),
        "musical_content_sha256": musical_content_hash(events),
        **polyphony,
        **tonal,
    }
