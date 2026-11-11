"""Logging: one line per event on stderr. Under the installed service that lands in the journal:

    journalctl --user -u aku -e

Set AKU_LOG=DEBUG for more detail.
"""

from __future__ import annotations

import logging
import os
import sys


def setup() -> None:
    level = getattr(logging, os.environ.get("AKU_LOG", "INFO").upper(), logging.INFO)
    logging.basicConfig(level=level, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "openai", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
