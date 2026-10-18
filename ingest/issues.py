"""Closed, discussed GitHub issues -> lower-trust troubleshooting sections.

Fetching uses the `gh` CLI (authenticated, so no rate-limit trouble) and writes a snapshot
to data/issues.jsonl. Builds without `gh` (e.g. the Docker build) reuse the snapshot.

Refresh: uv run python -m ingest.issues
"""

import json
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ORG = "datazip-inc"
REPOS = {"olake": "olake", "olake-ui": "olake", "olake-helm": "olake", "olake-fusion": "fusion"}
SNAPSHOT = Path("data/issues.jsonl")
CHATTER = re.compile(r"\b(assign|work on (this|it)|take (this|it) up|pick (this|it) up|interested in|can i (work|take))\b", re.I)
MAX_BODY, MAX_COMMENT, MAX_TOTAL = 1500, 1200, 4000


def _gh(path: str) -> list:
    out = subprocess.run(["gh", "api", "--paginate", path], capture_output=True, text=True, check=True).stdout
    # --paginate concatenates JSON arrays: "][" between pages
    return json.loads("[" + out.strip()[1:-1].replace("]\n[", ",").replace("][", ",") + "]") if out.strip() else []


def _clean(text: str | None, limit: int) -> str:
    text = re.sub(r"<!--.*?-->", "", text or "", flags=re.S)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _issue_section(repo: str, product: str, issue: dict) -> dict | None:
    comments = _gh(f"repos/{ORG}/{repo}/issues/{issue['number']}/comments?per_page=100")
    useful = [c for c in comments if c.get("user", {}).get("type") != "Bot"
              and len(c.get("body") or "") > 60 and not CHATTER.search((c.get("body") or "")[:200])]
    if not useful:
        return None
    parts = [f"Issue ({issue.get('state_reason') or 'closed'}): {_clean(issue.get('body'), MAX_BODY)}", "Discussion:"]
    for c in useful:
        parts.append(f"- {c.get('author_association', 'NONE').lower()}: {_clean(c['body'], MAX_COMMENT)}")
    return {
        "page_title": f"GitHub issue {repo}#{issue['number']}: {issue['title']}",
        "heading": None,
        "url": issue["html_url"],
        "page_url": issue["html_url"],
        "text": "\n\n".join(parts)[:MAX_TOTAL],
        "source_type": "issue",
        "product": product,
    }


def fetch() -> list[dict]:
    sections = []
    for repo, product in REPOS.items():
        issues = [i for i in _gh(f"repos/{ORG}/{repo}/issues?state=closed&per_page=100")
                  if "pull_request" not in i and i.get("comments", 0) > 0]
        with ThreadPoolExecutor(max_workers=8) as pool:
            found = [s for s in pool.map(lambda i: _issue_section(repo, product, i), issues) if s]
        print(f"issues {repo}: {len(found)}/{len(issues)} kept")
        sections.extend(found)
    with open(SNAPSHOT, "w") as f:
        for s in sections:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    return sections


def collect() -> list[dict]:
    if shutil.which("gh"):
        try:
            return fetch()
        except subprocess.CalledProcessError as e:
            print(f"issues: gh failed ({e.stderr.strip()[:200]}); using snapshot")
    if SNAPSHOT.exists():
        sections = [json.loads(line) for line in open(SNAPSHOT)]
        print(f"issues: {len(sections)} from snapshot")
        return sections
    print("issues: no gh and no snapshot; skipping")
    return []


if __name__ == "__main__":
    fetch()
