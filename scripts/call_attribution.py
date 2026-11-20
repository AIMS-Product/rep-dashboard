"""Shared call-closer attribution for native metrics and process adherence."""
from typing import Any


def scoring_rep_for_meeting(
    first_meeting: dict[str, Any] | None,
    current_owner_id: str | None,
    visible_rep_ids: set[str],
) -> tuple[str | None, str]:
    """Choose the meeting's rep, falling back to current owner only without a known rep."""
    if first_meeting:
        primary = str(first_meeting.get("user_id") or "")
        assigned = {str(value) for value in first_meeting.get("users") or [] if value}
        if primary:
            assigned.add(primary)
        if primary in visible_rep_ids:
            return primary, "meeting_rep"
        visible_assigned = assigned & visible_rep_ids
        if len(visible_assigned) == 1:
            return next(iter(visible_assigned)), "meeting_rep"
        if assigned:
            return None, "meeting_rep_not_visible" if not visible_assigned else "ambiguous_meeting_rep"
    if current_owner_id in visible_rep_ids:
        return current_owner_id, "owner_fallback"
    return None, "not_visible_rep"

