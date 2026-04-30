#!/usr/bin/env python3
"""Calendar notification delivery script.

Called by Hermes cron job every minute.
1. Hits /tools/outbox/deliver-batch to get pending reminders
2. Outputs them as JSON for the agent to deliver via send_message
"""
import json
import os
import sys

import requests

BASE_URL = os.environ.get('CALENDAR_AGENT_URL', 'http://localhost:8080')
AGENT_TOKEN = os.environ.get('AGENT_API_TOKEN', '')

if not AGENT_TOKEN:
    # Try loading from .env file
    env_path = os.environ.get('CALENDAR_AGENT_ENV', '/root/calendar-agent/.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith('AGENT_API_TOKEN='):
                    AGENT_TOKEN = line.split('=', 1)[1].strip().strip('"').strip("'")
                    break

if not AGENT_TOKEN:
    print(json.dumps({'error': 'No AGENT_API_TOKEN found', 'messages': []}))
    sys.exit(1)

resp = requests.post(
    f'{BASE_URL}/tools/outbox/deliver-batch',
    headers={'Authorization': f'Bearer {AGENT_TOKEN}', 'Content-Type': 'application/json'},
    timeout=10,
)
resp.raise_for_status()
messages = resp.json()

if messages:
    print(json.dumps({'messages': messages}, ensure_ascii=False))
else:
    print(json.dumps({'messages': []}))