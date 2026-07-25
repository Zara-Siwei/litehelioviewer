from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
UPLOAD_DIR = DATA_DIR / "uploads"
LOG_DIR = DATA_DIR / "logs"
STATIC_DIR = ROOT / "static"

# Interpreter used when spawning helper processes; always the one running the app.
PYTHON_EXE = sys.executable

for directory in (DATA_DIR, CACHE_DIR, UPLOAD_DIR, LOG_DIR, STATIC_DIR):
    directory.mkdir(parents=True, exist_ok=True)
