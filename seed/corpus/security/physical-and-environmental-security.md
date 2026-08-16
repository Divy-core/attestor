# Physical and Environmental Security Standard

**Document ID:** KD-SEC-014 · **Version:** 1.6 · **Owner:** Sofia Brenner, Head of IT
**Approved:** 12 January 2026 · **Next review:** 12 January 2027
**Maps to:** SOC 2 CC6.4, CC6.5 · ISO 27001:2022 A.7.1–A.7.14

## 1. Production infrastructure

Kestrel Data operates **no owned or leased data centre**. All production infrastructure
runs in AWS: `us-east-1` for the US instance and `eu-west-1` for the EU instance.
Physical and environmental controls for those facilities — perimeter security, biometric
access, CCTV, fire suppression, power redundancy, environmental monitoring, and media
destruction — are AWS's responsibility under the shared responsibility model.

Kestrel evidences these controls by reviewing the AWS SOC 2 Type II report and the AWS
ISO 27001 certificate annually. The most recent review was completed by the CISO on
27 January 2026 and is recorded in the vendor file under VRM-AWS-2026.

**No customer data is stored on Kestrel-controlled premises**, on removable media, or on
any device outside the AWS boundary, other than transiently in a support engineer's
browser session.

## 2. Offices

Kestrel occupies one office: 1401 Lavaca Street, Suite 210, Austin, TX 78701. The company
operates remote-first, and roughly 70% of personnel work from home in a typical week.

Building access is controlled by the landlord's card system; suite access requires a
Kestrel-issued badge provisioned by the Head of IT and revoked on the HRIS termination
event, in the same automated workflow that removes system access. Badge holders as of
1 February 2026: 96 employees, 4 contractors, 3 facilities staff.

## 3. Visitors

Visitors sign in at the building reception, are issued a dated visitor badge, and are
escorted at all times within the suite. The visitor log is retained for 12 months. No
visitor is admitted to the network; guests use a segregated wireless SSID with no route
to any internal or production system.

## 4. Clear desk and clear screen

Screens lock automatically after 5 minutes of inactivity, enforced by Kandji MDM policy
rather than by request. Printed material classified Confidential or above must be stored
in a locked cabinet when unattended and shredded on disposal via the cross-cut shredder in
the suite; the office holds no long-term paper records.

## 5. Home working

Personnel working from home are required to use a Kestrel-managed device with full-disk
encryption, to avoid working on customer data in public spaces, and to use a privacy
screen when travelling. Kestrel does not inspect home environments; the compensating
control is that no customer data is stored locally — access is through the browser to
systems inside the AWS boundary, and the DLP controls in KD-SEC-015 block local export.

## 6. Environmental controls in the office

The suite has no server room and no production equipment. Fire detection, suppression,
and power continuity are provided by the building. A loss of the Austin office has no
effect on service availability, which is the point of running no infrastructure there;
this was exercised during the 12–14 February 2025 winter storm closure with no customer
impact.

## 7. Equipment removal

Company equipment may be removed from the office freely, as befits a remote-first company.
Every device is enrolled in Kandji, encrypted, and remotely wipeable. Loss or theft must
be reported to `#security` within 4 hours; the device is wiped remotely and the event
recorded as a security incident. Three devices were reported lost or stolen in 2025, all
wiped successfully, none containing local customer data.
