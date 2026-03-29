"""GitHub Issues bootstrap — T-066.

Connects to GitHub, fetches repos from GITHUB_ORG, registers each as a project
in CanonicalStateStore with default state. No Jira credentials needed.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import List

logger = logging.getLogger(__name__)


async def bootstrap(org: str = None, db_path: str = None) -> int:
    """Register all repos from GITHUB_ORG as PMO projects.

    Args:
        org: GitHub organization name. Defaults to GITHUB_ORG env var.
        db_path: SQLite database path.

    Returns:
        Number of projects registered.
    """
    from state.canonical_state import CanonicalStateStore
    from state.schemas import CanonicalProjectState, HealthMetrics, ProjectIdentity

    org = org or os.environ.get("GITHUB_ORG", "")
    if not org:
        logger.warning("GITHUB_ORG not set — skipping bootstrap")
        return 0

    token = os.environ.get("GITHUB_TOKEN", "")
    db_path = db_path or os.environ.get("SQLITE_DB_PATH", "./data/autonomous_pmo.db")

    store = CanonicalStateStore(db_path=db_path)
    await store.initialize()

    try:
        from github import Github
        client = Github(token) if token else Github()
        gh_org = client.get_organization(org)
        repos = list(gh_org.get_repos())
    except Exception as e:
        logger.error("Bootstrap: failed to fetch repos from org '%s': %s", org, e)
        return 0

    count = 0
    for repo in repos:
        project_id = f"gh_{repo.name}"
        existing = await store.get(project_id)
        if existing:
            continue

        state = CanonicalProjectState(
            project_id=project_id,
            identity=ProjectIdentity(
                project_id=project_id,
                name=repo.name,
                tenant_id="default",
                owner=repo.owner.login,
            ),
            health=HealthMetrics(schedule_health=0.8, open_blockers=0),
        )
        await store.upsert(state)
        count += 1
        logger.info("Bootstrap: registered project '%s'", project_id)

    print(f"Bootstrap complete: {count} new projects registered from org '{org}'")
    if repos:
        print(f"Total repos found: {len(repos)}")
    return count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(bootstrap())
