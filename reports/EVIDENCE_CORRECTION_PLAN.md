# Occurrence evidence correction plan

This change does not migrate, delete, re-score or correct any existing incident.
Existing pre-policy assessments and assessed historical incidents are held for
explicit review. Original statuses can therefore remain incorrect until reviewed.

## Records identified in the read-only inspection

* Incident 5, `Cyclone Storm query — Orissa`: stored CORROBORATED via R4.
  Its supporting evidence includes CWC river-flood alerts classified broadly as
  `Met`, and a preparedness headline, `brace for Cyclone Arnab`. These are not
  independent confirmations of cyclone occurrence. Other actual-event headlines
  must be reviewed separately; this finding does not prove no cyclone occurred.
  Identified evidence IDs: 17–20 (flood alerts), 21 (preparedness), 25 and 28
  (remaining cyclone-related headlines requiring locality/event review).
* Incident 6, `Flood query — Bhutan`: stored CORROBORATED at 33.0204804,
  75.6458366 (an Indian namesake), while evidence describes the country Bhutan.
  Warning headlines also entered support. Country, locality and event identity
  are inconsistent. The record must not be treated as an Indian occurrence.
  Identified evidence IDs: 37–44; 39–44 explicitly include warnings/alerts/fears.
* Incident 7, `Fire query — Delhi`: the question concerned a hotel but the stored
  supporting headlines describe a shoe-factory fire. Generic category/place
  retrieval lost that narrower subject. This task does not implement arbitrary
  entity matching; a reviewer must decide whether a separate incident is needed.
  Identified evidence IDs: 114–116 in the read-only inspection. A running old
  server can refresh these records; capture the approved snapshot before review.

## Separate correction action, requiring explicit approval

1. Export the three complete incident records, evidence, cached queries,
   assessments and any report/grid relations as an immutable review snapshot.
2. Review each evidence item against occurrence, hazard, named event, place,
   country and time. Keep original evidence rows, URLs and timestamps intact.
3. For #6, annotate the incorrect linkage. Create a correctly scoped record only
   if the product's India-only scope is deliberately changed; do not move a
   foreign occurrence onto an Indian grid cell.
4. For #5, assess actual cyclone occurrence independently of flood alerts and
   forecasts. Do not infer a new status merely by subtracting rejected items.
5. For #7, distinguish the hotel question from the factory occurrence.
6. Record reviewer, reason, previous/new status and explicit linkage corrections
   in audit history. Preserve earlier assessments. Retire affected query-cache
   entries through an approved, auditable action.

No part of this correction is executed by startup, migration, tests or this patch.
New query keys are policy-versioned so legacy cached matches are not reused.

## New evidence behavior

Warnings, forecasts, unknown occurrence language, missing/future publication
dates, unrelated hazards, unresolved/foreign geography, mismatched named storms
and out-of-window items remain context rather than supporting evidence. Modelled
weather remains context. Evidence snapshots are append-only, with validation
reasons; fusion accepts only eligible snapshots for the current incident identity.
Existing heuristic score weights are retained; no probabilities are invented.
New reports do not join incidents held for legacy review, and an unreviewed
legacy corroboration cannot promote a new report. Crowd consensus also excludes
forwarded warning/forecast descriptions and requires validated Indian geography.

Matching remains conservative and English text rules are incomplete. A rejected
or unresolved headline means insufficient evidence, never proof the event is false.
Country validation requires an unambiguous global geocoder response and fails
closed on failure. Exact normalized titles/URLs and normalized publisher groups
reduce duplicates; paraphrased syndication cannot be reliably detected by these
rules alone.
