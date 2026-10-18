"""Fetch commits between two refs of a public GitHub repo (no token needed).

Uses the public REST API: /repos/{owner}/{repo}/compare/{base}...{head}.
Set GITHUB_TOKEN in the environment to raise the anonymous rate limit
(60 requests/hour per server IP) to 5,000.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
REPO_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?(?:[#?].*)?$"
    r"|^([\w.-]+)/([\w.-]+)$"
)
REF_RE = re.compile(r"^[\w./@^~+-]{1,100}$")
_cache: dict[str, tuple[float, object]] = {}
CACHE_SECONDS = 600


class SourceError(Exception):
    pass


def parse_repo(url: str) -> tuple[str, str]:
    m = REPO_RE.match(url.strip())
    if not m:
        raise SourceError("That doesn't look like a GitHub repo. Use https://github.com/owner/repo or owner/repo.")
    owner = m.group(1) or m.group(3)
    repo = m.group(2) or m.group(4)
    return owner, repo


def _get(path: str) -> object:
    url = API + path
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "ShipNotes/0.1 (+https://github.com)",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise SourceError("GitHub says that repo or ref doesn't exist (or it's private).")
        if e.code in (403, 429):
            raise SourceError("GitHub's anonymous rate limit is used up for this server. Paste `git log --oneline` output instead.")
        raise SourceError(f"GitHub API error {e.code}.")
    except (urllib.error.URLError, TimeoutError):
        raise SourceError("Couldn't reach the GitHub API.")
    _cache[url] = (time.time(), data)
    return data


def latest_tags(owner: str, repo: str, n: int = 2) -> list[str]:
    data = _get(f"/repos/{owner}/{repo}/tags?per_page={n}")
    return [t["name"] for t in data]  # type: ignore[index]


def fetch_range(repo_url: str, base: str = "", head: str = "") -> dict:
    owner, repo = parse_repo(repo_url)
    base, head = base.strip(), head.strip()
    for ref in (base, head):
        if ref and not REF_RE.match(ref):
            raise SourceError(f"'{ref}' isn't a valid tag, branch or commit.")
    if not base:
        tags = latest_tags(owner, repo, 2)
        if not tags:
            raise SourceError("This repo has no tags. Enter a 'from' commit or tag.")
        if head:
            base = tags[0]
        elif len(tags) >= 2:
            base, head = tags[1], tags[0]
        else:
            base = tags[0]
    if not head:
        meta = _get(f"/repos/{owner}/{repo}")
        head = meta["default_branch"]  # type: ignore[index]
    q = urllib.parse.quote
    data = _get(f"/repos/{owner}/{repo}/compare/{q(base, safe='')}...{q(head, safe='')}")
    commits = []
    for c in data.get("commits", []):  # type: ignore[union-attr]
        msg = c["commit"]["message"]
        subject, _, body = msg.partition("\n")
        author = (c.get("author") or {}).get("login") or c["commit"]["author"]["name"]
        commits.append({"sha": c["sha"][:7], "subject": subject, "body": body, "author": author})
    total = data.get("total_commits", len(commits))  # type: ignore[union-attr]
    return {
        "owner": owner, "repo": repo, "base": base, "head": head,
        "commits": commits, "total": total,
        "truncated": total > len(commits),
        "url": f"https://github.com/{owner}/{repo}",
    }
