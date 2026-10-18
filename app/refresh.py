"""Live index refresh: poll the OLake sources and hot-swap the search index when they change.

Every REFRESH_INTERVAL_S (default 10 min):
  - `git ls-remote` the docs repo and the 4 code repos (no API quota used)
  - docs repo moved and docs/ files changed -> re-scrape olake.io; if the site hasn't
    redeployed yet (content identical), retry next cycle
  - a code repo moved -> fresh shallow clone of just that repo
  - GitHub issues updated since the last check (needs GITHUB_TOKEN)
Only changed chunks are re-embedded; the new index replaces the old one atomically.
"""

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.search import SearchIndex
from ingest import docs, issues, repos
from ingest.chunk import make_chunks

DATA = Path(os.getenv("DATA_DIR", "data"))
INTERVAL_S = int(os.getenv("REFRESH_INTERVAL_S", "600"))
DOCS_REPO = "olake-docs"
MAX_DEPLOY_WAIT_CYCLES = 6  # ~1h for the website to pick up a docs commit
WEBSITE_TYPES = {"docs", "blog"}  # both come from the olake.io scrape


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _repo_of(section: dict) -> str | None:
    if section["source_type"] != "repo":
        return None
    return section.get("repo") or section["page_url"].split("github.com/datazip-inc/")[1].split("/")[0]


class Refresher:
    def __init__(self, get_index, swap_index, meta: dict):
        self.get_index, self.swap_index = get_index, swap_index
        self.meta = meta
        self.heads = dict(meta.get("heads") or {})
        self.issues_checked_at = meta.get("issues_checked_at")
        path = DATA / "sections.jsonl"
        self.sections = [json.loads(line) for line in open(path)] if path.exists() else None
        self.status = {"last_check": None, "last_refresh": None, "last_error": None, "pending_docs_cycles": 0}
        self._lock = asyncio.Lock()

    async def run_forever(self) -> None:
        await asyncio.sleep(60)  # let the server settle after boot
        while True:
            await self.check()
            await asyncio.sleep(INTERVAL_S)

    async def check(self) -> dict:
        if self.sections is None:
            self.status["last_error"] = "no sections.jsonl; live refresh disabled"
            return self.status
        async with self._lock:
            try:
                changes = await asyncio.to_thread(self._check_sync)
                self.status.update(last_check=_now(), last_error=None, last_changes=changes)
            except Exception as e:  # keep serving the current index no matter what
                self.status.update(last_check=_now(), last_error=f"{type(e).__name__}: {e}")
        return self.status

    # ---- sync work, runs in a thread ----

    def _docs_changed_in(self, client: httpx.Client | None, old: str | None, new: str) -> bool:
        """Did the commit range touch docs/? Without a token, assume yes."""
        if not client or not old:
            return True
        resp = client.get(f"/repos/datazip-inc/{DOCS_REPO}/compare/{old}...{new}")
        if resp.status_code != 200:
            return True
        return any(f["filename"].startswith(("docs/", "blog/", "compare/")) for f in resp.json().get("files", []))

    def _check_sync(self) -> list[str]:
        changes: list[str] = []
        sections = self.sections
        client = issues.github_client()
        try:
            # 1. docs website
            head = repos.remote_head(DOCS_REPO)
            if head and head != self.heads.get(DOCS_REPO):
                if not self._docs_changed_in(client, self.heads.get(DOCS_REPO), head):
                    self.heads[DOCS_REPO] = head  # commit touched no docs/blog/compare content
                else:
                    old_docs = [s for s in sections if s["source_type"] in WEBSITE_TYPES]
                    new_docs = docs.scrape(cache=False)
                    if new_docs and docs.content_hash(new_docs) != docs.content_hash(old_docs):
                        sections = [s for s in sections if s["source_type"] not in WEBSITE_TYPES] + new_docs
                        self.heads[DOCS_REPO] = head
                        self.status["pending_docs_cycles"] = 0
                        changes.append(f"docs ({len(new_docs)} sections)")
                    else:
                        # website not redeployed yet; retry next cycle, give up after a while
                        self.status["pending_docs_cycles"] += 1
                        if self.status["pending_docs_cycles"] >= MAX_DEPLOY_WAIT_CYCLES:
                            self.heads[DOCS_REPO] = head
                            self.status["pending_docs_cycles"] = 0

            # 2. code repos
            moved = {}
            for repo in repos.REPOS:
                h = repos.remote_head(repo)
                if h and h != self.heads.get(repo):
                    moved[repo] = h
            if moved:
                fresh = repos.collect(only=list(moved), fresh=True)
                sections = [s for s in sections if _repo_of(s) not in moved] + fresh
                self.heads.update(moved)
                changes.append(f"repos {', '.join(moved)}")

            # 3. GitHub issues updated since last check
            if client:
                started = _now()
                updated = issues.fetch(client, since=self.issues_checked_at)
                self.issues_checked_at = started
                if updated:
                    urls = {s["url"] for s in updated}
                    sections = [s for s in sections if s["url"] not in urls] + updated
                    changes.append(f"{len(updated)} issues")
        finally:
            if client:
                client.close()

        if changes:
            t = time.perf_counter()
            chunks = make_chunks(sections)
            new_index = SearchIndex(chunks=chunks, previous=self.get_index())
            self.sections = sections
            self.swap_index(new_index)
            self.meta.update(built_at=_now(), chunks=len(chunks), pages=len({c["page_url"] for c in chunks}),
                             heads=dict(self.heads), issues_checked_at=self.issues_checked_at)
            self.status["last_refresh"] = _now()
            changes.append(f"reindexed {len(chunks)} chunks in {time.perf_counter() - t:.1f}s")
        else:
            self.meta.update(heads=dict(self.heads), issues_checked_at=self.issues_checked_at)
        return changes
