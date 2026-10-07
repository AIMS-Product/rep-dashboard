# Rep Dashboard Process Adherence

Status: Production integration  
Date: 2026-09-21  
Related: SteelTrap `docs/reference/process-adherence-criteria.md`

## Goal

Add two scan-friendly adherence percentages to every sales-rep row:

- Pre-call = the equal-weight average of Pre-Call Loom, Pre-call text, and Day-of confirmation.
- Post-call = the equal-weight average of Task created, FU meeting created, and Recap email sent.

Clicking either percentage opens a right-side drawer with the percentage, completed count, and
eligible count for every step in that phase.

The Close baseline was validated locally and is now part of the production dashboard refresh. It
reads Close without changing it, enriches `data.json` before that file and its monthly archive are
written, and keeps the aggregate contract stable so the adapter can later move to SteelTrap's
database without changing the UI.

## Cohort and attribution

The cohort uses the rep dashboard's calendar-month First Sales Call Booked Date field:

```text
month_start <= First Sales Call Booked Date < next_month_start
```

- Bounds are interpreted in `America/Los_Angeles`.
- Exclude leads currently marked Canceled by Lead, Disqualified, Outside US/Canada, or Do Not
  Contact, plus the same excluded funnels as the existing dashboard. Include Lost leads so a
  call's process record remains in the cohort after its outcome changes.
- One lead is one first-call opportunity.
- Attribute a lead to the rep assigned to its first same-day meeting when Close identifies one;
  use current Lead Owner only when no meeting rep can be identified.
- Upcoming steps are neutral until their deadline or follow-up window closes; post-call scoring does
  not depend on the show-up outcome.
- Keep Closed/Won leads visible in the cohort, but mark all post-call cells Exempt. Exempt cells do
  not count in step or phase percentages; pre-call behavior remains scored.
- Keep Lost leads visible and mark all post-call cells Exempt. Pre-call behavior remains scored.

## Signal contract

The local payload keeps the five existing SteelTrap signal keys and adds one Close-specific
timed text signal for local review. The new key requires a future source mapping.

| Phase | Stable key | UI label | Local Close rule |
| --- | --- | --- | --- |
| Pre-call | `loom_usage` | Pre-Call Loom | Any active Close note or email, or a Close SMS from the current Lead Owner, containing `loom.com` at or before the first-call deadline. No lower time bound or content-quality requirement. |
| Pre-call | `precall_text` | Pre-call text | A non-empty sent outbound SMS from the current Lead Owner at or before the first-call deadline. |
| Pre-call | `day_of_confirmation_text` | Day-of confirmation | A non-empty sent outbound SMS, or an outbound call lasting at least 45 seconds, whose Close `user_id` matches the lead's current Lead Owner and which occurs on the scored first call's Pacific calendar date before the meeting start. The same SMS may also satisfy Pre-call text. |
| Post-call | `task_created` | Task created | Once the scheduled first-call anchor has passed, a dated Close task explicitly assigned to the credited closer, whose due date is at or after the call or which was created after the call. A qualifying task counts once created, whether open or complete. Show-up outcome does not affect eligibility. |
| Post-call | `fu_meeting_created` | FU meeting created | Once the scheduled first-call anchor has passed, a later meeting for the lead that is not canceled or declined, regardless of its assigned user or meeting outcome. Show-up outcome does not affect eligibility. |
| Post-call | `recap_email` | Recap email sent | A sent outbound email, or a sent outbound SMS from the current Lead Owner, from the scheduled first-call anchor through 24 hours after it. A missing message stays neutral until the 24-hour window closes. Show-up outcome does not affect eligibility. |

For a lead currently in Close's Closed / Won or Lost status, all post-call steps are exempt from
scoring. The lead remains visible in the monthly and daily breakdown under **Exempt**. Exempt cells
are not eligible, do not affect the step percentage, and are omitted from the phase's equal-weight
average. Pre-call cells remain scored as usual. This uses the lead's status in the fixed extract;
Close does not provide the status-transition timestamp needed to reconstruct when it became Closed /
Won or Lost. Leads in excluded terminal statuses such as Do Not Contact remain excluded from the
cohort as before.

The pre-call deadline is the earliest non-canceled meeting on the recorded booked date. When no
such meeting exists and the show outcome is unknown, use the earliest later non-canceled meeting;
when the outcome is Yes or No, or there is no later meeting, fall back to 11:59:59 PM Pacific on the
recorded booked date. Post-call steps use a separate show-independent anchor: the earliest
non-canceled same-day meeting, otherwise the booked-date fallback. Later meetings count as
next-step evidence and do not delay this anchor. A missing, Yes, or No show outcome does not gate
post-call scoring.

For task-based next steps, the task must be explicitly assigned to the rep credited with the first call. A later meeting can be assigned to anyone and still counts, provided it is not canceled or declined. All SMS evidence used by these steps must match the lead's current Lead Owner in Close `user_id`.
This applies to SMS used for Loom evidence, Pre-call text, Day-of confirmation, and post-call follow-up. The outbound call alternative for Day-of confirmation must also match the current Lead Owner.
Outbound text steps require a sent outbound SMS; Loom evidence only requires an active record.
Email and note evidence keeps its existing sender rules. Day-of confirmation becomes eligible once
the meeting start has passed and stays neutral when the first same-day meeting or owner is missing.
Its SMS or call evidence must be on that Pacific calendar date and before the meeting begins. Using
the current owner means a later owner transfer can change how historical activities are scored; this
preview does not reconstruct ownership at the time of the meeting.

For sent email and SMS evidence, `date_sent` is the timestamp used when Close provides it;
`activity_at` is the fallback. Old overdue tasks that existed before the first call do not satisfy
the post-call next-step signals.

For each rep and step:

```text
step_pct = round(done / eligible * 100)
```

When `eligible = 0`, the percentage is `null` and the UI displays `—`. Headline phase percentages
are equal-weight averages of the available step percentages, not pooled-volume ratios:

```text
pre_call_pct  = round(mean(loom_usage_pct, precall_text_pct, day_of_confirmation_text_pct))
post_call_pct = round(mean(task_created_pct, fu_meeting_created_pct, recap_email_pct))
```

## Architecture

```text
Close (read only)
  -> scripts/fetch_data.py
  -> scripts/build_adherence_preview.py (imported aggregate adapter)
  -> scripts/adherence_rules.py (pure scoring rules)
  -> data.json + monthly archive
  -> index.html
  -> Process Adherence board + accessible right drawer
```

The adapter adds the following contract to the dashboard payload. Its command-line entry point can
still write `data.preview.json` for local verification:

```json
{
  "adherence_meta": {
    "schema_version": 8,
    "source": "close_crm",
    "period": { "month": "2026-09", "basis": "first_sales_call_booked_date" },
    "cohort": { "included": 0, "excluded": 0, "includes_lost": true,
      "attribution_counts": { "meeting_rep": 0, "owner_fallback": 0 } }
  },
  "reps": [
    {
      "name": "Example Rep",
      "rep_owner_id": "user_123",
      "adherence": {
        "pre_call_pct": 84,
        "post_call_pct": 74,
        "steps": {
          "loom_usage": { "pct": 80, "done": 16, "eligible": 20 },
          "precall_text": { "pct": 90, "done": 18, "eligible": 20 },
          "day_of_confirmation_text": { "pct": 85, "done": 17, "eligible": 20 },
          "task_created": { "pct": 90, "done": 18, "eligible": 20 },
          "fu_meeting_created": { "pct": 70, "done": 7, "eligible": 10 },
          "recap_email": { "pct": 62, "done": 5, "eligible": 8 }
        }
      }
    }
  ]
}
```

Only aggregate rep-level data enters the production dashboard JSON. The ignored local preview
also includes eligible lead names, IDs, booked dates, and Close URLs for the Completed and Missed
lists. Message bodies, task text, and activity rows are not serialized.
`rep_owner_id` identifies the displayed rep row; individual lead scores follow meeting attribution
when available.

## UI behavior

- Add a fourth full-width Process Adherence board below the existing three boards.
- Pre-call and Post-call are top-level column groups. Each group contains an Average column followed
  by one aligned column for every contributing step percentage.
- Clicking a phase average opens a right-side modal drawer for that rep and focuses its close
  button.
- Escape and backdrop click close it; focus is trapped while open and returns to the triggering
  button afterward.
- The drawer explains that the phase score is an equal average of available step percentages.
- Local lead lists show the exact scored first-call time, since a lead may also have later meetings.
- Reduced-motion preferences disable drawer and page entrance transitions.

## Verification gates

1. Unit-test deadlines, eligibility, OR conditions, 24-hour boundary behavior, no-shows, task and
   meeting completion, null percentages, and equal-weight phase averaging.
2. Run the Close baseline for the dashboard month and inspect cohort/exclusion/API counts.
3. Assert every step has `0 <= done <= eligible`, percentages are bounded, and no customer data is
   serialized.
4. Render the local preview at desktop and mobile widths and exercise mouse and keyboard drawer
   behavior.
5. After acceptance, enrich the production payload before writing `data.json` and its archive.

## Known baseline limitations

- Close's first-call booked field is date-only, so the matching same-day meeting provides the time;
  otherwise the deadline falls back to end-of-day.
- The local calculation uses current Close state. It is a baseline snapshot, not a historical
  event-sourced reconstruction.
- The post-call signals are three independent requirements: a qualifying task, a later follow-up
  meeting, and a sent recap email or owner-sent SMS. The old task-or-meeting composite and task /
  meeting completion signal have been removed.
