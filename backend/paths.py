"""Writable user data lives beside the EXE, never inside its bundled files."""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    override = os.environ.get("MONEY_ENGINE_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "data"
    return PROJECT_ROOT / "data"


def frontend_dist() -> Path:
    bundle_root = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
    return bundle_root / "frontend" / "dist"


def images_dir() -> Path:
    return data_dir() / "images"


def logs_dir() -> Path:
    override = os.environ.get("MONEY_ENGINE_LOGS_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "logs"
    return PROJECT_ROOT / "logs"
