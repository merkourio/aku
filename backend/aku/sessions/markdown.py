"""Markdown export. The Markdown is an output only; Aku never reads it back."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .merge import display_name, participants


def clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def line_md(start: float, name: str, text: str) -> str:
    return f"**[{clock(start)}] {name}:** {text}"


def render(meta: dict, lines: list[dict], speakers: dict) -> str:
    user = meta.get("user_name") or "Me"
    created = datetime.fromisoformat(meta["created"]) if meta.get("created") else datetime.now()
    title = meta.get("title") or f"Call {created:%Y-%m-%d %H:%M}"
    out = [
        f"# {title}",
        "",
        f"- **Date:** {created:%Y-%m-%d %H:%M}",
        f"- **Duration:** {clock(meta.get('duration') or 0)}",
        f"- **Participants:** {', '.join(participants(lines, speakers, user)) or user}",
        "",
    ]
    for line in lines:
        out.append(line_md(line["start"], display_name(line["speaker"], speakers, user), line["text"]))
        out.append("")
    return "\n".join(out)


def safe_filename(text: str) -> str:
    text = re.sub(r'[\\/:*?"<>|#^\[\]]', " ", text)
    return " ".join(text.split())[:120].strip(". ") or "Call"


def output_path(folder: Path, meta: dict, current: Path | None = None) -> Path:
    """<date> <title>.md in the folder; never overwrites another session's file."""
    created = datetime.fromisoformat(meta["created"]) if meta.get("created") else datetime.now()
    stem = safe_filename(f"{created:%Y-%m-%d %H%M} {meta.get('title') or 'Call'}")
    path = folder / f"{stem}.md"
    n = 2
    while path.exists() and path != current:
        path = folder / f"{stem} ({n}).md"
        n += 1
    return path
