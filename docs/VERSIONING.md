# Versioning

llauncher carries two numbering axes that are **independent**:

- **Internal generation** — `v1`, `v2`, `v3` … denotes the *architecture
  epoch* (e.g. `v2` is the `operations/` stateless-facade refactor, ADR-LLNCH-008+).
- **External semver** — `0.x` (e.g. `0.4.1a0` / `v0.4.1-alpha`) denotes the
  *release*, per `pyproject.toml`, `llauncher/__init__.py`, and the latest git
  tag. Read the live number from those sources rather than this doc — it
  drifts every release.

> `vN` denotes architecture generation; `0.x` denotes semver release. They are
> independent axes and do not map to each other.

There is deliberately **no** mapping table and **no** `vN ↔ 0.(N+1)`
correspondence. Reaching for one (e.g. "`v2` == `0.4.0-alpha`?") manufactures a
false equivalence — the axes advance on their own schedules and are read
separately.

## Tag-namespace note

Release tags live in the `v0.x.y` namespace (`v0.1.0-alpha … v0.4.1-alpha`).
Generation labels (`v1`, `v2`, …) belong to the architecture axis and should
not be minted as release tags. The historical `v1-final` tag is the one spot
where the two axes physically collide; treat it as legacy, and keep generation
labels out of the release-tag namespace going forward.
