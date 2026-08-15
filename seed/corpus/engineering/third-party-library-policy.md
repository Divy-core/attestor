# Third-Party Library Policy

**Document ID:** KD-ENG-008 · **Version:** 1.4 · **Owner:** Dana Whitfield, VP Engineering
**Approved:** 22 January 2026 · **Maps to:** SOC 2 CC7.1 · ISO 27001:2022 A.8.28

## 1. Adding a dependency

A new direct dependency requires a pull request that documents:

1. Why an existing dependency or the standard library is insufficient.
2. The licence, checked against the allowlist below.
3. Maintenance signal: last release date, open critical issues, number of maintainers.
4. Transitive dependency count added.

Review is by any engineer other than the author. Dependencies pulling in more than 20
transitive packages require VP Engineering approval.

## 2. Licence policy

**Allowed:** MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, ISC, Python Software Foundation,
Unlicense, CC0.

**Requires legal review:** MPL-2.0, LGPL-2.1, LGPL-3.0, EPL-2.0.

**Prohibited:** GPL-2.0, GPL-3.0, AGPL-1.0, AGPL-3.0, SSPL, BUSL, Commons Clause, and any
licence without a clear grant. AGPL and SSPL are prohibited because Kestrel distributes a
network service.

Licence compliance is checked automatically in CI by FOSSA on every pull request. A
prohibited licence fails the build.

## 3. Software Bill of Materials

Kestrel generates a CycloneDX SBOM for every production container image at build time.
SBOMs are retained for 2 years and are provided to customers on request under NDA. The
current production SBOM covers 1,184 components across 6 services.

## 4. Vulnerability response

Dependency vulnerabilities follow the SLAs in KD-SEC-006: Critical 7 days, High 30 days,
Medium 90 days. Snyk runs on every pull request and daily against `main`. A Critical or
High finding blocks merge.

Where no upstream fix exists within the SLA, the options in order of preference are: apply
a vendored patch, replace the dependency, or accept the risk with a documented
compensating control and CISO approval.

## 5. Upgrades

Renovate opens upgrade pull requests weekly. Patch-level security upgrades auto-merge when
CI is green. Minor and major upgrades are reviewed manually.

An explicit goal is to stay within one minor version of current for all direct
dependencies. As of 1 February 2026, 94% of direct dependencies met that target; the 6%
that did not are tracked with named owners.

## 6. Unmaintained dependencies

A dependency with no release in 24 months and no active maintainer is flagged for
replacement. Three such dependencies were identified in the January 2026 review, all
scheduled for replacement by Q3 2026.

## 7. Internal forks

Forking a third-party library requires VP Engineering approval and creates an obligation
to track upstream security advisories manually. Kestrel currently maintains **zero**
internal forks, deliberately.
