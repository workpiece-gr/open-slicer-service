# Coding-agent repository rules

How we work (reviews, CI, merging, publishing, evaluations, wording) is written once, in `docs/SYSTEM_MAP.md` section 0 of the private `workpiece-gr/workpiece-web` repository: read it first. This repository is public: never put host details, credentials, addresses or customer data here.

Before substantive changes read `README.md` and the relevant `docs/FDM_AUTHORITY_V2*.md` and qualification/release documents.

This repository's own rules:

- Maintain this repository's public GNU AGPL-3.0-or-later licensing, the exact source offer, pinned toolchains, provenance and evidence identities.
- Never silently substitute an unqualified profile, slicer image or build for the exact reviewed source/authority. A passing CI or synthetic result does not qualify a real printer or production host.
- Keep the legacy slicing service and the qualified Authority separate; treat qualification and authority records as immutable.
- Use focused PRs; do not merge failing changes and do not alter deployed services without the owner's explicit approval.
