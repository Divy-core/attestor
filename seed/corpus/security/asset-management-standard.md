# Asset Management Standard

**Document ID:** KD-SEC-020 · **Version:** 1.5 · **Owner:** Sofia Brenner, Head of IT
**Approved:** 14 January 2026 · **Next review:** 14 January 2027
**Maps to:** SOC 2 CC6.1, CC6.5 · ISO 27001:2022 A.5.9, A.5.10, A.5.11, A.7.9, A.7.10, A.7.14, A.8.1

## 1. What counts as an asset

Kestrel maintains four inventories rather than one, because the sources of truth differ
and a merged list would be stale in all four dimensions:

| Inventory | Source of truth | Count at 1 Feb 2026 |
|---|---|---|
| Endpoints | Kandji (macOS) and Fleet (Linux) | 174 |
| Cloud resources | AWS Config aggregator across 6 accounts | 4,318 |
| SaaS applications | Okta application assignments plus the vendor register | 63 |
| Code repositories | GitHub organisation `kestreldata` | 214 |

Each inventory is reconciled monthly, and the reconciliation is a Vanta-monitored control.

## 2. Ownership

Every asset has a named owner. For cloud resources this is enforced by tagging: the
`Owner`, `Environment`, `DataClass`, and `CostCentre` tags are mandatory, and a Service
Control Policy denies creation of taggable resources without them. Untagged legacy
resources were remediated in the campaign that closed on 30 September 2025; the current
tag compliance rate is 99.6%, with the residual being AWS-managed resources that do not
accept tags.

## 3. Endpoint lifecycle

Devices are procured centrally, enrolled in Kandji before issue, and issued with a
recorded serial number against the person in Rippling. A device is never issued
unenrolled, because an unenrolled device cannot be wiped remotely.

At offboarding the device is returned within 5 business days, remotely wiped, and either
re-issued or disposed of. Where a remote contractor does not return a device within the
window, the device is remotely wiped and locked, and the loss is recorded; this happened
once in 2025.

## 4. Disposal and media sanitisation

Retired laptops are wiped through Kandji, then either re-issued internally or transferred
to Techno Rescue (Austin) for recycling under a certificate of destruction retained for
three years. Nine devices were disposed of in 2025, each with a serialised certificate on
file.

Kestrel holds no production storage media of its own: AWS decommissions storage under its
own NIST SP 800-88 process, evidenced through the AWS SOC 2 report. No customer data
resides on any Kestrel-controlled physical medium.

## 5. Software asset management

Software is licensed and tracked centrally. Unlicensed or unapproved software on a managed
endpoint is detected by Kandji inventory scans and removed. The SaaS register records, for
each application: the owner, the data classification it may hold, whether it processes
customer personal data, and whether it appears on the subprocessor list.

## 6. Removable media

Write access to USB mass storage is blocked on all managed endpoints by policy. There is
no approved workflow for moving customer data onto removable media, and none has been
requested since the control was introduced in March 2024.

## 7. Cloud resource lifecycle

Resources are created and destroyed by Terraform. Orphaned resources — those present in
AWS but absent from state — are detected by the weekly drift job and either imported or
deleted. Twelve orphans were found and resolved during 2025, all in the staging account.

Production compute nodes are recycled on a rolling 30-day maximum age, so the compute
inventory is continuously replaced rather than maintained (see KD-SEC-017 §5).

## 8. Return of assets

Return of company assets is a condition of the employment and contractor agreements. The
offboarding checklist held by the Head of IT covers device return, badge return, and
confirmation that access removal completed; the checklist is retained as evidence and was
sampled by the auditors during the 2025 SOC 2 examination without exception.
