"""Register multi-repo mapping for project OSJ via the LangGraph store."""
import asyncio
from datetime import UTC, datetime
from langgraph_sdk import get_client

LANGGRAPH_URL = "http://localhost:2024"

async def main():
    client = get_client(url=LANGGRAPH_URL)
    
    repos = [
        {"owner": "sandeep04nagarro", "name": "osj-frontend", "type": "frontend"},
        {"owner": "sandeep04nagarro", "name": "osj-backend", "type": "backend"},
    ]
    
    # Write to the multi_repo_registry store
    await client.store.put_item(
        ["multi_repo_registry"],
        "OSJ",
        {"repos": repos, "updated_at": datetime.now(UTC).isoformat()},
    )
    print("✅ Registered repos for project OSJ:")
    for r in repos:
        print(f"   - {r['owner']}/{r['name']} (type: {r['type']})")
    
    # Verify by reading it back
    item = await client.store.get_item(["multi_repo_registry"], "OSJ")
    print(f"\n📦 Stored value: {item}")

asyncio.run(main())