# Dashboard reporting definitions

## Calls Occurred reporting (November 2026)

The reporting selector is pinned to MTD V2 v2.8, including complete meeting histories,
30-day repeat credit, title mappings, VP owner scope, source attribution and cancellations.
Calls Occurred are accepted calls scheduled in the elapsed Pacific reporting window
with the meeting's native **Completed** or **No Show** outcome. Blank, unresolved,
rescheduled and future meetings do not enter this denominator. Shown uses native
Completed; the lead's First Call Show Up field is not consulted.

Qualified counts distinct currently qualified leads in the occurred cohort, retaining
native group deduplication. This release does not reset qualification or write to Close.
Show, qualification and primary close rates divide by occurred calls; zero calls is
undefined. Won leads/opportunities and revenue retain the dashboard's native date-won,
owner, currency and deduplication definitions, independent of meeting dates. Process
adherence retains its existing scheduled-date cohort and scoring.

All CRM requests are read-only, using existing credentials. Private input snapshots
and reconciliation rows are not committed. Existing finalized snapshots retain their
original definitions; live/new reports carry `calls-occurred.v1` metadata. To roll back,
revert the release code commit and rerun the dashboard's existing refresh workflow;
do not restore an old data snapshot over newer source results.

The existing **Booked** column now shows Calls Occurred. A separate creation-date
bookings metric is available only on MTD V2. The live scorecard is month-to-date.

Daily/Closer EOD generation is unchanged. The pinned selector source and provenance
live in `scripts/mtd_v28/` and must be updated together.
