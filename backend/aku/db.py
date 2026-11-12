"""SQLite: only what spans sessions (voiceprints, vocabulary sets, replacement rules)."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS voiceprints (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    space TEXT NOT NULL,          -- embedding model the vector belongs to
    embedding BLOB NOT NULL,      -- float32
    session TEXT,
    created TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS my_voice (     -- the owner's own voice (interview mode)
    id INTEGER PRIMARY KEY,
    space TEXT NOT NULL,
    embedding BLOB NOT NULL,
    session TEXT,
    created TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS vocab_sets (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    terms TEXT NOT NULL DEFAULT '[]'   -- JSON list, newest last
);
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY,
    wrong TEXT NOT NULL UNIQUE COLLATE NOCASE,
    right TEXT NOT NULL,
    created TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_lock = threading.Lock()


@contextmanager
def connect():
    """One short-lived connection per operation; a lock keeps writers in line."""
    config.ensure_dirs()
    with _lock:
        con = sqlite3.connect(config.db_path())
        con.row_factory = sqlite3.Row
        try:
            con.executescript(SCHEMA)
            yield con
            con.commit()
        finally:
            con.close()
