# Coding-agent repository rules

Read `README.md`, [the bounded execution protocol](docs/AGENT-EXECUTION-PROTOCOL.md), and the relevant `docs/FDM_AUTHORITY_V2*.md` and qualification/release documents before substantive changes.

- Follow approximately 25-minute bounded implementation sessions where practical, with real source/tests early and a precise checkpoint on completion or interruption.
- Verify relevant branch heads once at the start, preserve other contributors' branches and authority records, prefer local focused tests over repeated CI, and make only one reasonable retry before documenting blockers.
- Separate independent implementation and verification lanes; concurrent writes to the same branch or file are forbidden.
- Maintain this repository's public GNU AGPL-3.0-or-later licensing, exact source offer, pinned toolchains, provenance and evidence identities.
- Never silently substitute an unqualified profile, slicer image or build for the exact reviewed source/authority. A passing CI or synthetic result does not qualify a real printer or production host.
- Use focused PRs; do not merge failing changes or alter deployed services without the required explicit approval.
