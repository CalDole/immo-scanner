"""Score weights saved next to the data, so every page ranks with the same numbers."""

import hashlib
import json
from pathlib import Path

from immo_scanner.config import get_settings
from immo_scanner.explore.scoring import Weights


def _path(path: Path | None) -> Path:
    return path or get_settings().score_weights_path


def load_weights(path: Path | None = None) -> Weights:
    """Saved weights, or equal weights when the file is missing or unreadable."""
    try:
        raw = json.loads(_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Weights()
    return Weights.from_dict(raw)


def save_weights(weights: Weights, path: Path | None = None) -> None:
    target = _path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(weights.to_dict(), indent=2, sort_keys=True), encoding="utf-8")


def weights_version(path: Path | None = None) -> str:
    """Changes whenever the saved weights change. Page caches take it as an argument."""
    payload = json.dumps(load_weights(path).to_dict(), sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
