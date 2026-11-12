# MusicXML Discovery

Rights-aware discovery and deterministic screening of MusicXML scores hosted in
public GitHub repositories.

The command inventories files at an immutable Git commit, records provenance and
per-source reuse conditions, optionally downloads candidates, and screens them for tonal
music with at least two simultaneously sounding encoded voices. It produces
JSONL manifests and human-readable reports; it does **not** silently promote
scores into a corpus.

## Why this exists

GitHub code search alone is not a reproducible dataset method. Results are
rate-limited, incomplete, mutable, and do not establish that an individual score
can be redistributed. MusicXML Discovery therefore separates two decisions:

1. A human-curated registry approves a repository and records score-level rights
   evidence.
2. The software enumerates the approved repository tree at a resolved commit and
   applies transparent musical and file-safety checks.

This conservative workflow was extracted from the HARMONIA corpus pipeline in
the ANR MELODY project. The software is general-purpose; its registry includes
permissive and non-commercial sources but is not a claim that every public
GitHub MusicXML file is reusable.

## What the audit checks

- MusicXML or compressed MXL structure, with archive traversal and zip-bomb
  protections;
- agreement between each downloaded payload and its commit-pinned Git blob hash,
  plus a separate SHA-256 content digest;
- minimum pitched-note and measure counts;
- at least two encoded streams with actual temporal overlap;
- minimum pitch-class variety;
- a Krumhansl–Kessler key-profile correlation and diatonic pitch-duration ratio;
- plausible encoded key signatures;
- exact Git-blob duplicates and normalized musical-content duplicates.

The tonal classifier is an explainable screening heuristic, not a musicological
ground truth. Scores near a threshold should be reviewed manually. A “candidate”
result means eligible for downstream review, not published or legally cleared.

## Installation

Requires Python 3.10 or newer and has no runtime dependencies.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
musicxml-discovery --help
```

An authenticated GitHub token is strongly recommended for larger inventories:

```bash
export GITHUB_TOKEN=your_fine_grained_token
```

The token only needs read access to public repository metadata and contents. It
is read from `GITHUB_TOKEN` or `GH_TOKEN` and is never written to reports.

## Quick start

Inventory the approved permissive and non-commercial example sources without
downloading scores:

```bash
musicxml-discovery --registry examples/sources.example.json
```

Download and audit the first 20 non-conflicting files:

```bash
musicxml-discovery \
  --registry examples/sources.example.json \
  --output-dir output/demo \
  --apply --limit 20
```

Remove `--limit` to audit the complete approved snapshot. The command resolves
the configured ref once, then pins all provenance and raw downloads to that
commit SHA.

### Audit an existing local collection

Use the separate local command for a checkout or archive that is already on
disk. It reads scores in place and does not copy them into a release corpus:

```bash
musicxml-discovery-local \
  --input-dir /path/to/xmldata \
  --output-dir output/local-audit \
  --source-id example-research-archive \
  --source-url https://example.org/source \
  --commit-sha FULL_COMMIT_SHA \
  --score-license 'licence-with-recorded-caveat' \
  --rights-decision research-only \
  --commercial-use unclear \
  --derivatives-permitted \
  --attribution-required
```

`research-only` rows can become `research_candidate`, but are always marked
`public_release_eligible: false`. The command refuses to label a source
`approved` unless redistribution and derivatives are permitted and commercial
use is explicitly classified. Local audit output uses `candidates.jsonl`
instead of `accepted.jsonl` so a musical result cannot be confused with rights
clearance.

## Outputs

The output directory contains:

| Path | Purpose |
| --- | --- |
| `inventory.jsonl` | Every matching file plus immutable provenance and rights evidence |
| `audit.jsonl` | Download and quality decision for every audited file |
| `accepted.jsonl` | Candidate-only view for downstream review |
| `report.json` | Machine-readable run summary and thresholds |
| `report.md` | Human-readable summary |
| `staging/` | Retrieved source payloads, isolated from any release corpus |

The local command writes `inventory.jsonl`, `audit.jsonl`,
`candidates.jsonl`, `report.json`, and `report.md` only. Its report records
source-file errors and whether the audit is public-release eligible.

Exact duplicate Git blobs are fully quarantined rather than choosing an
arbitrary filename. After audit, identical normalized musical event content is
also fully quarantined. This avoids silently assigning a contested identity.

## Reference validation snapshot

The `0.1.0` registry was smoke-tested on 2026-07-30 against pinned OpenScore
Lieder and ASAP v1.1 trees. It inventoried 1,697 MusicXML/MXL paths and 1,696
unique blobs, quarantined one exact-blob identity group (two paths), and left
1,695 entries eligible for audit with no source errors. Of those paths, 1,462 are
CC0 and 235 are CC BY-NC-SA 4.0. These counts describe immutable example
snapshots, not all MusicXML on GitHub.

## Source registry

Copy `examples/sources.example.json` and review every source record. A source is
accepted only when `rights_decision` is `approved`, required evidence fields are
present, the repository is public and active, and the repository SPDX identifier
matches the registry expectation when one is supplied.

Important fields:

- `repository`: GitHub `owner/name`;
- `ref`: tag, branch, or commit to resolve (a release tag or commit is best);
- `score_license`: the license covering the notation files, which may differ
  from the software license;
- `commercial_use`, `redistribution_permitted`, `derivatives_permitted`, and
  `attribution_required`: explicit downstream-use constraints;
- `license_path` and `rights_evidence_path`: repository files supporting the
  decision;
- `include_prefixes`, `exclude_prefixes`, and `extensions`: bounded discovery
  scope;
- `attribution`: text retained in every manifest row.

Do not approve a repository merely because it is public or because GitHub shows
a software license. Confirm that the score files themselves are covered and
that any source-specific attribution or share-alike duties are compatible with
the intended corpus.

See [RIGHTS_POLICY.md](RIGHTS_POLICY.md) for the Open-NC eligibility and release
rules, and [SOURCES.md](SOURCES.md) for operational, planned, and review-only
collections. The default registry includes ASAP's explicitly CC BY-NC-SA 4.0
MusicXML scores as well as CC0 OpenScore Lieder.

## Reproducibility and limitations

- Discovery enumerates the Git tree of explicitly approved repositories. It does
  not claim exhaustive discovery across GitHub.
- MusicXML semantics are complex. The dependency-light parser handles common
  partwise and timewise encodings, voices, staves, chords, backups, forwards,
  grace notes, and changing divisions, but is not a replacement for a notation
  engine.
- “Voice” means an encoded part/voice or part/staff stream, not an inferred
  contrapuntal voice.
- Tonality is inferred from pitch-duration distributions. Modal, highly
  chromatic, short, or unusual tonal works can be false negatives.
- Rights approval remains a human responsibility. The tool preserves evidence;
  it does not provide legal advice.

## Development

```bash
python -m unittest discover -s tests -v
python scripts/build_release.py
```

The release builder creates a deterministic source ZIP and SHA-256 checksum in
`dist/`. The working tree includes changes after 0.1.0; see the [changelog](CHANGELOG.md).
No project-specific public Zenodo or HAL identifier has been verified for this tool.
DOIs in the [source registry notes](SOURCES.md) identify upstream datasets, not this software.

## Citation and license

Citation metadata is provided in [CITATION.cff](CITATION.cff). The software is
released under the [MIT License](LICENSE). Discovered scores retain their own
licences—including non-commercial, attribution, and share-alike conditions—and
this repository does not relicense them.
