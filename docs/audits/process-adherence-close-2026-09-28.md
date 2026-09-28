# Process adherence versus Close — September 28, 2026

## Scope and method

I read the deployed `data.json` from the Sales Performance Dashboard and recomputed September
process adherence from the Close API without changing Close or publishing customer-level evidence.
The first read used the deployed rules, then I inspected 80 recent leads and the full monthly task
and meeting cohort for rule defects. This audit covers the Process Adherence board.

The deployed snapshot generated at 1:04 PM PDT matched a fresh Close recomputation at 1:07 PM:
all 10 rep rows, both phase scores, all five step counts, and the 457-lead scored cohort agreed.
This establishes that the published aggregate accurately reflected its then-current code; it does
not establish that the old scoring rules were sound.

## Confirmed rule defects

- An old task with an actionable date before the first call could satisfy **Next steps set**.
  Across a 458-lead snapshot of the old cohort, 18 leads received credit solely from such a task;
  6 also received **Next steps completed** credit from those tasks. Close defines the task `date`
  as the date when the task becomes actionable.
- **Next steps completed** was eligible immediately after a shown call, even while the next task
  or meeting was still in the future. Missing follow-up messages were also scored before their
  24-hour window ended.
- Message timing used `activity_at` before `date_sent`, which could credit a message before it
  was actually sent if those fields diverged. No such divergence appeared in the 80-lead sample;
  the change is a boundary safeguard.
- The old cohort dropped leads once their status became Lost. The Close read showed 131 such
  September leads excluded. This creates outcome-based selection in a process metric.
- The old attribution used current Lead Owner. In the expanded cohort, 24 leads had a different
  rep assigned to the first same-day meeting. The user chose meeting-rep attribution.

## Corrections

- Include Lost leads in the process cohort; keep the existing canceled, disqualified, outside-area,
  do-not-contact, and funnel exclusions.
- Prefer the first same-day meeting's `user_id`; use its sole visible assigned user when the
  primary user is not a visible rep. Use current Lead Owner only if there is no identifiable
  meeting rep. Exclude meetings assigned only to reps outside this dashboard.
- Require a next-step task to be actionable at or after the call, or created after the call.
  Completion stays neutral until a qualifying task is due or a later meeting has ended, unless
  completion evidence already exists.
- Keep a missing post-call message neutral until its 24-hour window expires. Use `date_sent`
  when Close provides it for sent email and SMS evidence.

The new payload uses schema version 2 and records attribution counts and `includes_lost` under
`adherence_meta.cohort`. The dashboard footer labels the changed cohort only when that flag is
present, so an older `data.json` is not mislabeled during rollout.

## Full corrected recomputation

The published snapshot at 1:19 PM PDT and corrected read at 1:27 PM PDT were compared. Close may
change between those times. The corrected read found 651 raw leads, 596 scored, and 55 excluded;
559 scored leads used a meeting rep and 37 used the owner fallback. Every phase score recomputed
from its five step counts, and `raw = included + excluded`.

| Rep | Published pre | Corrected pre | Published post | Corrected post |
| --- | ---: | ---: | ---: | ---: |
| Luke Herman | 99% | 98% | 81% | 84% |
| Joe Vaughan | 78% | 79% | 55% | 73% |
| Eric Piccione | 72% | 72% | 50% | 69% |
| Shreya Bechra | 93% | 92% | 65% | 76% |
| Robin Perkins | 93% | 96% | 69% | 88% |
| Christian Hartwell | 78% | 78% | 68% | 80% |
| Oscar Pugh | 80% | 76% | 62% | 75% |
| Ariella Irvine | 58% | 59% | 71% | 78% |
| Scott Seymour | 100% | 100% | 85% | 87% |
| Joe Dysert | — | — | — | — |

Across the visible rows, **Next steps completed** changes from 122/234 to 146/151. The smaller
eligible denominator reflects the due-date rule: future next steps and absent next steps stay
neutral, while due next steps remain scored.

## Remaining source limitations

Close has no same-day meeting on 38 otherwise eligible September leads in the expanded cohort;
37 have a visible current owner and use the documented fallback, while one has no visible rep.
The first-call booked field is date-only, so those records use the end of the booked date for
timing. Close meeting assignment identifies the credited rep, but does not prove attendance.
This is a source-data approximation. The calculation also reads current lead status and
meeting/task state, so it is a current snapshot rather than a historical reconstruction.
The Loom and post-call message signals are evidence-presence checks: a `loom.com` reference or a
sent outbound email/SMS in the window counts without assessing the message's meaning or whether
it was authored by the credited rep. A content-quality rule would need a separate business rubric.

## Verification

- The final corrected calculation is read-only and keeps lead IDs, names, and message bodies out
  of the dashboard artifact.
- `python3 -m unittest discover -s tests` passes 23 tests.
- `git diff --check` passes.

## Close API references

- [Tasks and the actionable date](https://developer.close.com/api/resources/tasks)
- [Meeting assignment and status fields](https://developer.close.com/api/resources/activities/meetings/get)
- [Sent email timing](https://developer.close.com/api/resources/activities/emails/get)
- [Sent SMS timing](https://developer.close.com/api/resources/activities/sms/get)
