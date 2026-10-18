"""Collect READMEs, markdown docs and Helm values from the OLake GitHub repos."""

import re
import subprocess
from pathlib import Path

ORG = "datazip-inc"
REPOS = {"olake": "olake", "olake-ui": "olake", "olake-helm": "olake", "olake-fusion": "fusion"}
RAW_DIR = Path("data/raw/repos")

SKIP_PARTS = {"node_modules", "vendor", ".git", "testdata", "test", "tests", "__tests__", "dist", "build"}
SKIP_NAMES = {"CLAUDE.md", "AGENTS.md", "CHANGELOG.md", "CODE_OF_CONDUCT.md", "LICENSE.md", "SECURITY.md", "PULL_REQUEST_TEMPLATE.md"}
YAML_NAMES = {"values.yaml", "Chart.yaml"}


def clone(repo: str) -> tuple[Path, str]:
    """Shallow-clone (or reuse) a repo and return its path and default branch."""
    dest = RAW_DIR / repo
    if not dest.exists():
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", "-q", f"https://github.com/{ORG}/{repo}.git", str(dest)], check=True)
    branch = subprocess.run(["git", "-C", str(dest), "rev-parse", "--abbrev-ref", "HEAD"],
                            capture_output=True, text=True, check=True).stdout.strip()
    return dest, branch


def _slug(heading: str) -> str:
    """GitHub-style heading anchor."""
    s = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return s.replace(" ", "-")


def split_markdown(text: str) -> list[tuple[str | None, str | None, str]]:
    """Split markdown at ##/### headings (outside code fences) -> (heading_path, anchor, body)."""
    sections, buf, titles = [], [], {}
    heading, anchor, in_fence = None, None, False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else re.match(r"^(#{2,3})\s+(.*?)\s*#*\s*$", line)
        if m:
            sections.append((heading, anchor, "\n".join(buf).strip()))
            buf = []
            level, title = len(m.group(1)), re.sub(r"[*`]", "", m.group(2))
            titles[level] = title
            if level == 2:
                titles.pop(3, None)
            heading, anchor = " > ".join(titles[k] for k in sorted(titles)), _slug(title)
        else:
            buf.append(line)
    sections.append((heading, anchor, "\n".join(buf).strip()))
    return [s for s in sections if s[2]]


def _page_title(text: str, rel: str) -> str:
    m = re.search(r"^#\s+(.+)$", text, re.M)
    return re.sub(r"[*`]", "", m.group(1)).strip() if m else rel


def collect() -> list[dict]:
    sections = []
    for repo, product in REPOS.items():
        root, branch = clone(repo)
        n_files = 0
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root).as_posix()
            if not path.is_file() or SKIP_PARTS & set(path.parts) or path.name in SKIP_NAMES:
                continue
            is_md, is_yaml = path.suffix.lower() == ".md", path.name in YAML_NAMES
            if not (is_md or is_yaml):
                continue
            text = path.read_text(errors="ignore")
            if len(text.strip()) < 80:
                continue
            page_url = f"https://github.com/{ORG}/{repo}/blob/{branch}/{rel}"
            n_files += 1
            if is_yaml:
                parts = [(None, None, f"```yaml\n{text.strip()}\n```")]
                title = f"{repo}: {rel}"
            else:
                text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
                parts = split_markdown(text)
                title = f"{repo}: {_page_title(text, rel)}"
            for heading, anchor, body in parts:
                sections.append({
                    "page_title": title,
                    "heading": heading,
                    "url": f"{page_url}#{anchor}" if anchor else page_url,
                    "page_url": page_url,
                    "text": body,
                    "source_type": "repo",
                    "product": product,
                })
        print(f"repo {repo}: {n_files} files")
    print(f"repos: {len(sections)} sections")
    return sections
