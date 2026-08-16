# Data Subject Rights Procedure

**Document ID:** KD-LEG-007 · **Version:** 2.3 · **Owner:** Priya Raghunathan, DPO
**Approved:** 20 January 2026 · **Next review:** 20 January 2027
**Maps to:** GDPR Articles 12, 15–22, 28(3)(e) · CCPA/CPRA §1798.100–130

## 1. Which rights, and against whom

For customer content, Kestrel is the **processor** and the customer is the controller. A
data subject who approaches Kestrel directly about customer content is redirected to the
relevant customer without undue delay and, where Kestrel can identify the customer, within
**3 business days**. Kestrel does not respond substantively on the controller's behalf.

For data Kestrel controls itself — prospect and marketing contacts, personnel data, and
the account identity data of the individuals who administer a tenant — Kestrel responds
directly as controller.

## 2. Assisting customers as processor

**Kestrel commits to assist a customer with a data subject request within 5 business days
of a written request**, which is the commitment in the DPA (KD-LEG-001 §6). Assistance
means one of three things:

1. Pointing the customer at the self-service tooling that lets them do it themselves,
   which is the fastest route and covers the majority of requests;
2. Producing an export of the identified data subject's records where the customer cannot
   isolate them; or
3. Executing a deletion within the tenant where the customer directs it.

Fifty-eight assistance requests were received during 2025, with a measured median
turnaround of **2 business days** and a maximum of 5.

## 3. Self-service capability

The tenant administrator console supports, without involving Kestrel support:

* search for an end user by email address or external identifier;
* export of that user's account identity data and product usage events as JSON or CSV;
* deletion of an identified end user, which removes account identity data immediately and
  purges associated usage events within 30 days;
* full-tenant export of all customer-uploaded datasets in their original format.

## 4. Access requests (Article 15)

Where Kestrel acts as controller, an access request is acknowledged within 5 business days
and answered within **30 calendar days**, extendable by a further 60 days for complex
requests with notice to the data subject. Identity is verified through the requester's
registered email address plus one additional factor; where identity cannot be established,
the request is refused with reasons.

Eleven controller-side access requests were received during 2025, all answered within 30
days.

## 5. Erasure (Article 17)

Erasure of controller-side data is completed within 30 calendar days. Erasure of
processor-side data is executed on customer instruction; deletion from live systems is
immediate, and **encrypted backups containing the deleted data expire on the ordinary
backup rotation within 35 days**. Kestrel does not selectively edit backups, and says so:
surgical deletion inside an encrypted backup set is not achievable without restoring and
re-writing the whole set, which creates more risk than it resolves.

Records subject to a legal hold are exempt until the hold is lifted (KD-LEG-002 §5).

## 6. Portability (Article 20)

Customer content is exportable in machine-readable form at any time, by the customer,
without asking Kestrel: JSON and CSV through the console, and the same through the public
API. Datasets uploaded by the customer are returned in the format in which they were
supplied.

## 7. Objection, restriction, and automated decision-making

Objection and restriction requests concerning customer content are directed to the
controller. Kestrel performs no automated decision-making producing legal or similarly
significant effects, so Article 22 is not engaged; the machine learning features in the
product are described in KD-SEC-019 §2.

## 8. California and other US state rights

Kestrel acts as a **service provider** under the CCPA/CPRA in respect of customer content,
and the DPA contains the required service provider terms: no sale, no sharing for
cross-context behavioural advertising, no retention or use outside the business purpose,
and no combining with data from other sources. The same terms are applied for the Virginia,
Colorado, Connecticut, Texas, and Utah statutes.

Consumer rights requests received directly are routed as in §1. Kestrel **does not sell
personal data**, does not share it for cross-context behavioural advertising, and operates
no "Do Not Sell or Share" link because there is nothing to opt out of.

## 9. Records

Every request, its route, and its resolution date are recorded in the DSR register held by
the DPO and retained for three years. The register is available to auditors and, in
redacted form, to customers exercising their audit rights under the DPA.
