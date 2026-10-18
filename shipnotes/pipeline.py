"""Commits in, release notes out. Yields progress events so the UI can stream."""
from __future__ import annotations

import os
import time
from typing import Iterator

from . import llm
from .rules import SECTIONS, Commit, classify, dedupe, fallback_note

MAX_COMMITS = int(os.environ.get("SHIPNOTES_MAX_COMMITS", "300"))
MAX_REWRITES = int(os.environ.get("SHIPNOTES_MAX_REWRITES", "15"))

HEADINGS = {
    "en": {"new": "New", "improved": "Improved", "fixed": "Fixed", "breaking": "Breaking"},
    "zh": {"new": "新功能", "improved": "改进", "fixed": "修复", "breaking": "不兼容变更"},
}
BREAKING_PREFIX = {"en": "**Breaking:** ", "zh": "**不兼容：** "}


def build_markdown(title: str, commits: list[Commit], lang: str, link_base: str = "") -> str:
    h = HEADINGS[lang]
    out = [f"## {title}", ""] if title else []
    for sec in SECTIONS:
        items = [c for c in commits if c.kept and c.section == sec]
        if not items:
            continue
        out.append(f"### {h[sec]}")
        for c in items:
            line = (BREAKING_PREFIX[lang] if c.breaking else "") + c.note
            if c.pr and link_base:
                line += f" ([#{c.pr}]({link_base}/pull/{c.pr}))"
            elif c.pr:
                line += f" (#{c.pr})"
            out.append(f"- {line}")
        out.append("")
    if len(out) <= 2:
        out.append("_No customer-facing changes in this range._" if lang == "en" else "_此范围内没有面向用户的改动。_")
    return "\n".join(out).rstrip() + "\n"


def run(items: list[dict], lang: str = "en", title: str = "", link_base: str = "",
        use_model: bool = True) -> Iterator[dict]:
    t0 = time.time()
    lang = "zh" if lang == "zh" else "en"
    items = items[:MAX_COMMITS]
    commits = dedupe([classify(i) for i in items])
    kept = [c for c in commits if c.kept]
    dropped = [c for c in commits if not c.kept]
    # Breaking changes first, then features, then the rest, so the model's
    # budget is spent on what readers care about most.
    order = {"new": 1, "fixed": 2, "improved": 3}
    budget = sorted(kept, key=lambda c: (not c.breaking, order[c.section]))[:MAX_REWRITES]
    budget_ids = {id(c) for c in budget}

    yield {
        "type": "plan",
        "total": len(commits), "kept": len(kept), "dropped": len(dropped),
        "rewrites": len(budget) if use_model else 0,
        "dropped_samples": [{"subject": c.raw[:120], "why": c.dropped_reason} for c in dropped[:40]],
    }

    stats = {"prompt_tokens": 0, "cached_tokens": 0, "gen_tokens": 0,
             "prompt_ms": 0.0, "gen_ms": 0.0, "model_calls": 0, "fallbacks": 0}

    def account(r: dict) -> None:
        stats["prompt_tokens"] += r["prompt_n"]
        stats["cached_tokens"] += r["cached_n"]
        stats["gen_tokens"] += r["gen_n"]
        stats["prompt_ms"] += r["prompt_ms"]
        stats["gen_ms"] += r["gen_ms"]
        stats["model_calls"] += 1

    model_ok = use_model and llm.health()
    for c in kept:
        note = None
        if model_ok and id(c) in budget_ids:
            try:
                prompt, pre = llm.rewrite_prompt(c.subject, c.scope, lang, c.section)
                r = llm.complete(prompt, n_predict=40)
                account(r)
                text = pre + r["text"]  # the model continues after the prefill
                note = llm.clean_note(text, lang, c.subject)
                c.rewritten = note is not None
                if note is None:
                    stats["fallbacks"] += 1
            except Exception:
                model_ok = False
                stats["fallbacks"] += 1
        c.note = note or fallback_note(c)
        yield {"type": "item", "section": c.section, "note": c.note, "original": c.raw,
               "breaking": c.breaking, "pr": c.pr, "by_model": c.rewritten}

    md = build_markdown(title, commits, lang, link_base)

    # The announcement leads with what's new; breaking changes stay in the notes.
    bullets = [c.note for c in sorted(kept, key=lambda c: (c.breaking, order[c.section]))][:8]
    summary = None
    yield {"type": "stage", "stage": "summary"}
    if model_ok and bullets:
        try:
            stop = ["<end_of_turn>", "\n\n"]
            r = llm.complete(llm.summary_prompt(title, bullets, lang), n_predict=80, stop=stop,
                             temperature=0.4)
            account(r)
            summary = llm.clean_summary(r["text"], lang)
            if summary and title:
                summary = (f"{title} 已发布。" if lang == "zh" else f"{title} is out. ") + summary
        except Exception:
            summary = None
    if not summary:
        summary = _fallback_summary(title, kept, lang)
    yield {"type": "summary", "text": summary}

    secs = time.time() - t0
    gen_tps = stats["gen_tokens"] / (stats["gen_ms"] / 1000) if stats["gen_ms"] else 0.0
    pp_tps = stats["prompt_tokens"] / (stats["prompt_ms"] / 1000) if stats["prompt_ms"] else 0.0
    yield {
        "type": "done", "markdown": md, "summary": summary,
        "stats": {**stats, "seconds": round(secs, 2), "gen_tps": round(gen_tps, 2),
                  "prompt_tps": round(pp_tps, 2), "cost_usd": 0.0,
                  "model": "gemma-3-1b-it Q4_K_M" if stats["model_calls"] else "rules only"},
    }


def _fallback_summary(title: str, kept: list[Commit], lang: str) -> str:
    n = {s: sum(1 for c in kept if c.section == s) for s in SECTIONS}
    if lang == "zh":
        return f"{title or '新版本'}已发布：{n['new']} 项新功能、{n['improved']} 项改进、{n['fixed']} 项修复。"
    return (f"{title or 'A new release'} is out: {n['new']} new, "
            f"{n['improved']} improved, {n['fixed']} fixed.")
