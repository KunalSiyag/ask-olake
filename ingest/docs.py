"""Scrape the rendered OLake docs website into heading-level sections.

We scrape olake.io (not the olake-docs MDX files) because shared MDX snippets are
only inlined at build time, so the rendered page is what users actually read.
"""

import hashlib
import re
import time
from pathlib import Path
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag

SITEMAP_URL = "https://olake.io/sitemap.xml"
USER_AGENT = "AskOLakeBot/0.1 (+hackathon docs assistant)"
RAW_DIR = Path("data/raw/docs")

SKIP_TAGS = {"script", "style", "svg", "button", "nav", "noscript", "img"}


def docs_urls(client: httpx.Client) -> list[str]:
    xml = client.get(SITEMAP_URL).text
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = [e.text.strip() for e in ElementTree.fromstring(xml).findall(".//s:loc", ns)]
    return sorted(u for u in locs if u.startswith("https://olake.io/docs/"))


def fetch(client: httpx.Client, url: str) -> str:
    """Fetch a page, caching the raw HTML so re-runs don't hit the site again."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".html")
    if path.exists():
        return path.read_text()
    resp = client.get(url)
    resp.raise_for_status()
    path.write_text(resp.text)
    time.sleep(0.2)  # be polite
    return resp.text


def _inline(node: Tag) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True).replace("\u200b", "")).strip()


def _code(pre: Tag) -> str:
    lines = pre.select(".token-line")
    if lines:
        return "\n".join(line.get_text() for line in lines).rstrip()
    return pre.get_text().rstrip()


def _render(node: Tag, out: list[str]) -> None:
    """Append a markdown-ish rendering of node's children to out.

    h2/h3 become section markers: \x00H<level>|<anchor>|<title>\x00
    """
    for child in node.children:
        if isinstance(child, NavigableString):
            text = re.sub(r"\s+", " ", str(child))
            if text.strip():
                out.append(text)
            continue
        if not isinstance(child, Tag) or child.name in SKIP_TAGS:
            continue
        if "hash-link" in (child.get("class") or []):
            continue
        name = child.name
        if name in ("h2", "h3"):
            out.append(f"\n\x00H{name[1]}|{child.get('id', '')}|{_inline(child)}\x00\n")
        elif name in ("h4", "h5", "h6"):
            out.append(f"\n\n**{_inline(child)}**\n\n")
        elif name == "pre":
            out.append(f"\n\n```\n{_code(child)}\n```\n\n")
        elif name == "code":
            out.append(f"`{child.get_text()}`")
        elif name in ("p", "summary"):
            out.append("\n\n")
            _render(child, out)
            out.append("\n\n")
        elif name == "br":
            out.append("\n")
        elif name in ("ul", "ol"):
            out.append("\n")
            for li in child.find_all("li", recursive=False):
                out.append("\n- ")
                _render(li, out)
            out.append("\n\n")
        elif name == "table":
            out.append("\n\n")
            for row in child.find_all("tr"):
                cells = [_inline(c) for c in row.find_all(["th", "td"])]
                out.append("| " + " | ".join(cells) + " |\n")
            out.append("\n")
        elif name == "div" and "tabs-container" in (child.get("class") or []):
            labels = [_inline(li) for li in child.select('[role="tab"]')]
            panels = child.select('[role="tabpanel"]')
            for i, panel in enumerate(panels):
                label = labels[i] if i < len(labels) else f"Tab {i + 1}"
                out.append(f"\n\n**{label}:**\n\n")
                _render(panel, out)
        elif "theme-admonition" in (child.get("class") or []):
            heading = child.select_one('[class*="admonitionHeading"]')
            content = child.select_one('[class*="admonitionContent"]') or child
            out.append(f"\n\n**{_inline(heading) if heading else 'Note'}:** ")
            _render(content, out)
            out.append("\n\n")
        else:
            _render(child, out)


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_page(url: str, html: str) -> list[dict]:
    """Split one rendered docs page into sections at h2/h3 headings."""
    soup = BeautifulSoup(html, "lxml")
    body = soup.select_one(".theme-doc-markdown")
    if body is None:
        return []
    h1 = body.find("h1")
    h1_text = _inline(h1) if h1 else ""
    if h1:
        h1.decompose()
    # Connector pages all have the h1 "Overview", so prefix the breadcrumb trail
    # ("Sources / Postgres") to make titles specific.
    crumbs = [t for t in (_inline(a) for a in soup.select(".breadcrumbs__item")) if t]
    page_title = " / ".join(crumbs) if crumbs else ""
    if h1_text and h1_text.lower() != (crumbs[-1].lower() if crumbs else ""):
        page_title = f"{page_title}: {h1_text}" if page_title else h1_text
    page_title = page_title or (soup.title.get_text(strip=True) if soup.title else url)

    out: list[str] = []
    _render(body, out)
    parts = "".join(out).split("\x00")

    sections = []
    heading, anchor, level_titles = None, "", {}
    for part in parts:
        if part.startswith("H") and "|" in part[:4]:
            level, (anchor, title) = part[1], part[3:].split("|", 1)
            level_titles[level] = title
            if level == "2":
                level_titles.pop("3", None)
            heading = " > ".join(level_titles[k] for k in sorted(level_titles))
            continue
        text = _tidy(part)
        if not text:
            continue
        sections.append({
            "page_title": page_title,
            "heading": heading,
            "url": f"{url}#{anchor}" if heading else url,
            "page_url": url,
            "text": text,
        })
    return sections


def scrape() -> list[dict]:
    sections = []
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True) as client:
        urls = docs_urls(client)
        for i, url in enumerate(urls, 1):
            try:
                page_sections = parse_page(url, fetch(client, url))
            except httpx.HTTPError as e:
                print(f"  ! {url}: {e}")
                continue
            for s in page_sections:
                s["source_type"] = "docs"
                s["product"] = "fusion" if "/docs/fusion/" in url else "olake"
            sections.extend(page_sections)
            if i % 20 == 0:
                print(f"  {i}/{len(urls)} pages")
    print(f"docs: {len(urls)} pages -> {len(sections)} sections")
    return sections
