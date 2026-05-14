"""Fetch a news article and extract its main body text.

Used by both VN and World pipelines to give the AI summarizer the full article
body (not just the RSS description). Trafilatura handles per-site HTML quirks
across VnExpress, Tuoi Tre, VietnamNet, VietnamPlus, Reuters, AP, BBC, etc.

Failure modes return None — callers must fall back to RSS description.
"""

from __future__ import annotations

import sys
import urllib.request

try:
    import trafilatura
except ImportError:
    trafilatura = None


def _urllib_fetch(url: str, timeout: int) -> str | None:
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode('utf-8', 'ignore')
    except Exception as exc:
        print(f'[article_extract] urllib fetch failed for {url}: {exc}', file=sys.stderr)
        return None


def fetch_article_body(url: str, timeout: int = 15, min_chars: int = 200) -> str | None:
    if not url:
        return None
    if trafilatura is None:
        print('[article_extract] trafilatura not installed; cannot extract body', file=sys.stderr)
        return None

    # Prefer trafilatura's built-in fetcher (handles encoding detection); on
    # failure (TypeError from version-specific kwargs, SSL issues on macOS,
    # User-Agent blocks) fall back to urllib + custom UA.
    downloaded = None
    try:
        downloaded = trafilatura.fetch_url(url)
    except Exception as exc:
        print(f'[article_extract] trafilatura.fetch_url raised for {url}: {exc}', file=sys.stderr)

    if not downloaded:
        downloaded = _urllib_fetch(url, timeout)
    if not downloaded:
        return None

    try:
        body = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=False,
            favor_recall=True,
        )
    except Exception as exc:
        print(f'[article_extract] trafilatura.extract failed for {url}: {exc}', file=sys.stderr)
        return None
    if not body or len(body) < min_chars:
        print(f'[article_extract] extracted body too short ({len(body) if body else 0} chars) for {url}', file=sys.stderr)
        return None
    return body.strip()
