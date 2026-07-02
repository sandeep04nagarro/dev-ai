"""Verify the multi-repo registry has the correct repos."""
import asyncio
from langgraph_sdk import get_client

async def main():
    client = get_client(url="http://localhost:2024")
    item = await client.store.get_item(["multi_repo_registry"], "OSJ")
    
    if item is None:
        print("❌ No registry entry for OSJ")
        return
    
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    repos = value.get("repos", []) if value else []
    
    print(f"✅ Found {len(repos)} repos for OSJ:")
    for r in repos:
        print(f"   - {r['owner']}/{r['name']} (type: {r.get('type', '?')})")

asyncio.run(main())