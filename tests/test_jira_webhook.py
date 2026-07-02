"""Send a signed test Jira webhook to trigger a multi-repo agent run."""
import hashlib
import hmac
import json
import sys

import httpx

LANGGRAPH_URL = "http://localhost:2024"
JIRA_WEBHOOK_SECRET = "2579fc5067d3a8ac51e155255d0673f9782d6bff3fe7b16eefbf6d322ae77949"

# ── Build the payload ──────────────────────────────────────────────
# Simulates a "comment_created" event on issue OSJ-99
# with an @openswe mention in the comment body
payload = {
    "webhookEvent": "comment_created",
    "issue": {
        "id": "99999",
        "key": "OSJ-7",
        "fields": {
            "summary": "Add user login page with API endpoint",
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "We need a login page on the frontend (React form with "
                                    "email/password) and a corresponding /api/auth/login "
                                    "endpoint on the backend (Flask). The endpoint should "
                                    "validate credentials and return a JWT token."
                                ),
                            }
                        ],
                    }
                ],
            },
            "creator": {
                "displayName": "Test User",
                "emailAddress": "test@example.com",
            },
            "assignee": None,
            "attachment": [],
        },
    },
    "comment": {
        "id": "comment-001",
        "author": {
            "displayName": "Test User",
            "emailAddress": "test@example.com",
            "accountType": "atlassian",
        },
        "body": {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "@openswe please implement this feature across both repos",
                        }
                    ],
                }
            ],
        },
    },
}

# ── Sign & send ────────────────────────────────────────────────────
body = json.dumps(payload).encode("utf-8")
signature = hmac.new(
    JIRA_WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256
).hexdigest()

print(f"📤 Sending Jira webhook for OSJ-99 ...")
print(f"   Signature: sha256={signature}")

resp = httpx.post(
    f"{LANGGRAPH_URL}/webhooks/jira",
    content=body,
    headers={
        "Content-Type": "application/json",
        "X-Hub-Signature": f"sha256={signature}",
    },
)

print(f"   Status: {resp.status_code}")
print(f"   Response: {resp.json()}")

if resp.status_code == 200 and resp.json().get("status") == "accepted":
    print("\n✅ Webhook accepted! Check the server logs for multi-repo behavior.")
else:
    print("\n❌ Webhook was not accepted. Check the response above.")