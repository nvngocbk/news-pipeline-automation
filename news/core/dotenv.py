"""Minimal .env loader (stdlib only).

Parses `KEY=VALUE` lines from a .env file and exports each one to os.environ if
not already set — so cron / shell overrides always win over the file. Quoted
values (single or double) are unquoted. `#` starts a comment.

For anything richer (multi-line values, escape sequences, variable expansion)
use python-dotenv. This loader is deliberately just enough to keep secrets out
of the repo.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            count += 1
    return count
