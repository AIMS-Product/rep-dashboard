"""Lead-level evidence for the existing scorecard financial metrics."""
from scorecard_rules import cash_collected


def financial_details(opportunities, *, grain="lead"):
    """Keep cash per opportunity and closed credit per distinct lead."""
    cash_rows = []
    closed = {}
    for opportunity in opportunities:
        lead_id = opportunity['lead_id']
        row = {'id': lead_id, 'name': opportunity.get('lead_name') or lead_id,
               'url': f'https://app.close.com/lead/{lead_id}/',
               'owner_id': opportunity.get('user_id'),
               'opportunity_id': opportunity['id'], 'date_won': opportunity.get('date_won'),
               'cash_amount': cash_collected(opportunity)}
        cash_rows.append(row)
        closed.setdefault(opportunity["id"] if grain == "opportunity" else lead_id, row)
    return {'closed_leads': list(closed.values()), 'cash_opportunities': cash_rows}


def attach_financial_details(dashboard, opportunities):
    """Require drill-down evidence to reconcile with the displayed win counts."""
    for rep in dashboard['reps']:
        own = [o for o in opportunities if o.get('user_id') == rep['rep_owner_id']]
        details = financial_details(own, grain=dashboard.get("closed_grain", "lead"))
        if len(details['closed_leads']) != rep['deals']:
            raise ValueError(f"Closed lead evidence does not reconcile for {rep['name']}")
        rep['financial_details'] = details
    team = financial_details(opportunities, grain=dashboard.get("closed_grain", "lead"))
    if len(team['closed_leads']) != dashboard['total_deals']:
        raise ValueError('Team closed lead evidence does not reconcile')
    # Cash retains the displayed roster scope; close rate retains historical team wins.
    team['cash_opportunities'] = [row for rep in dashboard['reps']
                                  for row in rep['financial_details']['cash_opportunities']]
    dashboard['team_financial_details'] = team
    return dashboard
