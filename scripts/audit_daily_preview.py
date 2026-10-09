#!/usr/bin/env python3
"""Independently reconcile a private daily preview to one fixed Close extract."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

import audit_adherence_extract as adherence_audit


PACIFIC = adherence_audit.PACIFIC
BOOKED_FIELD = "cf_LFdYEQ6bsgp49YjZzefypDmdVx8iwuakWDSLPLpVrBq"
SHOW_FIELD = "cf_OPyvpU45RdvjLqfm8V1VWwNxrGKogEH2IBJmfCj0Uhq"
OWNER_FIELD = "cf_gOfS9pFwext58oberEegLyix8hZzeHrxhCZOVh3P3rd"
QUALIFIED_FIELD = "cf_ZDx7NBQaDzV1yYrFcBMzt6cIYj81dAcswpNN0CQzCPS"
BOOKING_EXCLUDED_STATUSES = {
    "stat_hWIGHjzyNpl4YjIFSFz3VK4fp2ny10SFJLKAihmo4KT",
    "stat_YV4ZngDB4IGjLjlOf0YTFEWuKZJ6fhNxVkzQkvKYfdB",
}
BOOKING_EXCLUDED_FUNNELS = {"LTF - Quiz Funnel"}
OUTCOMES = {"completed": "Completed", "missed": "Missed", "exempt": "Exempt"}


def value(lead: dict, field_id: str, field_name: str = ""):
    for source in (lead, lead.get("custom") or {}):
        for key in (f"custom.{field_id}", field_id, field_name):
            if key and source.get(key) is not None:
                return source[key]
    return ""


def lead_id_from_url(url: str) -> str | None:
    parts = [part for part in urlparse(url).path.split("/") if part]
    return parts[1] if len(parts) == 2 and parts[0] == "lead" else None


def id_sets(rows: list[dict]) -> dict[str, set[str]]:
    result = defaultdict(set)
    for row in rows:
        lead_id = lead_id_from_url(str(row.get("url") or ""))
        if lead_id:
            result[str(row.get("rep") or "TEAM")].add(lead_id)
    return result


def run(extract_path: Path, preview_path: Path, out_dir: Path) -> dict:
    extract, preview = (json.loads(path.read_text()) for path in (extract_path, preview_path))
    meta = preview["daily_meta"]
    day = str(meta["date"])
    if extract.get("meta", {}).get("date") != day or not extract.get("meta", {}).get("complete"):
        raise ValueError("Preview date must match a complete fixed Close extract")
    leads = {str(row["id"]): row for row in extract["leads"]}
    process_extract = extract.get("process_extract") or extract
    if process_extract.get("meta", {}).get("cohort_method") not in {
        "qualifying_meeting_activity", "qualifying_meeting_activity_all_leads",
    }:
        raise ValueError("Daily adherence audit requires the qualifying meeting cohort")
    process_end = adherence_audit.pacific(process_extract["meta"]["ended_at"])
    process_leads = {str(row["id"]): row for row in process_extract["leads"]}
    users = extract["users"]
    dashboard_reps = {row["name"]: row for row in preview.get("reps", [])}
    ids_by_name = {name: user_id for user_id, name in users.items()}
    visible_ids = {
        ids_by_name[name] for name, rep in dashboard_reps.items()
        if name in ids_by_name and not rep.get("exclude_meetings") and not rep.get("is_manager")
    }

    # Independently recalculate the three daily lead metrics from the Close lead records.
    expected_metric_rows = {key: [] for key in ("booked", "shown", "qualified")}
    metric_exclusions = Counter()
    raw_dates = Counter()
    for lead_id, lead in leads.items():
        booked = str(value(lead, BOOKED_FIELD, "First Sales Call Booked Date") or "")[:10]
        raw_dates[booked] += 1
        if booked != day:
            metric_exclusions["booked_date_mismatch"] += 1
            continue
        if lead.get("status_id") in BOOKING_EXCLUDED_STATUSES:
            metric_exclusions["status"] += 1
            continue
        funnel = str(value(lead, adherence_audit.FIELDS["funnel"][0], "Funnel Name DEAL (Opp)") or "").strip()
        if funnel in BOOKING_EXCLUDED_FUNNELS:
            metric_exclusions["funnel"] += 1
            continue
        owner = adherence_audit.owner_id(lead, users)
        rep_name = users.get(owner, "") if owner else str(value(lead, OWNER_FIELD, "Lead Owner") or "Unknown")
        if rep_name not in dashboard_reps or dashboard_reps[rep_name].get("exclude_meetings"):
            metric_exclusions["non_dashboard_owner"] += 1
            continue
        item = {"id": lead_id, "name": lead.get("display_name") or lead.get("name") or "Unnamed lead",
                "rep": rep_name, "booked_date": day}
        expected_metric_rows["booked"].append(item)
        if str(value(lead, SHOW_FIELD, "First Call Show Up (Opp)") or "").strip().lower() == "yes":
            expected_metric_rows["shown"].append(item)
        if str(value(lead, QUALIFIED_FIELD, "Qualified (Opp)") or "").strip().lower() == "yes":
            expected_metric_rows["qualified"].append(item)

    actual_team_lists = preview.get("daily_metric_leads", {})
    metric_mismatches = []
    metric_rep_summary = {}
    expected_team_counts = {}
    for metric, expected_rows in expected_metric_rows.items():
        expected_ids = {row["id"] for row in expected_rows}
        actual_ids = {lead_id_from_url(str(row.get("url") or "")) for row in actual_team_lists.get(metric, [])}
        actual_ids.discard(None)
        expected_team_counts[metric] = len(expected_ids)
        if expected_ids != actual_ids:
            metric_mismatches.append({"scope": "team", "metric": metric,
                                      "missing_ids": sorted(expected_ids - actual_ids),
                                      "unexpected_ids": sorted(actual_ids - expected_ids)})
        expected_by_id = {row["id"]: row for row in expected_rows}
        actual_by_id = {lead_id_from_url(str(row.get("url") or "")): row
                        for row in actual_team_lists.get(metric, [])}
        for lead_id in sorted(expected_ids & actual_ids):
            actual = actual_by_id.get(lead_id, {})
            expected = expected_by_id[lead_id]
            for field in ("name", "rep", "booked_date"):
                if actual.get(field) != expected[field]:
                    metric_mismatches.append({"scope": "team", "metric": metric,
                                              "lead_id": lead_id, "field": field,
                                              "independent": expected[field], "preview": actual.get(field)})
        for rep_name in dashboard_reps:
            expected_rep_ids = {row["id"] for row in expected_rows if row["rep"] == rep_name}
            actual_rep_lists = (dashboard_reps[rep_name].get("daily_metric_leads") or {}).get(metric, [])
            actual_rep_ids = {lead_id_from_url(str(row.get("url") or "")) for row in actual_rep_lists}
            actual_rep_ids.discard(None)
            if expected_rep_ids != actual_rep_ids:
                metric_mismatches.append({"scope": rep_name, "metric": metric,
                                          "missing_ids": sorted(expected_rep_ids - actual_rep_ids),
                                          "unexpected_ids": sorted(actual_rep_ids - expected_rep_ids)})
            actual_by_id = {lead_id_from_url(str(row.get("url") or "")): row
                            for row in actual_rep_lists}
            for lead_id in sorted(expected_rep_ids & actual_rep_ids):
                expected = next(row for row in expected_rows if row["id"] == lead_id)
                actual = actual_by_id.get(lead_id, {})
                for field in ("name", "rep", "booked_date"):
                    if actual.get(field) != expected[field]:
                        metric_mismatches.append({"scope": rep_name, "metric": metric,
                                                  "lead_id": lead_id, "field": field,
                                                  "independent": expected[field], "preview": actual.get(field)})
            metric_rep_summary.setdefault(rep_name, {})[metric] = {
                "independent": len(expected_rep_ids),
                "preview": int(dashboard_reps[rep_name].get(metric) or 0),
                "lead_ids_match": expected_rep_ids == actual_rep_ids,
            }
            if len(expected_rep_ids) != metric_rep_summary[rep_name][metric]["preview"]:
                metric_mismatches.append({"scope": rep_name, "metric": metric,
                                          "independent_count": len(expected_rep_ids),
                                          "preview_count": metric_rep_summary[rep_name][metric]["preview"]})
    for rep_name in dashboard_reps:
        rep_metrics = metric_rep_summary[rep_name]
        expected_booked = rep_metrics["booked"]["independent"]
        expected_shown = rep_metrics["shown"]["independent"]
        expected_rate = round(expected_shown / expected_booked * 100, 1) if expected_booked else 0
        preview_rate = dashboard_reps[rep_name].get("show_rate")
        rep_metrics["show_rate"] = {"independent": expected_rate, "preview": preview_rate}
        if preview_rate != expected_rate:
            metric_mismatches.append({"scope": rep_name, "metric": "show_rate",
                                      "independent": expected_rate, "preview": preview_rate})
    team_counts = meta.get("booked_shown_qualified", {})
    for metric in ("booked", "shown", "qualified"):
        if team_counts.get(f"team_{metric}") != expected_team_counts[metric]:
            metric_mismatches.append({"scope": "team", "metric": metric,
                                      "independent_count": expected_team_counts[metric],
                                      "preview_count": team_counts.get(f"team_{metric}")})
    if team_counts.get("raw_leads") != len(leads):
        metric_mismatches.append({"scope": "team", "metric": "raw_leads",
                                  "independent_count": len(leads), "preview_count": team_counts.get("raw_leads")})
    expected_excluded_status = metric_exclusions["status"]
    expected_excluded_funnel = metric_exclusions["funnel"]
    for key, expected_value in (("excluded_status", expected_excluded_status),
                                ("excluded_funnel", expected_excluded_funnel)):
        if team_counts.get(key) != expected_value:
            metric_mismatches.append({"scope": "team", "metric": key,
                                      "independent_count": expected_value, "preview_count": team_counts.get(key)})
    expected_team_rate = round(expected_team_counts["shown"] / expected_team_counts["booked"] * 100, 1) if expected_team_counts["booked"] else 0
    preview_team_rate = round((team_counts.get("team_shown") or 0) / (team_counts.get("team_booked") or 1) * 100, 1) if team_counts.get("team_booked") else 0
    if expected_team_rate != preview_team_rate:
        metric_mismatches.append({"scope": "team", "metric": "show_rate",
                                  "independent": expected_team_rate, "preview": preview_team_rate})

    # Independently determine the scored call/credited rep and all six process outcomes.
    actual_results = {}
    for rep in preview.get("reps", []):
        for lead_result in (rep.get("adherence") or {}).get("lead_results", []):
            actual_results[str(lead_result["id"])] = (rep["name"], lead_result)
    records, excluded = [], []
    independent_by_rep = defaultdict(list)
    preview_by_rep = defaultdict(list)
    expected_result_ids = set()
    expected_attribution_counts = Counter()
    expected_cohorts = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))
    lead_record_mismatches = []
    for lead_id, lead in process_leads.items():
        booked, scope_reason, _ = adherence_audit.in_scope(lead, day, day)
        if scope_reason == "candidate" and not adherence_audit.active_first_meeting(
            process_extract, lead_id, booked
        ):
            scope_reason = "inactive_booked_meeting"
        if scope_reason != "candidate" or booked != day:
            excluded.append({"lead_id": lead_id, "lead_name": lead.get("display_name") or lead.get("name"),
                             "reason": scope_reason if scope_reason != "candidate" else "booked_date_mismatch"})
            continue
        owner = adherence_audit.owner_id(lead, users)
        meetings = adherence_audit.by_lead(process_extract, "meetings", lead_id)
        show = str(value(lead, SHOW_FIELD, "First Call Show Up (Opp)") or "")
        deadline, first = adherence_audit.deadline_for(booked, meetings, show)
        rep_id, attribution = adherence_audit.credited_rep(first, owner, visible_ids)
        if not rep_id or rep_id not in visible_ids:
            excluded.append({"lead_id": lead_id, "lead_name": lead.get("display_name") or lead.get("name"),
                             "reason": attribution})
            continue
        rep_name = users[rep_id]
        expected_result_ids.add(lead_id)
        expected_attribution_counts[attribution] += 1
        independent, classified_deadline, classified_first = adherence_audit.classify(
            lead, rep_id, booked, owner, process_extract, process_end
        )
        independent_by_rep[rep_id].append(independent)
        actual_pair = actual_results.get(lead_id)
        if actual_pair:
            actual_rep_name, actual_result = actual_pair
            actual_steps = actual_result.get("steps", {})
        else:
            actual_rep_name, actual_steps = None, {}
        if not actual_pair:
            lead_record_mismatches.append({"lead_id": lead_id, "field": "lead_result", "preview": "missing"})
        else:
            expected_scored_call = classified_deadline.isoformat() if classified_first and classified_deadline else ""
            for field, expected_value, actual_value in (
                ("rep", rep_name, actual_rep_name),
                ("attribution", attribution, actual_result.get("attribution")),
                ("booked_date", booked, actual_result.get("booked_date")),
                ("scored_call_at", expected_scored_call, actual_result.get("scored_call_at") or ""),
            ):
                if expected_value != actual_value:
                    lead_record_mismatches.append({"lead_id": lead_id, "field": field,
                                                   "independent": expected_value, "preview": actual_value})
        preview_by_rep[rep_id].append({
            step: {"result": actual_steps.get(step, "Neutral")} for step in adherence_audit.STEP_LABELS
        })
        for step, (phase, label) in adherence_audit.STEP_LABELS.items():
            expected = independent[step]["result"]
            if expected.lower() in OUTCOMES:
                expected_cohorts[rep_name][step][expected.lower()].add(lead_id)
            actual = actual_steps.get(step, "Neutral")
            support = list(independent[step]["support"])
            if classified_first:
                support.insert(0, adherence_audit.event("scored_first_call", classified_first, classified_deadline))
            records.append({
                "lead_id": lead_id,
                "lead_name": lead.get("display_name") or lead.get("name"),
                "credited_rep": rep_name,
                "preview_rep": actual_rep_name,
                "booked_date": booked,
                "scored_first_call_id": classified_first.get("id") if classified_first else None,
                "scored_first_call_at": classified_deadline.isoformat() if classified_deadline else None,
                "step": step,
                "phase": phase,
                "label": label,
                "independent": expected,
                "preview": actual,
                "matches": expected == actual and actual_rep_name == rep_name,
                "supporting_close_records": support,
                "rule_basis": independent[step]["basis"],
                "difference_explanation": "Matches independent result." if expected == actual and actual_rep_name == rep_name
                    else f"Expected {expected} for {rep_name}; preview shows {actual} for {actual_rep_name or 'no rep'}.",
            })

    expected_lead_count = len(expected_result_ids)
    actual_lead_count = len(actual_results)
    lead_record_mismatches.extend(
        {"lead_id": lead_id, "field": "lead_result", "independent": "included", "preview": "missing"}
        for lead_id in sorted(expected_result_ids - set(actual_results))
    )
    lead_record_mismatches.extend(
        {"lead_id": lead_id, "field": "lead_result", "independent": "not included", "preview": "present"}
        for lead_id in sorted(set(actual_results) - expected_result_ids)
    )
    drawer_mismatches = []
    for rep_name, rep in dashboard_reps.items():
        actual_cohorts = (rep.get("adherence") or {}).get("lead_cohorts", {})
        for step in adherence_audit.STEP_LABELS:
            for bucket in OUTCOMES:
                expected_ids = expected_cohorts[rep_name][step].get(bucket, set())
                actual_ids = {lead_id_from_url(str(row.get("url") or ""))
                              for row in actual_cohorts.get(step, {}).get(bucket, [])}
                actual_ids.discard(None)
                if expected_ids != actual_ids:
                    drawer_mismatches.append({"rep": rep_name, "step": step, "bucket": bucket,
                                              "missing_ids": sorted(expected_ids - actual_ids),
                                              "unexpected_ids": sorted(actual_ids - expected_ids)})

    cohort_meta = preview.get("adherence_meta", {}).get("cohort", {})
    adherence_meta_mismatches = []
    for key, expected_value in (("raw", len(process_leads)), ("included", expected_lead_count),
                                ("excluded", len(excluded))):
        if cohort_meta.get(key) != expected_value:
            adherence_meta_mismatches.append({"field": key, "independent": expected_value,
                                              "preview": cohort_meta.get(key)})
    if cohort_meta.get("attribution_counts") != dict(expected_attribution_counts):
        adherence_meta_mismatches.append({"field": "attribution_counts",
                                          "independent": dict(expected_attribution_counts),
                                          "preview": cohort_meta.get("attribution_counts")})

    adherence_mismatches = sum(not row["matches"] for row in records)
    rep_adherence_summary = {}
    rep_aggregate_mismatches = []
    for rep_name, rep in dashboard_reps.items():
        rep_id = ids_by_name.get(rep_name)
        expected = adherence_audit.aggregate(independent_by_rep.get(rep_id, []))
        actual = rep.get("adherence") or {}
        summary = {"lead_count": len(independent_by_rep.get(rep_id, [])),
                   "pre_call_pct": {"independent": expected["pre_call_pct"], "preview": actual.get("pre_call_pct")},
                   "post_call_pct": {"independent": expected["post_call_pct"], "preview": actual.get("post_call_pct")},
                   "steps": {}}
        for step, expected_step in expected["steps"].items():
            actual_step = actual.get("steps", {}).get(step, {})
            summary["steps"][step] = {field: {"independent": expected_step[field], "preview": actual_step.get(field)}
                                       for field in ("done", "eligible", "exempt", "pct")}
            for field in ("done", "eligible", "exempt", "pct"):
                if actual_step.get(field) != expected_step[field]:
                    rep_aggregate_mismatches.append({"rep": rep_name, "step": step, "field": field,
                                                     "independent": expected_step[field], "preview": actual_step.get(field)})
        for field in ("pre_call_pct", "post_call_pct"):
            if actual.get(field) != expected[field]:
                rep_aggregate_mismatches.append({"rep": rep_name, "field": field,
                                                 "independent": expected[field], "preview": actual.get(field)})
        rep_adherence_summary[rep_name] = summary

    counts = Counter(row["independent"] for row in records)
    outcomes_by_step = defaultdict(Counter)
    for row in records:
        outcomes_by_step[row["step"]][row["independent"]] += 1
    result = {
        "meta": {
            "date": day,
            "timezone": "America/Los_Angeles",
            "extract_started_at": extract["meta"]["started_at"],
            "extract_ended_at": extract["meta"]["ended_at"],
            "raw_leads": len(process_leads),
            "raw_metric_leads": len(leads),
            "included_leads": len(independent_by_rep) and sum(map(len, independent_by_rep.values())) or 0,
            "excluded_leads": len(excluded),
            "lead_step_cells_reviewed": len(records),
            "expected_lead_step_cells": sum(map(len, independent_by_rep.values())) * len(adherence_audit.STEP_LABELS),
            "result_cells": dict(counts),
            "metric_mismatches": len(metric_mismatches),
            "adherence_cell_mismatches": adherence_mismatches,
            "adherence_aggregate_mismatches": len(rep_aggregate_mismatches),
            "lead_record_mismatches": len(lead_record_mismatches),
            "drawer_cohort_mismatches": len(drawer_mismatches),
            "adherence_meta_mismatches": len(adherence_meta_mismatches),
            "excluded_lead_reasons": dict(Counter(row["reason"] for row in excluded)),
            "activity_counts": extract["meta"].get("activity_counts", {}),
            "task_count": extract["meta"].get("task_count"),
        },
        "metric_mismatches": metric_mismatches,
        "excluded_leads": excluded,
        "booked_shown_qualified": {"expected_team_counts": expected_team_counts,
                                   "preview_team_counts": {key: team_counts.get(f"team_{key}") for key in expected_team_counts},
                                   "team_show_rate": {"independent": expected_team_rate, "preview_from_counts": preview_team_rate},
                                   "rep_summary": metric_rep_summary},
        "adherence_mismatches": [row for row in records if not row["matches"]],
        "lead_record_mismatches": lead_record_mismatches,
        "drawer_cohort_mismatches": drawer_mismatches,
        "adherence_meta_mismatches": adherence_meta_mismatches,
        "adherence_by_step": records,
        "rep_adherence_summary": rep_adherence_summary,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "reconciliation.json").write_text(json.dumps(result, indent=2) + "\n")
    fields = ["lead_id", "lead_name", "credited_rep", "preview_rep", "booked_date",
              "scored_first_call_id", "scored_first_call_at", "step", "phase", "label",
              "independent", "preview", "matches", "supporting_close_records", "rule_basis",
              "difference_explanation"]
    with (out_dir / "lead-by-step.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in records:
            writer.writerow({**row, "supporting_close_records": json.dumps(row["supporting_close_records"], sort_keys=True)})
    lines = [
        f"# Private Daily Preview Reconciliation — {day}",
        "",
        f"Fixed Close extract: {extract['meta']['started_at']} to {extract['meta']['ended_at']} (America/Los_Angeles).",
        f"Coverage: {len(leads)} raw leads; {result['meta']['included_leads']} included; {len(excluded)} excluded; {len(records)} of {result['meta']['expected_lead_step_cells']} lead-step cells reviewed.",
        f"Daily metric cells: {len(dashboard_reps) * 3} rep counts plus three team totals and Show Rates; lead-list/count/rate mismatches: {len(metric_mismatches)}.",
        f"Adherence differences: {adherence_mismatches} lead-step cells; {len(rep_aggregate_mismatches)} rep aggregate fields; {len(lead_record_mismatches)} lead records; {len(drawer_mismatches)} drawer cohorts; {len(adherence_meta_mismatches)} cohort metadata fields. Results: {dict(counts)}.",
        "",
        "## Booked / Shown / Qualified recalculation",
        "",
        "| Rep | Booked | Shown | Qualified | Show Rate |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for rep_name, values in metric_rep_summary.items():
        lines.append(f"| {rep_name} | {values['booked']['independent']} | {values['shown']['independent']} | {values['qualified']['independent']} | {values['show_rate']['independent']}% |")
    lines.extend(["| **Team** | **{booked}** | **{shown}** | **{qualified}** | **{rate}%** |".format(**expected_team_counts, rate=expected_team_rate),
                  "", f"Independent team Show Rate: {expected_team_rate}% (recalculated from shown ÷ booked).", ""])
    def pct(value):
        return "—" if value is None else f"{value}%"
    lines.extend([
        "## Process adherence by rep",
        "",
        "| Rep | Pre avg | Loom | Pre-call text | Day confirmation | Post avg | Task created | FU meeting created | Recap email sent |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for rep_name, summary in rep_adherence_summary.items():
        steps = summary["steps"]
        lines.append(
            f"| {rep_name} | {pct(summary['pre_call_pct']['independent'])} "
            f"| {pct(steps['loom_usage']['pct']['independent'])} "
            f"| {pct(steps['precall_text']['pct']['independent'])} "
            f"| {pct(steps['day_of_confirmation_text']['pct']['independent'])} "
            f"| {pct(summary['post_call_pct']['independent'])} "
            f"| {pct(steps['task_created']['pct']['independent'])} "
            f"| {pct(steps['fu_meeting_created']['pct']['independent'])} "
            f"| {pct(steps['recap_email']['pct']['independent'])} |"
        )
    lines.extend([
        "",
        "## Lead-step outcome counts",
        "",
        "| Process step | Completed | Missed | Neutral | Exempt |",
        "| --- | ---: | ---: | ---: | ---: |",
    ])
    for step, (_, label) in adherence_audit.STEP_LABELS.items():
        values = outcomes_by_step[step]
        lines.append(f"| {label} | {values['Completed']} | {values['Missed']} | {values['Neutral']} | {values['Exempt']} |")
    lines.append(f"| **Total** | **{counts['Completed']}** | **{counts['Missed']}** | **{counts['Neutral']}** | **{counts['Exempt']}** |")
    lines.extend(["", "## Exclusions", ""])
    if excluded:
        lines.extend(f"- {row['lead_name']} (`{row['lead_id']}`): {row['reason']}" for row in excluded)
    else:
        lines.append("None.")
    lines.extend(["", "Each lead-step CSV row includes independent and preview results, scored call, supporting Close IDs/times, rule basis, and any explanation."])
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(result["meta"], indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract", type=Path, required=True)
    parser.add_argument("--preview", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run(args.extract, args.preview, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
