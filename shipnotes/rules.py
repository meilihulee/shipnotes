"""Deterministic commit parsing, filtering and classification.

Everything in this file is plain Python. The language model never decides
whether a commit is shown or which section it lands in: that is done here,
with rules you can read, test and argue with.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Conventional-commit header: type(scope)!: subject
CC_RE = re.compile(
    r"^(?P<type>[a-zA-Z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:\s*(?P<subject>.+)$"
)
HASH_PREFIX_RE = re.compile(r"^(?:[0-9a-f]{7,40})\s+", re.I)
BULLET_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")
PR_REF_RE = re.compile(r"\s*\((?:#|GH-)(\d+)\)\s*$")
TRAILING_PR_RE = re.compile(r"\s+#(\d+)\s*$")
GITMOJI_RE = re.compile(r"^(?::[a-z0-9_+-]+:\s*)+")
EMOJI_RE = re.compile(r"^[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\u200d\s]+")

SECTIONS = ("new", "improved", "fixed")

# Conventional types that customers never need to read about.
INTERNAL_TYPES = {
    "chore", "ci", "build", "refactor", "test", "tests", "style", "deps",
    "dep", "release", "revert", "wip", "docs", "doc", "lint", "format",
    "infra", "dev", "meta", "bump",
}
TYPE_TO_SECTION = {
    "feat": "new", "feature": "new", "add": "new", "new": "new",
    "fix": "fixed", "bugfix": "fixed", "hotfix": "fixed", "bug": "fixed",
    "security": "fixed", "sec": "fixed",
    "perf": "improved", "improve": "improved", "improvement": "improved",
    "enhance": "improved", "enhancement": "improved", "update": "improved",
    "ux": "improved", "ui": "improved", "a11y": "improved", "i18n": "improved",
}

# Heuristics for repos that do not use conventional commits.
INTERNAL_PATTERNS = [
    r"^merge (pull request|branch|remote-tracking)\b",
    r"^merged? .* into ",
    r"^bump(ed)? .* (from|to) ",
    r"^(update|upgrade|bump) (dependency|dependencies|deps|lockfile|lock file)\b",
    r"^(update|upgrade) [\w@/.-]+ (requirement )?(from|to) v?\d",
    r"^(chore|ci|build|test|tests|refactor|style|docs?)\b[\s:/]",
    r"^(release|prepare release|version bump|bump version)\b",
    r"^v?\d+\.\d+(\.\d+)?([-.][\w.]+)?$",
    r"^(wip|fixup!|squash!|amend!)",
    r"^revert ",
    r"\b(typo|typos|lint(ing)?|linter|prettier|eslint|black|ruff|flake8|clippy)\b",
    r"\b(formatting|reformat|whitespace|code style)\b",
    r"\b(unit tests?|e2e tests?|test coverage|add tests?|fix tests?|flaky test|test failures?|"
    r"failing tests?|tests? (fail|failure|suite)|fixtures?|snapshot tests?)\b",
    r"(^|\s)\.github/|\b(test|tests|ci|lint|release)\.ya?ml\b",
    r"\b(mypy|pyright|typecheck(ing)?|type hints?|types to make|tsc errors?|sphinx|"
    r"docs? build|deprecation warnings?)\b",
    r"\b(ci|github actions?( workflow)?|ci pipeline|pre-commit|codecov|dependabot|renovate)\b",
    r"\b(refactor(ing|ed)?|clean ?up|cleanup|rename internal|internal)\b",
    r"\b(changelog|readme|contributing\.md|code of conduct)\b",
]
INTERNAL_RE = [re.compile(p, re.I) for p in INTERNAL_PATTERNS]

FIXED_RE = re.compile(
    r"\b(fix(es|ed)?|bug|crash(es|ed)?|resolve[sd]?|correct(s|ed)?|prevent(s|ed)?|"
    r"handle[sd]?|avoid(s|ed)?|regression|broken|error|issue|leak|hang|patch)\b",
    re.I,
)
NEW_RE = re.compile(
    r"^(add(s|ed)?|introduce[sd]?|implement(s|ed)?|support(s|ed)?|new|create[sd]?|"
    r"allow(s|ed)?|enable[sd]?|expose[sd]?|launch(es|ed)?)\b",
    re.I,
)

IMPROVED_RE = re.compile(
    r"^(improve[sd]?|update[sd]?|optimi[sz]e[sd]?|speed(s)? up|sped up|make|made|refine[sd]?|"
    r"polish(es|ed)?|enhance[sd]?|tweak(s|ed)?|reduce[sd]?|increase[sd]?|simplif(y|ies|ied)|"
    r"clarif(y|ies|ied)|better|faster|use|show|hide|change[sd]?|rework(s|ed)?|redesign(s|ed)?)\b",
    re.I,
)
REMOVED_RE = re.compile(r"^(drop(s|ped)?|remove[sd]?|delete[sd]?|deprecate[sd]?)\b", re.I)

BOT_AUTHORS = re.compile(r"\[bot\]$|^(dependabot|renovate|github-actions|pre-commit-ci)", re.I)


@dataclass
class Commit:
    raw: str
    subject: str
    section: str = "improved"
    scope: str = ""
    breaking: bool = False
    pr: str = ""
    sha: str = ""
    author: str = ""
    dropped_reason: str = ""
    note: str = ""          # final customer-facing line
    rewritten: bool = False  # True if the model produced `note`
    extra: dict = field(default_factory=dict)

    @property
    def kept(self) -> bool:
        return not self.dropped_reason


def parse_text(text: str) -> list[dict]:
    """Turn pasted text into a list of {subject, body, sha} dicts.

    Accepts `git log --oneline`, plain one-commit-per-line lists, bullet
    lists, and the default multi-line `git log` format.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[dict] = []
    if any(re.match(r"^commit [0-9a-f]{7,40}\b", l) for l in lines[:5]):
        cur = None
        for l in lines:
            m = re.match(r"^commit ([0-9a-f]{7,40})\b", l)
            if m:
                if cur:
                    out.append(cur)
                cur = {"sha": m.group(1)[:7], "subject": "", "body": "", "author": ""}
                continue
            if cur is None:
                continue
            if l.startswith("Author:"):
                cur["author"] = l[7:].strip()
            elif l.startswith(("Date:", "Merge:")):
                continue
            elif l.startswith("    ") or l.startswith("\t"):
                s = l.strip()
                if not cur["subject"] and s:
                    cur["subject"] = s
                elif s:
                    cur["body"] += s + "\n"
        if cur:
            out.append(cur)
        return [c for c in out if c["subject"]]

    for l in lines:
        s = l.strip()
        if not s or s.startswith("#") and not re.match(r"^#\d", s):
            continue
        s = BULLET_RE.sub("", s)
        sha = ""
        m = HASH_PREFIX_RE.match(s)
        if m:
            sha = m.group(0).strip()[:7]
            s = s[m.end():]
        if s:
            out.append({"sha": sha, "subject": s, "body": "", "author": ""})
    return out


def _clean_subject(s: str) -> tuple[str, str]:
    s = GITMOJI_RE.sub("", s)
    s = EMOJI_RE.sub("", s).strip()
    pr = ""
    m = PR_REF_RE.search(s)
    if m:
        pr = m.group(1)
        s = s[: m.start()].rstrip()
    # "..., closes #1679" is for GitHub, not for readers
    m = re.search(r",?\s*\b(closes|close|fixes|fix|resolves|refs?|see)\s+#(\d+)\b", s, flags=re.I)
    if m:
        pr = pr or m.group(2)
        s = (s[: m.start()] + s[m.end():]).strip()
    m = TRAILING_PR_RE.search(s)
    if m:
        pr = pr or m.group(1)
        s = s[: m.start()].rstrip()
    # squash merges can leave an earlier "(#123)" in the middle
    s = re.sub(r"\s*\((?:#|GH-)\d+\)", "", s)
    return s.strip(" .;,"), pr


def classify(item: dict) -> Commit:
    raw = item["subject"].strip()
    subject, pr = _clean_subject(raw)
    c = Commit(raw=raw, subject=subject, pr=pr, sha=item.get("sha", ""),
               author=item.get("author", ""))
    body = item.get("body", "") or ""
    if "BREAKING CHANGE" in body or "BREAKING-CHANGE" in body:
        c.breaking = True

    if c.author and BOT_AUTHORS.search(c.author):
        c.dropped_reason = "bot author"
        return c

    m = CC_RE.match(subject)
    if m:
        ctype = m.group("type").lower()
        c.scope = (m.group("scope") or "").strip()
        c.breaking = c.breaking or bool(m.group("bang"))
        c.subject = m.group("subject").strip(" .")
        if ctype in INTERNAL_TYPES and not c.breaking:
            c.dropped_reason = f"type '{ctype}'"
            return c
        if ctype in TYPE_TO_SECTION:
            c.section = TYPE_TO_SECTION[ctype]
            if REMOVED_RE.search(c.subject):
                c.section = "improved"
            # `fix(deps)` / `feat(ci)` are still internal
            if c.scope.lower() in {"deps", "dep", "ci", "build", "test", "tests", "release"}:
                c.dropped_reason = f"scope '{c.scope}'"
            return c
        # unknown type such as `api: ...` is really a scope; fall through
        c.scope = ctype
        subject = c.subject

    for rx in INTERNAL_RE:
        if rx.search(subject):
            c.dropped_reason = "looks internal"
            return c

    if REMOVED_RE.search(subject):
        c.section = "improved"
    elif NEW_RE.search(subject):
        c.section = "new"
    elif IMPROVED_RE.search(subject):
        c.section = "improved"
    elif FIXED_RE.search(subject):
        c.section = "fixed"
    else:
        c.section = "improved"
    return c


def fallback_note(c: Commit) -> str:
    """A rule-based customer line, used when the model is skipped or misbehaves."""
    s = c.subject.strip()
    if not s:
        return s
    s = s.replace("`", "")
    first, _, rest = s.partition(" ")
    verb = PAST_TENSE.get(first.lower())
    if verb and rest:
        rest = re.sub(r"^for\s+", "", rest) if verb == "Fixed" else rest
        s = f"{verb} {rest}"
    elif c.section == "new" and not NEW_RE.search(s):
        s = "Added " + (s if s[:2].isupper() else s[0].lower() + s[1:])
    elif c.section == "fixed" and not FIXED_RE.match(s):
        s = f"Fixed {s}"
    elif c.section == "improved" and not (IMPROVED_RE.match(s) or REMOVED_RE.match(s)) \
            and re.search(r"[._]", first):
        s = f"Updated {s}"  # bare code noun phrase, e.g. "model.supports_x property"
    first = s.split(" ", 1)[0]
    if not re.search(r"[._]", first):  # don't capitalise code like model.supports_x
        s = s[0].upper() + s[1:]
    if not s.endswith((".", "!", "?")):
        s += "."
    return s


# First word of an imperative commit -> how a changelog says it.
PAST_TENSE = {
    "add": "Added", "adds": "Added", "fix": "Fixed", "fixes": "Fixed", "remove": "Removed",
    "drop": "Dropped", "improve": "Improved", "update": "Updated", "make": "Made",
    "change": "Changed", "rename": "Renamed", "replace": "Replaced", "support": "Added support for",
    "allow": "Now allows", "use": "Now uses", "show": "Now shows", "keep": "Now keeps",
    "close": "Now closes", "wrap": "Now wraps", "set": "Now sets", "handle": "Now handles",
    "prevent": "Prevented", "avoid": "Avoided", "reduce": "Reduced", "increase": "Increased",
    "enable": "Enabled", "disable": "Disabled", "deprecate": "Deprecated", "speed": "Sped",
    "introduce": "Introduced", "debounce": "Debounced", "cache": "Cached", "batch": "Batched",
"load": "Now loads", "return": "Now returns", "implement": "Implemented", "optimize": "Optimized",
}


def dedupe(commits: list[Commit]) -> list[Commit]:
    seen: set[str] = set()
    for c in commits:
        if not c.kept:
            continue
        key = re.sub(r"\W+", " ", c.subject.lower()).strip()
        if key in seen:
            c.dropped_reason = "duplicate"
        seen.add(key)
    return commits
