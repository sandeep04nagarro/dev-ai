"""Register repos as enabled for auto-review (and optionally as project repos) in the LangGraph store.

Usage:
    LANGGRAPH_URL=http://localhost:2024 python scripts/repo_setup.py owner/repo1 owner/repo2

    # With a project key (also registers under multi_repo_registry):
    LANGGRAPH_URL=http://localhost:2024 python scripts/repo_setup.py \\
        --project OSJ \\
        owner/repo1:backend owner/repo2:frontend owner/repo3

    # Register under a project WITHOUT enabling for review:
    LANGGRAPH_URL=http://localhost:2024 python scripts/repo_setup.py \\
        --project OSJ --no-enable \\
        owner/repo1:backend owner/repo2:frontend

    # Default LANGGRAPH_URL is http://localhost:2024
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import UTC, datetime

from langgraph_sdk import get_client

ENABLED_REVIEW_REPOS_NAMESPACE: list[str] = ["enabled_review_repos"]
ENABLED_REVIEW_REPOS_KEY = "default"

MULTI_REPO_NAMESPACE: list[str] = ["multi_repo_registry"]

DEFAULT_TYPE = "backend"


def normalize_repo_full_name(raw: str) -> str:
    v = raw.strip()
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if v.lower().startswith(prefix):
            v = v[len(prefix) :]
    v = v.strip("/")
    if v.endswith(".git"):
        v = v[:-4]
    parts = [p for p in v.split("/") if p]
    if len(parts) != 2:
        raise ValueError(f"Invalid repo format: '{raw}' — expected owner/repo")
    return f"{parts[0]}/{parts[1]}"


def parse_repo_arg(arg: str) -> tuple[str, str]:
    if ":" in arg:
        raw, repo_type = arg.rsplit(":", 1)
        return normalize_repo_full_name(raw), repo_type
    return normalize_repo_full_name(arg), DEFAULT_TYPE


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Register repos in the LangGraph store for auto-review and/or project mapping.",
    )
    parser.add_argument(
        "repos",
        nargs="+",
        help="One or more owner/repo[:\btype] entries. Type defaults to 'backend'.",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="Jira project key to register repos under (e.g. OSJ).",
    )
    parser.add_argument(
        "--no-enable",
        action="store_true",
        help="Skip writing to the enabled-review-repos store (only register project repos).",
    )
    args = parser.parse_args()

    langgraph_url = os.environ.get("LANGGRAPH_URL", "http://localhost:2024")
    client = get_client(url=langgraph_url)

    parsed = [parse_repo_arg(arg) for arg in args.repos]
    repo_full_names = [r for r, _ in parsed]

    # 1. Write to the enabled-review-repos store
    if not args.no_enable:
        now = datetime.now(UTC).isoformat()
        await client.store.put_item(
            ENABLED_REVIEW_REPOS_NAMESPACE,
            ENABLED_REVIEW_REPOS_KEY,
            {"repos": sorted(repo_full_names), "updated_at": now},
        )
        print("✅ Enabled repos for auto-review:")
        for r in repo_full_names:
            print(f"   - {r}")

        stored = await client.store.get_item(
            ENABLED_REVIEW_REPOS_NAMESPACE, ENABLED_REVIEW_REPOS_KEY
        )
        print(f"\n📦 Enabled-review-repos store: {stored}")
    else:
        print("⏭️  Skipped enabling repos for auto-review (--no-enable)")

    # 2. Write to the multi-repo-registry store (if --project is given)
    if args.project:
        repos_payload = [
            {"owner": r.split("/", 1)[0], "name": r.split("/", 1)[1], "type": t} for r, t in parsed
        ]
        now = datetime.now(UTC).isoformat()
        await client.store.put_item(
            MULTI_REPO_NAMESPACE,
            args.project,
            {"repos": repos_payload, "updated_at": now},
        )
        print(f"\n✅ Registered repos for project '{args.project}':")
        for entry in repos_payload:
            print(f"   - {entry['owner']}/{entry['name']} (type: {entry['type']})")

        stored = await client.store.get_item(MULTI_REPO_NAMESPACE, args.project)
        print(f"\n📦 Multi-repo-registry store for '{args.project}': {stored}")


if __name__ == "__main__":
    asyncio.run(main())
