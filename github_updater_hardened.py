#!/usr/bin/env python3
"""Election-night source hardening wrapper.

Keeps github_updater.py as the canonical parser/aggregator, but replaces only
its bounded result-link discovery so county-site quirks do not block results:
- repairs two known malformed Revize relative links (Lincoln and Sheridan)
- follows iframe/embed/object document sources in addition to anchors
- permits Johnson County's explicitly trusted Google Drive document links
- converts public Google Drive file links to direct-download targets when possible
"""
from __future__ import annotations

import re
from collections import deque
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

import github_updater as base


EXTERNAL_HOSTS_BY_ROOT = {
    "johnsoncowy.gov": {"drive.google.com", "docs.google.com"},
}

REVIZE_PATH_REPAIRS = (
    (
        "/government/clerk/elections_voting_information/government/clerk/elections_voting_information/",
        "/government/clerk/elections_voting_information/",
    ),
    (
        "/departments/elections/departments/elections/",
        "/departments/elections/",
    ),
)

JINA_PROXY_ROOT = "https://r.jina.ai/http://"


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _repair_known_url(url: str) -> str:
    parts = urlparse(url)
    path = parts.path
    for bad, good in REVIZE_PATH_REPAIRS:
        if bad in path:
            path = path.replace(bad, good, 1)
    return urlunparse(parts._replace(path=path))


def _allowed(root_url: str, candidate_url: str) -> bool:
    root = _host(root_url)
    candidate = _host(candidate_url)
    return candidate == root or candidate in EXTERNAL_HOSTS_BY_ROOT.get(root, set())


def _drive_download(url: str) -> str:
    """Turn a public Google Drive file share URL into a direct-download URL."""
    p = urlparse(url)
    if p.netloc.lower().removeprefix("www.") != "drive.google.com":
        return url
    m = re.search(r"/file/d/([^/]+)", p.path)
    file_id = m.group(1) if m else parse_qs(p.query).get("id", [None])[0]
    if not file_id:
        return url
    return f"https://drive.google.com/uc?{urlencode({'export': 'download', 'id': file_id})}"


def _get_with_sheridan_fallback(session, url: str):
    """Fetch an official URL, proxying Sheridan's public 403 responses as text."""
    try:
        return base.get(session, url)
    except Exception:
        if _host(url) != "sheridancountywy.gov":
            raise
        original = urlparse(url)
        proxy_target = urlunparse(original._replace(scheme="http"))
        response = base.get(session, JINA_PROXY_ROOT + proxy_target)
        response.url = url
        response.headers["content-type"] = "text/markdown; charset=utf-8"
        response._jina_markdown = True
        return response


def discover(session, landing, max_depth=2, max_pages=28):
    """Hardened bounded crawl for county election-result documents."""
    out = []
    seen = set()
    q = deque([(landing, 0, 50)])

    while q and len(seen) < max_pages:
        url, depth, parent_score = q.popleft()
        url = _repair_known_url(url)
        if url in seen:
            continue
        seen.add(url)

        try:
            r = _get_with_sheridan_fallback(session, url)
        except Exception:
            continue

        ct = r.headers.get("content-type", "").lower()
        final = _repair_known_url(r.url)
        jina_markdown = bool(getattr(r, "_jina_markdown", False))
        if not jina_markdown and ("pdf" in ct or final.lower().split("?")[0].endswith(".pdf")):
            out.append((max(parent_score, 20), final, r.content, ct))
            continue

        soup = BeautifulSoup(r.content, "html.parser")
        page = base.norm(soup.get_text(" ", strip=True))
        ps = base.score_link(page[:2500], final)
        parent_is_result_page = "2026" in page and ("result" in page or "return" in page)
        if parent_is_result_page:
            out.append((max(10, ps), final, r.content, ct))

        if depth >= max_depth:
            continue

        # Jina renders links as Markdown when it retrieves a public page that
        # Sheridan's server refuses to return directly to the runner.
        if jina_markdown:
            markdown = r.content.decode("utf-8", errors="ignore")
            for label, href in re.findall(r"\[([^\]]+)\]\((https?://[^)]+)\)", markdown):
                u = _repair_known_url(href)
                sc = base.score_link(label, u)
                if _allowed(landing, u) and sc > 0 and not base.has_bad_marker(label + " " + u):
                    q.append((u, depth + 1, max(sc, parent_score - 8)))

        # Normal hyperlinks, including Johnson County's public Drive documents.
        for a in soup.find_all("a", href=True):
            raw = urljoin(final, a["href"])
            u = _repair_known_url(raw)
            txt = " ".join(a.stripped_strings)
            sc = base.score_link(txt, u)
            if not _allowed(landing, u):
                continue
            trail = base.norm(txt + " " + u)
            follow = sc > 0 or any(
                k in trail
                for k in (
                    "election result",
                    "results archive",
                    "previous election",
                    "historical election",
                    "elections voting",
                    "county clerk elections",
                    "unofficial result",
                )
            )
            if follow and not base.has_bad_marker(txt + " " + u):
                q.append((_drive_download(u), depth + 1, max(sc, parent_score - 8)))

        # Some county CMSes expose result documents only through embedded viewers.
        for tag_name, attr in (("iframe", "src"), ("embed", "src"), ("object", "data")):
            for tag in soup.find_all(tag_name):
                src = tag.get(attr)
                if not src:
                    continue
                u = _repair_known_url(urljoin(final, src))
                if not _allowed(landing, u):
                    continue
                sc = base.score_link("embedded election result", u)
                if sc > 0 or parent_is_result_page or "result" in base.norm(final):
                    q.append((_drive_download(u), depth + 1, max(sc, parent_score - 8)))

    ded = {}
    for item in out:
        if item[1] not in ded or item[0] > ded[item[1]][0]:
            ded[item[1]] = item
    return sorted(ded.values(), key=lambda x: x[0], reverse=True)


base.discover = discover

if __name__ == "__main__":
    base.main()
