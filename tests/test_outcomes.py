import unittest, json, re, hashlib, csv
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from mtd_v28.outcome_core import *


class OccurredRulesTests(unittest.TestCase):
    def row(self, **extra):
        return {'meeting_id': 'meeting', 'lead_id': 'lead', 'channel': 'YouTube', 'owner': 'Scott Seymour',
                'lead_name': 'Fixture', 'showed': True, 'qualified': True, 'outcome': 'Completed', 'status': 'completed',
                'scheduled_date_pt': '2026-11-02', **extra}

    def dataset(self, rows):
        return {'rows': rows, 'totals': {}, 'source_cutoff': '2026-11-05T11:46:02-08:00', 'period_start': '2026-11-01'}

    def test_native_outcome_is_required_and_completed_status_is_not_a_show(self):
        rows = [self.row(meeting_id='show'), self.row(meeting_id='miss', lead_id='miss', outcome='No Show', showed=False),
                self.row(meeting_id='blank', outcome='No outcome'), self.row(meeting_id='cancel', status='canceled'),
                self.row(meeting_id='other', outcome='Rescheduled')]
        data = occurred_dataset(self.dataset(rows))
        self.assertEqual({r['meeting_id'] for r in data['rows']}, {'show', 'miss'})
        self.assertEqual(data['totals']['occurred_calls'], data['totals']['showed'] + data['totals']['no_shows'])

    def test_qualification_uses_only_occurred_leads_without_resetting_flags(self):
        rows = [self.row(), self.row(meeting_id='second', lead_id='lead'), self.row(meeting_id='blank', lead_id='blank', outcome='No outcome')]
        data = occurred_dataset(self.dataset(rows))
        self.assertEqual(data['totals']['qualified'], 1)
        self.assertTrue(all(r['qualified'] for r in rows))

    def test_zero_occurred_calls_keep_wins_and_use_undefined_rates(self):
        previous = {'reps': [{'name': 'Scott Seymour', 'booked': 5, 'deals': 1, 'revenue': 100}],
                    'total_booked': 5, 'total_deals': 1, 'total_revenue': 100, 'alignment_metadata': {}}
        scheduled = self.dataset([self.row(outcome='No outcome')])
        candidate = apply_occurred_payload(previous, occurred_dataset(scheduled), scheduled)
        self.assertEqual(candidate['total_deals'], 1)
        for key in ('team_show_rate', 'team_qualification_rate', 'team_close_rate'):
            self.assertIsNone(candidate[key])
        self.assertIsNone(candidate['reps'][0]['revenue_per_occurred'])

    def test_no_show_qualification_is_explicit_current_flag_not_inferred_attendance(self):
        data = occurred_dataset(self.dataset([self.row(outcome='No Show', showed=False)]))
        self.assertEqual(data['totals']['showed'], 0)
        self.assertEqual(data['totals']['qualified'], 1)



class ReportingReaderTests(unittest.TestCase):
    def test_identical_reads_share_one_source_and_return_independent_copies(self):
        from unittest.mock import Mock
        get = Mock(return_value={'data': [{'id': 'one'}]})
        reader = ReportingReader(get, '2026-11-01', datetime(2026,11,7,tzinfo=PACIFIC))
        first = reader.get('/user/', {'_limit':100})
        first['data'].clear()
        self.assertEqual(reader.get('user/', {'_limit':100})['data'], [{'id':'one'}])
        get.assert_called_once_with('user/', {'_limit':100})

    def test_source_errors_fail_closed(self):
        from unittest.mock import Mock
        reader = ReportingReader(Mock(side_effect=RuntimeError('source unavailable')), '2026-11-01', datetime(2026,11,7,tzinfo=PACIFIC))
        with self.assertRaisesRegex(RuntimeError, 'source unavailable'): reader.get('user/')
        with self.assertRaisesRegex(RuntimeError, 'source unavailable'): reader.get('user/')

    def test_naive_cutoff_is_rejected(self):
        with self.assertRaises(ValueError): ReportingReader(None, '2026-11-01', datetime(2026,11,7))
