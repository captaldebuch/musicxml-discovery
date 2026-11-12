"""MusicXML Discovery public package interface."""

from .quality import (
    MusicXMLAuditError,
    NoteEvent,
    QualityThresholds,
    audit_musicxml_payload,
    estimate_tonality,
    extract_musicxml_events,
    polyphony_evidence,
)

__all__ = [
    "MusicXMLAuditError",
    "NoteEvent",
    "QualityThresholds",
    "audit_musicxml_payload",
    "estimate_tonality",
    "extract_musicxml_events",
    "polyphony_evidence",
]
__version__ = "0.1.0"

