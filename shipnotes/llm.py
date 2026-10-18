"""Tiny client for a local llama.cpp server running Gemma 3 1B.

The model gets exactly two kinds of jobs:
  1. rewrite ONE commit subject as ONE customer-facing sentence
  2. write a short "what's new" blurb from the finished bullet list

The prompts are fixed prefixes so llama.cpp's prompt cache can reuse them:
only the few tokens of each new commit have to be processed.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

LLM_URL = os.environ.get("SHIPNOTES_LLM_URL", "http://127.0.0.1:8412")

REWRITE_EN = """You turn git commit messages into release notes for end users.
Rules: write ONE short sentence in plain English. Start with a verb in the past tense (Added, Fixed, Improved, Made, Removed). Keep product names, commands, flags and file names exactly as written. Never invent details that are not in the commit. No jargon, no emoji, no quotes.

Commit: feat(auth): support passkey login on mobile
Note: Added passkey sign-in on mobile.

Commit: fix: crash when uploading a file with no extension
Note: Fixed a crash when uploading a file without an extension.

Commit: perf(search): cache tokenizer, 3x faster queries
Note: Made search queries about 3x faster.

Commit: fix(ui): sidebar width not saved between sessions
Note: Fixed the sidebar forgetting its width between sessions.

Commit: show progress bar during sync
Note: Added a progress bar while syncing.

Commit: add --json flag to `export` command
Note: Added a --json flag to the export command.
"""

REWRITE_ZH = """You turn git commit messages into release notes for end users, written in Simplified Chinese.
Rules: write ONE short sentence in natural Simplified Chinese. Keep product names, commands, flags and file names in their original form. Never invent details that are not in the commit. No emoji, no quotes.

Commit: feat(auth): support passkey login on mobile
Note: 新增移动端通行密钥（passkey）登录。

Commit: fix: crash when uploading a file with no extension
Note: 修复上传无扩展名文件时崩溃的问题。

Commit: perf(search): cache tokenizer, 3x faster queries
Note: 搜索速度提升约 3 倍。

Commit: add --json flag to `export` command
Note: export 命令新增 --json 参数。

Commit: fix(api): timeout when uploading large files
Note: 修复上传大文件时请求超时的问题。
"""

SUMMARY_EN = """Write a short "What's new" announcement for a software release, suitable for a tweet or the first line of an email. Use at most 2 sentences and under 240 characters. Mention the 2 or 3 most useful changes from the list. Plain, friendly, no hashtags, no emoji, no invented features, Do not name the product or the version: they are added for you. Start with "This release". Output one short paragraph, not a list.

Changes:
{bullets}
"""

SUMMARY_ZH = """为一次软件版本发布写一段简短的“更新亮点”，适合发微博或作为邮件开头。最多 2 句话，不超过 100 个汉字。只提列表里最有用的 2 到 3 项改动。语气简洁友好，不要话题标签，不要表情，不要编造功能。不要提产品名和版本号，会自动加上。以“本次更新”开头。只输出一段话，不要列表。

改动：
{bullets}
"""


def _wrap(user: str) -> str:
    # Gemma 3 chat format, written by hand so the prefix is byte-identical
    # between calls and stays in the KV cache.
    return f"<start_of_turn>user\n{user}<end_of_turn>\n<start_of_turn>model\n"


def complete(prompt: str, n_predict: int, stop: list[str] | None = None,
             temperature: float = 0.2, timeout: float = 120) -> dict:
    body = json.dumps({
        "prompt": prompt,
        "n_predict": n_predict,
        "temperature": temperature,
        "top_k": 40,
        "top_p": 0.9,
        "repeat_penalty": 1.05,
        "cache_prompt": True,
        "stop": stop or ["<end_of_turn>", "\n"],
    }).encode()
    req = urllib.request.Request(LLM_URL + "/completion", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    t = d.get("timings", {})
    return {
        "text": d.get("content", ""),
        "prompt_n": t.get("prompt_n", 0),
        "cached_n": t.get("cache_n", 0),
        "prompt_ms": t.get("prompt_ms", 0.0),
        "gen_n": t.get("predicted_n", 0),
        "gen_ms": t.get("predicted_ms", 0.0),
    }


# The section is decided by rules, so we can also decide how the sentence
# starts. Prefilling the first word keeps a 1B model on the right track
# (a fix is described as a fix, a feature as something added).
PREFILL = {
    "en": {"new": " Added", "fixed": " Fixed", "improved": " Improved",
           "removed": " Removed", "deprecated": " Deprecated"},
    "zh": {"new": " 新增", "fixed": " 修复", "improved": " 优化",
           "removed": " 移除", "deprecated": " 弃用"},
}
_REMOVE = re.compile(r"^(drop|remove|delete)", re.I)
_DEPRECATE = re.compile(r"^deprecate", re.I)
_IMPROVE_FREE = re.compile(r"^(make|made|show|hide|use|change|rework|redesign|allow)", re.I)


_ADD_WORDS = re.compile(r"^(add|adds|added|introduce[sd]?|implement(s|ed)?|new|create[sd]?)\b", re.I)
_SUPPORT = re.compile(r"^support(s|ed)?\b", re.I)
# Things a user *does*. "feat: export a page as PDF" reads best as
# "You can now export a page as PDF", not "Added a page as PDF".
_USER_VERBS = re.compile(
    r"^(export|import|edit|share|search|filter|sort|sync|upload|download|invite|rename|"
    r"duplicate|schedule|pin|archive|configure|customi[sz]e|set|choose|select|open|view|"
    r"preview|copy|paste|undo|redo|drag|resize|run|deploy|connect|sign|log|switch|toggle|"
    r"save|restore|merge|split|group|tag|mention|reply|react|embed|print|translate|record|"
    r"stream|watch|follow|subscribe|attach|move|reorder|hide|show|collapse|expand|zoom)\b",
    re.I,
)
NEW_PREFILL = {
    "en": {"add": " Added", "support": " Added support", "can": " You can now"},
    "zh": {"add": " 新增", "support": " 新增", "can": " 现在可以"},
}


def prefill_for(subject: str, section: str, lang: str) -> str:
    if section == "new":
        if _ADD_WORDS.match(subject):
            return NEW_PREFILL[lang]["add"]
        if _SUPPORT.match(subject):
            return NEW_PREFILL[lang]["support"]
        if _USER_VERBS.match(subject):
            return NEW_PREFILL[lang]["can"]
    if _REMOVE.match(subject):
        return PREFILL[lang]["removed"]
    if _DEPRECATE.match(subject):
        return PREFILL[lang]["deprecated"]
    if section == "improved" and _IMPROVE_FREE.match(subject):
        return ""  # "Make X collapsible" reads better without a forced verb
    return PREFILL[lang].get(section, "")


def rewrite_prompt(subject: str, scope: str, lang: str, section: str = "improved") -> tuple[str, str]:
    """Return (prompt, prefill). The prefill is part of the answer."""
    head = REWRITE_ZH if lang == "zh" else REWRITE_EN
    commit = f"{scope}: {subject}" if scope else subject
    pre = prefill_for(subject, section, lang)
    # Instructions + examples are a fixed prefix (cached by llama.cpp); the
    # commit comes last, and the model turn is prefilled with "Note:".
    return _wrap(head + f"\nCommit: {commit}") + "Note:" + pre, pre.strip()


def summary_prompt(name: str, bullets: list[str], lang: str) -> str:
    tpl = SUMMARY_ZH if lang == "zh" else SUMMARY_EN
    # The product name is left out on purpose: a 1B model tends to mangle
    # it ("httpie/cli" came back as "ieee-cli"). Code adds it afterwards.
    return _wrap(tpl.format(bullets="\n".join(f"- {b}" for b in bullets)))


def health() -> bool:
    try:
        with urllib.request.urlopen(LLM_URL + "/health", timeout=3) as r:
            return json.load(r).get("status") == "ok"
    except Exception:
        return False


_KEEP_RE = re.compile(
    r"`([^`]+)`"                              # `code`
    r"|(--?[a-zA-Z][\w-]+)"                   # --flags
    r"|(<[a-zA-Z][^>]*>)"                     # <html> tags
    r"|\b([A-Za-z]\w*(?:[._]\w+)+)\b"         # dotted.names and snake_case
    r"|\b([a-z]+[A-Z]\w*|[A-Z][a-z]+[A-Z]\w*)\b"  # camelCase / PascalCase
    r"|\b(\d+(?:\.\d+)*x?)\b"                 # numbers, versions, 3x
)


def must_keep(subject: str) -> list[str]:
    """Literal tokens the rewrite has to preserve, or we fall back to rules.

    A 1B model happily turns `AsyncResponse.log_to_db` into "the response
    log". For a changelog that's worse than the raw commit, so we check.
    """
    out = []
    for m in _KEEP_RE.finditer(subject):
        tok = next(g for g in m.groups() if g)
        if len(tok) > 1 or tok.isdigit():
            out.append(tok)
    return out


# Filler a small model likes to add. Removed, not rewritten.
_FLUFF = [
    (re.compile(r",?\s*(ensuring|making sure|providing|resulting in|for a (better|smoother|more)|"
                r"to (ensure|provide|improve) (a )?(better|smoother|more))\b[^.。]*", re.I), ""),
    (re.compile(r"\b(critical|major|significant(ly)?|seamless(ly)?|greatly|robust)\s+", re.I), ""),
]


def defluff(s: str) -> str:
    for rx, rep in _FLUFF:
        s = rx.sub(rep, s)
    s = re.sub(r"\b([Aa]) (?=[aeioAEIO])", r"\1n ", s)
    return re.sub(r"\s+([.,])", r"\1", s).strip()


_BAD = re.compile(r"(commit:|note:|release notes|here is|here's|sure[,!])", re.I)


def clean_note(text: str, lang: str, subject: str = "") -> str | None:
    """Validate one model line. Return None to fall back to the rule-based note."""
    s = text.strip().strip('"“”\'*').strip()
    s = s.replace("`", "")
    for tok in must_keep(subject):
        tok = tok.lstrip("-")
        if re.fullmatch(r"\d+x", tok):  # "3x" may become "3 times" or "3 倍"
            tok = tok[:-1]
        if tok.lower() not in s.lower():
            return None
    s = re.sub(r"^[-*•]\s*", "", s)
    s = re.sub(r"\*\*", "", s)
    if lang == "en":
        s = defluff(s)
    if not s or _BAD.search(s):
        return None
    limit = 120 if lang == "zh" else 200
    if len(s) > limit or len(s) < 4:
        return None
    if lang == "en" and not re.match(r"^[A-Z0-9\-`]", s):
        s = s[0].upper() + s[1:]
    if lang == "en" and not s.endswith((".", "!", "?")):
        s += "."
    if lang == "zh" and not re.search(r"[\u4e00-\u9fff]", s):
        return None
    if lang == "zh" and not s.endswith(("。", "！", "？", ".")):
        s += "。"
    return s


def clean_summary(text: str, lang: str) -> str | None:
    s = text.strip().strip('"“”').strip()
    s = re.sub(r"\*\*", "", s)
    s = re.sub(r"#\w+", "", s).strip()
    s = re.sub(r"\s*\(\s*\)", "", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"^(what's new|更新亮点)\s*[:：]\s*", "", s, flags=re.I)
    if not s or len(s) < 15 or s.endswith((":", "：")):
        return None
    limit = 110 if lang == "zh" else 280
    if len(s) > limit:
        cut = max(s.rfind(".", 0, limit), s.rfind("。", 0, limit))
        s = s[: cut + 1] if cut > 20 else s[:limit].rstrip() + "…"
    return s
