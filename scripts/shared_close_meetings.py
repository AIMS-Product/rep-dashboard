"""Index the private Close meeting catalog collected for this dashboard release."""

from collections import defaultdict
import json
from pathlib import Path


class SharedCloseMeetings:
    def __init__(self, rows):
        self.rows = rows
        self.by_lead = defaultdict(list)
        for meeting in rows:
            if lead_id := meeting.get("lead_id"):
                self.by_lead[lead_id].append(meeting)

    @classmethod
    def read(cls, path: Path):
        with path.open() as source:
            rows = json.load(source)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("Invalid shared Close meeting catalog")
        return cls(rows)

    def for_leads(self, lead_ids):
        return {lead_id: self.by_lead[lead_id] for lead_id in lead_ids if lead_id in self.by_lead}
