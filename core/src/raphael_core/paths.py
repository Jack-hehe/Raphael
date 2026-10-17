"""Where Raphael keeps its files: the database and the downloaded speech models.

Both live under one data directory. `RAPHAEL_DATA_DIR` overrides it (the tests and the end-to-end
runs set it); otherwise it is Raphael's folder in the platform's per-user application data.
`RAPHAEL_MODELS_DIR` puts the models somewhere else: an end-to-end run uses an empty data directory
but the models that are already downloaded.
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


def models_dir(base: Path | None = None) -> Path:
    """Where the speech models are: `RAPHAEL_MODELS_DIR` if it is set, else `models` in `base`
    (the data directory when there is no `base`). Only a path: nothing is created."""
    override = os.environ.get("RAPHAEL_MODELS_DIR")
    if override:
        return Path(override)
    return (data_dir() if base is None else base) / "models"


def db_path(base: Path | None = None) -> Path:
    """The database: `raphael.db` in `base` (the data directory when there is no `base`)."""
    return (data_dir() if base is None else base) / "raphael.db"
