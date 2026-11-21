"""Load the private, accepted SteelTrap VP population for the shared publisher."""
from collections import defaultdict
from datetime import datetime
import hashlib
import json
import os


COMPANY = '88a28173-2a61-4cbb-92ea-f6b8be4592f3'
OFFER = 'value:Vendingpreneurs (VP)'
CONTRACT = 'steeltrap.vp-equivalent-bookings.v1'

GENERATION_SQL = """
select pub.publication_id, g.generation_id, g.source_cutoff_at, g.source_delta_version,
       g.source_evidence->'vp_appointment_scope' as policy,
       serving.lane2_nlb_generation_valid(g.generation_id) as valid
from app.lane2_configuration_control current_config
join serving.lane2_configuration_publications pub
  on pub.company_id=current_config.company_id and pub.publication_id=current_config.current_publication_id
join serving.lane2_new_leads_booked_v2_current current_bookings
  on current_bookings.company_id=pub.company_id and current_bookings.source_domain='crm'
 and current_bookings.provider='close'
join serving.lane2_new_leads_booked_generation_v2 g on g.generation_id=current_bookings.generation_id
 and g.company_id=current_bookings.company_id and g.source_domain='crm' and g.provider='close'
where current_config.company_id=%s::uuid
  and pub.output_manifest#>>'{native,new_leads_booked_generation_id}'=g.generation_id::text
  and pub.output_manifest#>>'{native,source_receipt_sha256}'=g.source_receipt_sha256
"""

MEMBERS_SQL = """
select close_activity_id as activity_id, booked_date_pt as booked_day, reporting_bucket as bucket,
       decision_evidence#>>'{vp_appointment_scope,appointment_closer}' as appointment_closer,
       decision_evidence->'reviewed_calendly_booking_origin' as reviewed_origin
from serving.lane2_new_leads_booked_decision_v2
where company_id=%s::uuid and generation_id=%s::uuid and source_domain='crm' and provider='close'
  and booked_date_pt >= %s::date and offer_bucket=%s and credit_count=1
  and disposition not in ('canceled','deleted') and reporting_bucket in ('Lane 2','Social DMs')
  and decision_evidence#>>'{vp_appointment_scope,activity_id}'=close_activity_id
  and decision_evidence#>>'{vp_appointment_scope,contract}'=%s
  and decision_evidence#>>'{vp_appointment_scope,included}'='true'
order by booked_date_pt, reporting_bucket collate "C", close_activity_id collate "C"
"""


def _bundle(generation, rows):
    policy = generation['policy']
    if not generation['valid'] or not policy or policy.get('contract') != CONTRACT:
        raise ValueError('SteelTrap has not accepted the VP appointment-closer policy')
    cutoff = generation['source_cutoff_at']
    if (datetime.fromisoformat(policy['source_cutoff']) != cutoff
            or policy['source_delta_version'] != generation['source_delta_version']
            or policy['company_id'] != COMPANY):
        raise ValueError('Published VP policy belongs to a different SteelTrap source frontier')
    members = []
    seen = set()
    months = defaultdict(list)
    reviewed = {}
    for row in rows:
        member = {key: row[key] for key in ('activity_id', 'booked_day', 'bucket', 'appointment_closer')}
        if not member['appointment_closer'] or member['activity_id'] in seen:
            raise ValueError('Certified VP appointment has a missing closer or duplicate meeting ID')
        seen.add(member['activity_id'])
        day = member['booked_day'].isoformat()
        member['booked_day'] = day
        members.append(member)
        months[day[:7]].append(member)
        if row['reviewed_origin']:
            reviewed[row['activity_id']] = row['reviewed_origin']
    fingerprint = hashlib.sha256(json.dumps(members, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if len(members) != policy['member_count'] or fingerprint != policy['member_sha256']:
        raise ValueError('Private VP membership differs from the native publication certificate')
    return {'contract': CONTRACT, 'source_cutoff': cutoff.isoformat(),
            'steeltrap_publication_id': str(generation['publication_id']),
            'months': dict(months), 'reviewed_booking_origins': reviewed}


def read_accepted_bundle():
    import psycopg
    from psycopg.rows import dict_row
    from requests.certs import where

    with psycopg.connect(
        host=os.environ['LAKEBASE_HOST'], dbname=os.environ['LAKEBASE_DATABASE'],
        user=os.environ['LAKEBASE_USER'], password=os.environ['LAKEBASE_PASSWORD'],
        sslmode='verify-full', sslrootcert=os.environ.get('LAKEBASE_SSLROOTCERT') or where(),
        connect_timeout=5, row_factory=dict_row,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            cursor.execute("SET LOCAL statement_timeout = '10s'")
            cursor.execute("select set_config('app.company_id', %s, true)", (COMPANY,))
            cursor.execute(GENERATION_SQL, (COMPANY,))
            generations = cursor.fetchall()
            if len(generations) != 1:
                raise ValueError('SteelTrap has no single accepted VP reporting generation')
            generation = generations[0]
            if not generation['valid'] or not generation['policy']:
                raise ValueError('Current SteelTrap generation lacks the certified VP appointment scope')
            cursor.execute(MEMBERS_SQL, (COMPANY, generation['generation_id'], generation['policy']['start'], OFFER, CONTRACT))
            return _bundle(generation, cursor.fetchall())
