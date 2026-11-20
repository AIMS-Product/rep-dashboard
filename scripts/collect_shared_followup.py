"""Reuse the audited current-owner/status follow-up capture for live shared builds."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import requests
from build_adherence_preview import CloseClient

OWNER='cf_gOfS9pFwext58oberEegLyix8hZzeHrxhCZOVh3P3rd'
BUSINESS='cf_aJlNlilQZIgLLuhcymNN8fiOzewnFxrbWjLZFPmsucO'
STATUSES=['stat_kY1aMGKOui3jjlgniY2LQWMadXN78cr7vTHVMPDCliy','stat_fOwpEPCfmjC2LhrJEZE5T1btfKJRcgkMinycBxLPwS5','stat_mRxbAkfEqzEcmEF2Z5CkucMQocXAbwzs0hSlD0SzHEh']
LEAD_FIELDS=['id','display_name','status_id',f'custom.{OWNER}',f'custom.{BUSINESS}']
ACTIVITY_FIELDS='id,_type,lead_id,user_id,direction,status,activity_at,date_created,date_sent,date_started'


def slim(lead):
    def value(key):return lead.get('custom.'+key,lead.get('custom',{}).get(key,''))
    return {'id':lead['id'],'name':lead['display_name'],'status_id':lead['status_id'],'owner_id':value(OWNER),'business_line':value(BUSINESS)}


def collect_followup(api_key, reps, cutoff):
    query={'type':'and','queries':[{'type':'object_type','object_type':'lead'},
        {'type':'field_condition','field':{'type':'custom_field','custom_field_id':OWNER},'condition':{'type':'reference','reference_type':'user','object_ids':[r['rep_owner_id'] for r in reps]}},
        {'type':'field_condition','field':{'type':'regular_field','object_type':'lead','field_name':'status_id'},'condition':{'type':'reference','reference_type':'status.lead','object_ids':STATUSES}}]}
    session=requests.Session();session.auth=(api_key,'');initial=[];cursor=None
    while True:
        body={'query':query,'_fields':{'lead':LEAD_FIELDS},'_limit':100}
        if cursor:body['cursor']=cursor
        response=session.post('https://api.close.com/api/v1/data/search/',json=body,timeout=45)
        if response.status_code!=200:raise RuntimeError(f'Follow-up census returned HTTP {response.status_code}')
        page=response.json()
        if not isinstance(page.get('data'),list):raise ValueError('Invalid follow-up census')
        initial.extend(page['data']);cursor=page.get('cursor')
        if not cursor:break
    if len({l['id'] for l in initial})!=len(initial):raise ValueError('Duplicate census lead')
    owners={r['rep_owner_id'] for r in reps}
    if any(slim(l)['owner_id'] not in owners or slim(l)['status_id'] not in STATUSES for l in initial):raise ValueError('Follow-up census escaped scope')
    selected=[slim(l) for l in initial if slim(l)['business_line'] in {'','Vendingpreneurs (VP)'}]
    def capture(lead):
        client=CloseClient(api_key)
        activities=list(client.paginate('/activity/',{'lead_id':lead['id'],'_type__in':'Call,SMS,Email','_order_by':'-activity_at','_fields':ACTIVITY_FIELDS,'activity_at__lte':cutoff.isoformat()}))
        if any(a.get('lead_id')!=lead['id'] for a in activities):raise ValueError('Follow-up activity escaped scope')
        current=slim(client.get('/lead/'+lead['id']+'/',{'_fields':','.join(LEAD_FIELDS)}))
        return {'lead':current,'activities':activities,'verified_at':datetime.now(cutoff.tzinfo).isoformat()}
    with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(capture,selected))
    return {'meta':{'started_at':cutoff.isoformat(),'ended_at':datetime.now(cutoff.tzinfo).isoformat(),'selected_count':len(selected)},'results':results}
