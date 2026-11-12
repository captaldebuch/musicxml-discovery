# Open score source register

This register distinguishes what the current GitHub-tree connector can process
now from large archives and mixed-rights collections that need additional work.
“All available” means exhaustive enumeration inside approved, immutable source
snapshots—not a claim that every public MusicXML file on the internet is open.

## Approved and operational

| Source | Snapshot | MusicXML paths | Score licence | Commercial use | Status |
| --- | --- | ---: | --- | --- | --- |
| [OpenScore Lieder](https://github.com/OpenScore/Lieder) | `6b2dc542ce2e8aa4b78c8ee62103b210efc07015` | 1,462 MXL | CC0-1.0 | Permitted | Auditable now |
| [ASAP](https://github.com/fosfrancesco/asap-dataset) | `fad8d1e8078d0ae47ad2f280b5d022bd2de24784` (`v1.1`) | 235 MusicXML | CC-BY-NC-SA-4.0 | Prohibited | Auditable now |

The combined validation inventory contains 1,697 paths, 1,696 unique Git blobs,
and 1,695 auditable paths after quarantining the two-path identity conflict in
OpenScore Lieder. ASAP's README explicitly applies CC BY-NC-SA 4.0 to the
dataset; GitHub's automated licence classifier returns `NOASSERTION`, so that
classifier discrepancy is retained in the source record.

## Approved in principle; connector required

### PDMX v9

- Record: [Zenodo 10.5281/zenodo.15571083](https://doi.org/10.5281/zenodo.15571083)
- MXL archive: approximately 1.89 GB, MD5
  `49ffd75ecf5489c0be6d41182eb11ff7`
- Deposit-level licence: CC BY 4.0; the scores are described as public-domain
  MusicXML with per-item licence metadata.
- Required selection: MXL path present, `license_conflict == false`, and the
  documented `no_license_conflict` subset. Prefer the `all_valid` intersection.
- Reason it is not silently enabled: the maintainers report 31,221 records
  (12.29%) whose public and internal licence metadata conflict, and recommend the
  222,856-record `no_license_conflict` subset.

PDMX is the highest-priority size expansion. It needs a resumable Zenodo/archive
adapter, CSV filtering before extraction, checksum verification, and sufficient
disk-space confirmation. The descriptor is in
`examples/external_sources.review.json`; no multi-gigabyte download is triggered
by the default command.

### OpenScore String Quartets

- Repository: [OpenScore/StringQuartets](https://github.com/OpenScore/StringQuartets)
- Score licence: CC0-1.0.
- Current repository format: uncompressed MuseScore (`.mscx`), not MusicXML.
- Integration path: deterministic batch conversion with a pinned MuseScore
  version, followed by the same MusicXML audit and provenance manifest.

## Review-only; do not blanket ingest

### When in Rome

[When in Rome](https://github.com/MarkGotham/When-in-Rome) contains many local
`score.mxl` files and remote score references, but explicitly says that external
licences vary and users must check the licence of each score. Its new analyses
and conversions are CC BY-SA, which does not automatically establish a uniform
licence for every underlying score. It requires per-subcorpus/per-item evidence,
so it is not in the approved registry.

### Software test/example repositories

MusicXML files bundled with renderers, parsers, or notation applications are
not assumed to be a reusable music corpus. A BSD, MIT, GPL, or Apache software
licence may cover tests or code without resolving the rights of the encoded
composition or edition. Such repositories remain review-only unless score-level
coverage is explicit.

### EMMSAP xmldata

[EMMSAP](https://github.com/cuthbertLab/emmsap) contains a substantial medieval
MusicXML collection, but its README says some encodings rely on scholarly fair
use of in-copyright transcriptions and limits the data to transformative and
research purposes—not performances or republication. It is therefore a valuable
research-only source, not an Open-NC redistribution source.

The pinned `xmldata` checkout at commit
`521a41a6747f6c837dc3115bd8849e71bb7e1ba1` contains 3,660 MusicXML-like files
plus 26 Capella files. HARMONIA stores it in an ignored research-only staging
lane and excludes it from public release manifests pending item-level rights
clearance.

The full local audit found 3,315 profile-compatible, overlapping, 2+ stream
research candidates, 340 rejections, and five errors. This is not evidence that
3,315 medieval pieces use common-practice tonality: the key-profile heuristic
also accepts much modal diatonic music. A dedicated modal/tonal classification
stage is required before using EMMSAP in HARMONIA's tonal slice.

The five quarantined source errors are two MXL packages whose declared rootfile
names do not match their archived filenames after character encoding, plus
three OMR MusicXML files containing impossible octave 9/10 pitches. The audit
does not silently repair these records.
