"""Read-only release checks: registry counts, webhook authentication and routing."""
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'functionapp'))
from shared import config, db, telegram


def main():
    endpoint = 'https://skatebot-prod-azure-32441.azurewebsites.net/api/webhook'
    rows = list(db._members.query_entities("PartitionKey eq 'MEMBER'"))
    eligible = sum(bool(row.get('rules_acknowledged')) and not row.get('ambiguous') for row in rows)
    with httpx.Client(timeout=45) as client:
        anonymous = client.post(endpoint, json={})
        authenticated = client.post(endpoint, json={}, headers={
            'X-Telegram-Bot-Api-Secret-Token': config.webhook_secret()})
    info = telegram.get_webhook_info()
    result = {
        'registry_records': len(rows), 'acknowledged_unambiguous': eligible,
        'without_secret_status': anonymous.status_code,
        'authenticated_empty_update_status': authenticated.status_code,
        'authenticated_empty_update_body': authenticated.text[:60],
        'telegram_routes_to_azure': info.get('url') == endpoint,
        'telegram_pending_updates': info.get('pending_update_count'),
        'telegram_last_error_date': info.get('last_error_date'),
    }
    print(json.dumps(result), flush=True)
    assert eligible > 0
    assert anonymous.status_code == 401
    assert authenticated.status_code == 200 and authenticated.text == 'ok'
    assert info.get('url') == endpoint


if __name__ == '__main__':
    main()
