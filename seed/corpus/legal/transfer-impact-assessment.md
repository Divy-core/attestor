# Transfer Impact Assessment

**Document ID:** KD-LEG-010 · **Version:** 2.1 · **Owner:** Priya Raghunathan, DPO
**Approved:** 9 January 2026 · **Next review:** 9 January 2027
**Maps to:** GDPR Articles 44–49 · EDPB Recommendations 01/2020 · Schrems II (C-311/18)

## 1. Status

**Kestrel Data has completed a Transfer Impact Assessment**, most recently updated on
9 January 2026. It is refreshed annually and on any material change to the transfer chain,
the receiving country's law, or the supplementary measures relied on. A copy is available
to customers under NDA.

## 2. The transfers assessed

| # | Transfer | Exporter | Importer | Country | Data |
|---|---|---|---|---|---|
| T1 | EU tenant support access | Kestrel EU instance | Kestrel Data, Inc. | United States | Account identity data, support correspondence |
| T2 | Product telemetry aggregation | Kestrel EU instance | Kestrel Data, Inc. | United States | Pseudonymised usage events |
| T3 | Error monitoring | Kestrel EU instance | Datadog, Inc. | United States | Operational logs, no customer content |
| T4 | Transactional email | Kestrel EU instance | Twilio Inc. (SendGrid) | United States | Recipient email address |

**Customer-uploaded analytics datasets are not transferred.** EU tenant content remains in
`eu-west-1`, including backups, which is the single most important fact in this assessment
and the reason the residual risk is as low as it is.

## 3. Transfer mechanism

The 2021 **Standard Contractual Clauses, Module Two (controller to processor)** apply
between the customer and Kestrel, and **Module Three (processor to sub-processor)** between
Kestrel and its US sub-processors. The **UK International Data Transfer Addendum** is
incorporated for UK exports, and for Switzerland the SCCs are applied with the adaptations
published by the Swiss FDPIC, naming the FDPIC as competent authority and extending
protection to legal entities.

Kestrel is **not** self-certified under the EU-US Data Privacy Framework. Reliance is on
the SCCs plus the measures below, which do not depend on an adequacy decision remaining in
force — a deliberate choice after two adequacy frameworks were annulled.

## 4. Assessment of US law

The assessment considers FISA §702 and Executive Order 12333, and reaches these
conclusions:

* Kestrel is **not** an "electronic communications service provider" as defined in 50 USC
  §1881(b)(4). It provides an analytics platform, not communications services, and has
  never received a directive under §702.
* Executive Order 14086 and the Data Protection Review Court materially narrow the gap
  identified in Schrems II, though Kestrel does not rely on them as the sole basis.
* **Kestrel has never received a National Security Letter, a FISA order, or any other
  national security process**, and has never disclosed customer content to a government
  authority. See KD-LEG-012 for the process and the published figures.

## 5. Supplementary measures relied on

Technical:

1. **Data residency by default** — EU customer content, including backups and the
   replicated log ship, never leaves `eu-west-1`.
2. **Encryption in transit** — TLS 1.3 (1.2 minimum) on every hop, including between
   Kestrel and each sub-processor.
3. **Encryption at rest** — AES-256-GCM with keys in AWS KMS. Keys for the EU instance are
   held in the EU KMS region and are not replicated to the US.
4. **Pseudonymisation** — telemetry transferred under T2 carries a tenant identifier and a
   hashed user identifier, with the mapping table held only in the EU instance, so the
   transferred data cannot be attributed to an individual by the importer alone.
5. **No content in monitoring** — Datadog receives operational logs with customer content
   fields redacted at source by the logging pipeline (KD-SEC-009 §1).

Organisational and contractual:

6. Government-request policy requiring legal review, challenge of overbroad or unlawful
   requests, and customer notification unless legally prohibited (KD-LEG-012).
7. A transparency report published annually.
8. Onward transfer restrictions in every sub-processor agreement, with SCCs Module Three
   in place.
9. Access to EU tenant data by US personnel is just-in-time, per-incident, customer
   authorised, and logged — 19 such accesses in 2025 across all instances.

## 6. Conclusion

For T1 and T2 the residual risk to data subjects is assessed as **low**: the data
transferred is limited, pseudonymised where possible, encrypted with keys held outside the
importer's jurisdiction, and unaccompanied by any customer content. For T3 and T4 the
residual risk is **low** on the same basis, with the additional point that no customer
content is transferred at all.

The assessment concludes that the transfers may proceed on the SCCs with the supplementary
measures listed. It will be re-performed if any of the following occur: receipt of a
national security request, a change in the residency architecture, a new US sub-processor
receiving customer content, or a material change in US surveillance law.
