"""Enrich the rep dashboard with the manager dashboard's Avoma MTD scorecard."""
from pathlib import Path

from avoma_scores import fetch_avoma_mtd_scores, score_for_close_email
from build_adherence_preview import CloseClient, load_env_value


def add_avoma_scores_to_dashboard(dashboard, *, as_of=None, audit_path=None):
    root = Path(__file__).resolve().parents[1]
    paths = [root.parent / '.env', root / '.env']
    options={'as_of':as_of,'audit_path':audit_path} if as_of or audit_path else {}
    scores, metadata = fetch_avoma_mtd_scores(load_env_value(paths, 'AVOMA_API_KEY'),**options)
    emails = {}
    if metadata.get('available'):
        close_key = load_env_value(paths, 'CLOSE_API_KEY')
        if not close_key:
            raise RuntimeError('CLOSE_API_KEY is required to match Avoma scores to dashboard reps')
        client = CloseClient(close_key)
        emails = {user['id']: str(user.get('email') or '').strip().casefold()
                  for user in client.paginate('/user/', {'_fields': 'id,email'})}
    weighted = 0
    count = 0
    for rep in dashboard.get('reps', []):
        score = score_for_close_email(scores, emails.get(rep.get('rep_owner_id'), ''))
        rep['qa_score'] = score.get('score')
        rep['qa_score_count'] = score.get('count', 0)
        if rep['qa_score'] is not None and rep['qa_score_count']:
            weighted += rep['qa_score'] * rep['qa_score_count']
            count += rep['qa_score_count']
    dashboard['team_qa_score'] = weighted/count if count else None
    dashboard['team_qa_score_count'] = count
    dashboard['qa_score_meta'] = metadata
    return dashboard
