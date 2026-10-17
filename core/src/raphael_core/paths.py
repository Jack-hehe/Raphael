"""Where Raphael keeps its files: the database and the downloaded speech models.

Both live under one data directory. `RAPHAEL_DATA_DIR` overrides it (the tests and the end-to-end
runs set it); otherwise it is Raphael's folder in the platform's per-user application data.
"""

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    """The data directory, created on first use."""
    override = os.environ.get("RAPHAEL_DATA_DIR")
    if override:
        path = Path(override)
    elif sys.platform == "win32":
        path = Path(os.environ["APPDATA"]) / "Raphael"
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "Raphael"
    else:
        raise RuntimeError(f"No default data directory on {sys.platform}: set RAPHAEL_DATA_DIR")
    path.mkdir(parents=True, exist_ok=True)
    return path


def models_dir() -> Path:
    return data_dir() / "models"


def db_path() -> Path:
    return data_dir() / "raphael.db"
