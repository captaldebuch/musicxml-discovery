# Changelog

All notable changes to this project will be documented here.

## Unreleased

- Allow the Python entry point to accept an explicit argument list so MELODY Crawl can
  orchestrate the engine without mutating process arguments.
- Pin requests to GitHub REST API version `2026-03-10`.

## 0.1.0 — 2026-07-30

- Extract the HARMONIA GitHub MusicXML discovery and quality-audit workflow as a
  standalone, installable Python package.
- Add commit-pinned provenance, explicit score-rights evidence, safe MXL parsing,
  downloaded Git-blob verification, tonal/polyphonic screening, and two-stage
  duplicate quarantine.
- Add GitHub, CodeMeta, citation, Software Heritage, and HAL publication files.
- Support explicitly redistributable non-commercial score sources with
  machine-readable commercial-use, derivative, and attribution conditions.
- Add the pinned ASAP v1.1 CC BY-NC-SA 4.0 MusicXML source and document PDMX,
  OpenScore String Quartets, and mixed-rights source handling.
- Add an in-place local-collection auditor with explicit public/research-only
  separation and per-file error reporting.
- Register the pinned EMMSAP medieval score archive as research-only, preserving
  its fair-use caveat and excluding its data from distributable releases.
