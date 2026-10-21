"""Where Raphael keeps its files: the database and the downloaded speech models.

The small user data (database, settings) lives in the data directory: Raphael's folder in the
platform's per-user application data, which on Windows is Roaming and may follow a profile from
one PC to another. The speech models are large and can be downloaded again, so they live in the
machine-local folder instead (`%LOCALAPPDATA%\\Raphael` on Windows, `~/Library/Caches/Raphael` on
macOS). `RAPHAEL_DATA_DIR` overrides the data directory and, with it, puts the models in its
`models` folder (the tests and the end-to-end runs set it). `RAPHAEL_MODELS_DIR` puts the models
somewhere else: an end-to-end run uses an empty data directory but the models that are already
downloaded.
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


def local_dir() -> Path:
    """Raphael's folder in the per-user machine-local data (not roaming). Only a path: nothing is
    created."""
    if sys.platform == "win32":
        return Path(os.environ["LOCALAPPDATA"]) / "Raphael"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "Raphael"
    raise RuntimeError(f"No default local directory on {sys.platform}: set RAPHAEL_DATA_DIR")


def models_dir(base: Path | None = None) -> Path:
    """Where the speech models are: `RAPHAEL_MODELS_DIR` if it is set, else `models` in `base`.
    Without a `base` they are in the data directory when `RAPHAEL_DATA_DIR` is set, and in the
    machine-local folder otherwise. Only a path: nothing is created."""
    override = os.environ.get("RAPHAEL_MODELS_DIR")
    if override:
        return Path(override)
    if base is not None:
        return base / "models"
    if os.environ.get("RAPHAEL_DATA_DIR"):
        return data_dir() / "models"
    return local_dir() / "models"


def db_path(base: Path | None = None) -> Path:
    """The database: `raphael.db` in `base` (the data directory when there is no `base`)."""
    return (data_dir() if base is None else base) / "raphael.db"
