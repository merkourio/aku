"""Custom vocabulary: named term sets (fed to Whisper as a prompt) and replacement rules."""

from __future__ import annotations

import json
import re

from .. import db

PROMPT_CHARS = 700  # ~224 Whisper tokens is the hard limit; stay under it
DEFAULT_SET = "General"
# Without any prompt Whisper drops punctuation and capitals on some chunks; a neutral sentence in the
# spoken language fixes most. An English sentence would nudge other languages towards English,
# so languages without an entry (and automatic mode) get no style prompt.
STYLE_PROMPT = "Hello. Okay, so here is what I think."
STYLE_PROMPTS = {"en": STYLE_PROMPT, "da": "Hej. Okay, her er, hvad jeg tænker."}


def list_sets() -> list[dict]:
    with db.connect() as con:
        rows = con.execute("SELECT name, terms FROM vocab_sets ORDER BY name COLLATE NOCASE").fetchall()
    return [{"name": r["name"], "terms": json.loads(r["terms"])} for r in rows]


def list_rules() -> list[dict]:
    with db.connect() as con:
        rows = con.execute("SELECT id, wrong, right FROM rules ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def _clean_terms(terms: list[str]) -> list[str]:
    seen, out = set(), []
    for t in terms:
        t = " ".join(str(t).split())
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def replace_all(sets: list[dict], rules: list[dict]) -> None:
    """PUT /vocab: the given sets and rules become the whole vocabulary."""
    with db.connect() as con:
        con.execute("DELETE FROM vocab_sets")
        con.execute("DELETE FROM rules")
        for s in sets:
            name = " ".join(str(s.get("name", "")).split())
            if name:
                con.execute(
                    "INSERT OR REPLACE INTO vocab_sets (name, terms) VALUES (?, ?)",
                    (name, json.dumps(_clean_terms(s.get("terms") or []))),
                )
        for r in rules:
            wrong, right = str(r.get("wrong", "")).strip(), str(r.get("right", "")).strip()
            if wrong and right and wrong != right:
                con.execute("INSERT OR REPLACE INTO rules (wrong, right) VALUES (?, ?)", (wrong, right))


def add_term(term: str, set_name: str = DEFAULT_SET) -> None:
    with db.connect() as con:
        row = con.execute("SELECT terms FROM vocab_sets WHERE name = ?", (set_name,)).fetchone()
        terms = _clean_terms((json.loads(row["terms"]) if row else []) + [term])
        con.execute("INSERT OR REPLACE INTO vocab_sets (name, terms) VALUES (?, ?)", (set_name, json.dumps(terms)))


def add_rule(wrong: str, right: str, set_name: str | None = None) -> dict:
    """Fix-from-transcript: remember the correction and teach Whisper the right form."""
    wrong, right = wrong.strip(), right.strip()
    if not wrong or not right or wrong == right:
        raise ValueError("Need a misheard word and a different correction.")
    with db.connect() as con:
        con.execute("INSERT OR REPLACE INTO rules (wrong, right) VALUES (?, ?)", (wrong, right))
    add_term(right, set_name or DEFAULT_SET)
    return {"wrong": wrong, "right": right, "set": set_name or DEFAULT_SET}


def build_prompt(active_sets: list[str], names: list[str] | None = None, language: str = "en") -> str:
    """Whisper's initial prompt: active terms, newest first, within the token limit."""
    wanted = {s.lower() for s in active_sets}
    terms: list[str] = []
    for s in list_sets():
        if s["name"].lower() in wanted:
            terms += reversed(s["terms"])
    terms = _clean_terms(terms + list(names or []))
    out, size = [], len("Glossary: ")
    for t in terms:
        if size + len(t) + 2 > PROMPT_CHARS:
            break
        out.append(t)
        size += len(t) + 2
    if not out:
        return STYLE_PROMPTS.get(language, "")
    # the word "Glossary" is English: only use it when English is being spoken
    return ("Glossary: " if language == "en" else "") + ", ".join(out) + "."


def compile_rules(rules: list[dict] | None = None) -> list[tuple[re.Pattern, str]]:
    rules = list_rules() if rules is None else rules
    # longest first, so "Collect app" wins over "Collect"
    ordered = sorted(rules, key=lambda r: -len(r["wrong"]))
    return [(re.compile(rf"(?<!\w){re.escape(r['wrong'])}(?!\w)", re.IGNORECASE), r["right"]) for r in ordered]


def apply_rules(text: str, compiled: list[tuple[re.Pattern, str]]) -> str:
    for pattern, right in compiled:
        text = pattern.sub(lambda m, right=right: right, text)
    return text
