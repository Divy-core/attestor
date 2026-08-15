# Standard Contractual Clauses and Transfer Impact Assessment - Summary

**Document ID:** KD-LEG-006 · **Version:** 2.2 · **Owner:** Aaron Feldstein, General Counsel
**Last updated:** 28 January 2026 · **Next review:** 28 January 2027

## 1. Which clauses apply

Kestrel incorporates the **2021 EU Standard Contractual Clauses** (Commission
Implementing Decision (EU) 2021/914 of 4 June 2021):

- **Module Two** (Controller to Processor) where a customer established in the EEA
  transfers personal data to Kestrel in the US.
- **Module Three** (Processor to Sub-processor) where a customer is itself a processor,
  and in Kestrel own contracts with its US sub-processors.

Docking clause (Clause 7) is included. Option 2 of Clause 9 (general written
authorisation for sub-processors) applies, with the 30-day notice period recorded in the
DPA.

Governing law for the SCCs is Irish law; the competent supervisory authority is the Irish
Data Protection Commission; the forum for disputes is the courts of Ireland.

## 2. UK and Switzerland

- **UK** - the ICO International Data Transfer Addendum (version B1.0, in force
  21 March 2022) is appended to the EU SCCs. UK GDPR references replace EU GDPR
  references; the competent authority is the ICO.
- **Switzerland** - the Swiss Annex applies. The FDPIC is the competent authority, and
  references to Member State law are read as references to Swiss law. The SCCs are
  extended to protect data of legal entities as required by the revised FADP.

## 3. EU-US Data Privacy Framework

Kestrel is **not** currently self-certified under the EU-US Data Privacy Framework.
Transfers rely on the SCCs plus the supplementary measures below. Self-certification is
under evaluation but no commitment date has been set. This is stated plainly rather than
implied.

## 4. Transfer Impact Assessment - conclusion

A TIA was completed on 28 January 2026 covering transfers to the US. Summary of
conclusions:

**Assessment of US law.** Kestrel assessed FISA 702 and EO 12333. Kestrel is not an
"electronic communications service provider" as defined in 50 U.S.C. 1881(b)(4) and has
never received a national security request of any kind, nor a National Security Letter,
nor any request under FISA. A warrant canary is not maintained; the absence of requests is
stated directly here instead.

**Supplementary measures in place:**

1. Encryption in transit (TLS 1.3) and at rest (AES-256-GCM) with keys held by Kestrel in
   AWS KMS, not by any sub-processor in plaintext form.
2. EU customers may elect the EU instance, keeping data within the EEA for all
   infrastructure subprocessors.
3. Enterprise customers may hold their own key material for the Snowflake layer through
   Tri-Secret Secure, so that layer cannot be read without customer participation.
4. A documented government-access response procedure requiring legal review, a challenge
   to overbroad or unlawful requests, and customer notification unless legally prohibited.
5. Transparency reporting: Kestrel publishes an annual count of government data requests
   received. The count for 2025 was **zero**.

**Residual risk:** assessed as low for customers electing the EU instance, and low to
moderate for EU customers electing the US instance, with the moderate rating driven by
theoretical rather than observed exposure.
