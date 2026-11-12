# Score rights policy

## Intended use

The default collection profile supports non-commercial research, education,
analysis, and corpus publication. It may include scores whose licences permit
commercial reuse and scores whose licences prohibit it. Every manifest row keeps
the stricter source-specific conditions.

This policy applies to discovered scores, not to the MusicXML Discovery source
code. The software remains MIT-licensed so it can be audited, reproduced, and
extended without conflating software rights with data rights.

## Eligible score licences

An approved source must provide evidence that score redistribution and
derivative processing are permitted. Eligible classes include:

- public domain marks, dedications, and CC0;
- CC BY;
- CC BY-SA;
- CC BY-NC;
- CC BY-NC-SA;
- another explicit licence whose redistribution and derivative terms have been
  reviewed and recorded.

Non-commercial material is eligible for the Open-NC profile. It must remain
clearly labelled `commercial_use: prohibited` in inventories, audits, exports,
and any downstream catalogue.

## Ineligible or quarantined material

- no licence or an unknown/ambiguous licence;
- “publicly accessible” files without redistribution permission;
- no-derivatives licences, because normalization, repair, conversion, and
  feature extraction can create derivatives;
- a repository-level software licence with no evidence that it covers scores;
- contradictory score-level and repository-level rights metadata unless an
  unconflicted subset can be selected deterministically;
- sources requiring a commercial subscription, paywall circumvention, or access
  control bypass.

## Research-only isolation

A source may still be useful for private, transformative scholarship when its
terms do not support public corpus redistribution. Such material must use the
`research-only` decision, remain physically separated from release staging, and
carry `public_release_eligible: false` in every local-audit row. Passing musical
quality gates never overrides that status. Public promotion requires new,
item-level rights evidence; non-commercial intent by itself is insufficient.

## Required machine-readable evidence

Each approved registry source records:

- `score_license`;
- `rights_decision`;
- `redistribution_permitted`;
- `derivatives_permitted`;
- `commercial_use` (`permitted` or `prohibited`);
- `attribution_required`;
- `license_path`, `rights_evidence_path`, and immutable evidence URLs;
- source-specific attribution and assessment notes.

The CLI refuses sources that prohibit redistribution or derivatives. GitHub's
automatic SPDX classifier is supporting evidence only: when it returns
`NOASSERTION`, the explicit pinned licence and rights-evidence files must support
the decision and the discrepancy must be documented.

## Release consequences

The collected score set is multi-licensed. It must not be relicensed as one
homogeneous MIT, CC0, or CC BY dataset. A downstream release must:

1. preserve per-file provenance and licence metadata;
2. provide attribution required by CC BY/CC BY-NC material;
3. satisfy share-alike requirements for adapted CC BY-SA/CC BY-NC-SA material;
4. label or physically separate the non-commercial partition;
5. exclude quarantined files and unresolved identity conflicts;
6. include a correction and takedown process.

This is a conservative research-data policy, not legal advice.
