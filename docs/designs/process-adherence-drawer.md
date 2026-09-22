# Rep Dashboard Process Adherence

Status: Production integration  
Date: 2026-09-21  
Related: SteelTrap `docs/reference/process-adherence-criteria.md`

## Goal

Add two scan-friendly adherence percentages to every sales-rep row:

- Pre-call = the equal-weight average of Pre-Call Loom and Pre-call text.
- Post-call = the equal-weight average of Next steps set, Next steps completed, and Post-call follow-up.

Clicking either percentage opens a right-side drawer with the percentage, completed count, and
eligible count for every step in that phase.

The Close baseline was validated locally and is now part of the production dashboard refresh. It
reads Close without changing it, enriches `data.json` before that file and its monthly archive are
written, and keeps the aggregate contract stable so the adapter can later move to SteelTrap's
database without changing the UI.

## Cohort and attribution

The cohort is the same calendar-month meeting cohort already used by the rep dashboard:

```text
month_start <= First Sales Call Booked Date < next_month_start
```

- Bounds are interpreted in `America/Los_Angeles`.
- Exclude leads currently marked Canceled by Lead, Lost, Disqualified, Outside US/Canada, or Do Not
  Contact, plus the same excluded funnels as the existing dashboard.
- One lead is one first-call opportunity.
- Attribute the lead to its current Lead Owner.
- Upcoming steps are neutral until their deadline or outcome is knowable.

## Signal contract

The local payload keeps SteelTrap's five stable signal keys so the future source swap does not
require a UI migration.

| Phase | Stable key | UI label | Local Close rule |
| --- | --- | --- | --- |
| Pre-call | `loom_usage` | Pre-Call Loom | Any non-empty Close note, email, or SMS content containing `loom.com` at or before the first-call deadline. No lower time bound or sender restriction. |
| Pre-call | `precall_text` | Pre-call text | A non-empty outbound SMS at or before the first-call deadline. |
| Post-call | `followup_task` | Next steps set | A dated Close task assigned to the current owner (or unassigned), open or complete; **or** a later meeting assigned to the owner (or unassigned) that is not canceled or declined. |
| Post-call | `followup_completed` | Next steps completed | A qualifying task completed after the held first-call anchor; **or** a later meeting whose Close status is `completed`. |
| Post-call | `recap_email` | Post-call follow-up | A sent outbound email **or outbound SMS** from the held first-call anchor through 24 hours after it. |

The first-call deadline is the earliest non-canceled meeting on the recorded booked date, falling
back to 11:59:59 PM Pacific on the recorded date. The existing `First Call Show Up (Opp)` field
determines the first-call outcome. A no-show is eligible only for `followup_task`; the other two
post-call steps are neutral.

For each rep and step:

```text
step_pct = round(done / eligible * 100)
```

When `eligible = 0`, the percentage is `null` and the UI displays `—`. Headline phase percentages
are equal-weight averages of the available step percentages, not pooled-volume ratios:

```text
pre_call_pct  = round(mean(loom_usage_pct, precall_text_pct))
post_call_pct = round(mean(followup_task_pct, followup_completed_pct, recap_email_pct))
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
    "schema_version": 1,
    "source": "close_crm",
    "period": { "month": "2026-09", "basis": "first_sales_call_booked_date" },
    "cohort": { "included": 0, "excluded": 0 }
  },
  "reps": [
    {
      "name": "Example Rep",
      "rep_owner_id": "user_123",
      "adherence": {
        "pre_call_pct": 85,
        "post_call_pct": 74,
        "steps": {
          "loom_usage": { "pct": 80, "done": 16, "eligible": 20 },
          "precall_text": { "pct": 90, "done": 18, "eligible": 20 },
          "followup_task": { "pct": 90, "done": 18, "eligible": 20 },
          "followup_completed": { "pct": 70, "done": 7, "eligible": 10 },
          "recap_email": { "pct": 62, "done": 5, "eligible": 8 }
        }
      }
    }
  ]
}
```

Only aggregate rep-level data enters the dashboard or preview JSON. Lead IDs, names, message
bodies, task text, and evidence rows remain in memory and are never written to the public artifact.

## UI behavior

- Add a fourth full-width Process Adherence board below the existing three boards.
- Pre-call and Post-call are top-level column groups. Each group contains an Average column followed
  by one aligned column for every contributing step percentage.
- Clicking a phase average opens a right-side modal drawer for that rep and focuses its close
  button.
- Escape and backdrop click close it; focus is trapped while open and returns to the triggering
  button afterward.
- The drawer explains that the phase score is an equal average of available step percentages.
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
- The stable keys `followup_task`, `followup_completed`, and `recap_email` intentionally retain
  their SteelTrap names even though the displayed rules are now broader.
