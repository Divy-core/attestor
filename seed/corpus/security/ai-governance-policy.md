# AI Governance Policy

**Document ID:** KD-SEC-019 · **Version:** 1.3 · **Owner:** Marcus Oyelaran, CISO
**Approved:** 3 February 2026 · **Next review:** 3 August 2026
**Maps to:** SOC 2 CC1.4, CC6.1, CC9.2 · ISO 27001:2022 A.5.10, A.5.19 · ISO/IEC 42001 (aligned, not certified)

## 1. Status

**Kestrel Data maintains a documented AI governance policy** — this document. It is
reviewed twice yearly rather than annually, because the tooling and the regulatory
position both move faster than an annual cycle can track. Kestrel is **not** certified to
ISO/IEC 42001; the policy is aligned with its structure so that certification is available
later without a rewrite.

Accountability sits with the CISO. Model-level decisions inside the product are made by
the VP Engineering under the review process in §5.

## 2. Machine learning inside Kestrel Insight

Kestrel Insight incorporates two machine learning features, both of which process customer
data:

| Feature | Purpose | Model | Hosting |
|---|---|---|---|
| Anomaly highlighting | Flags outliers in a customer's own analytics datasets | Gradient-boosted trees, trained by Kestrel | In-account, `us-east-1` / `eu-west-1` |
| Natural-language query | Translates a plain-English question into a query over the customer's own schema | Third-party foundation model via AWS Bedrock | AWS Bedrock, same region as the tenant |

Both operate strictly within one tenant's boundary. Neither is used to make a decision
producing legal or similarly significant effects concerning an individual, so Article 22
of the GDPR is not engaged; this position is recorded in the ROPA (KD-LEG-004).

## 3. Customer content and model training

**Kestrel does not use customer content to train, fine-tune, or improve any model, whether
its own or a third party's.** This is a contractual commitment in the DPA and is enforced
technically: the Bedrock invocation sets model-invocation logging off and uses no
customer-content-retaining configuration, and the anomaly-highlighting model is trained
per tenant on that tenant's data only, with the artefact stored inside the tenant boundary
and destroyed on termination alongside the rest of the tenant's data.

## 4. Approved generative AI tools for internal use

Personnel may use generative AI tools **only** from the approved list, and **only** with
information classified Internal or Public. Restricted or Confidential information —
including any customer content — must not be entered into any generative AI tool.

Approved as of 1 February 2026:

* GitHub Copilot Business (code completion; telemetry and code-snippet retention disabled by tenant policy)
* Anthropic Claude, Kestrel enterprise workspace (zero-retention configuration)
* Google Gemini within Google Workspace, under the Workspace data processing terms

Everything else — including personal accounts on the same products — is prohibited. Use of
personal accounts on a managed device is blocked at the network layer by Cloudflare
Gateway. Two policy violations were recorded in 2025; both involved a personal ChatGPT
account with Internal-classified text and resulted in retraining rather than disciplinary
action.

## 5. Approving a new AI feature or tool

A new AI feature or tool requires an assessment covering: what data it processes and at
what classification, where the processing occurs, whether the provider trains on inputs,
retention, the effect on the ROPA and the subprocessor list, human oversight, and failure
behaviour. The assessment is reviewed by the CISO and DPO jointly and recorded in the
Security Steering Group minutes. Four assessments were completed during 2025; one tool was
rejected because the provider would not contractually exclude training on inputs.

## 6. Human oversight

Output from the natural-language query feature is always shown alongside the generated
query, so the customer can see what was actually run. No AI feature takes an action on a
customer's data without the customer initiating it, and no AI feature can write to or
delete customer data.

## 7. Transparency to customers

The use of AI features, the model providers involved, and the regions of processing are
disclosed in the subprocessor list (KD-LEG-006) and in the product documentation. AWS
Bedrock is listed as a subprocessor. Customers may disable both AI features at the tenant
level, and 11 customers had done so as of 1 February 2026.
