"""
Disk cache for raw model Attribution outputs.

Key is sha256 of (ticker, move_date, ablation_name, prompt_version). The
prompt_version must change whenever the attribution prompt template changes,
or the cache will silently serve stale results and you will not be able to
trust any "the prompt change helped" claim.

Cache dir: .cache/eval/<key>.json, gitignored per project convention.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Optional

from schema import Attribution


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DIR = REPO_ROOT / ".cache" / "eval"


def cache_key(
    ticker: str,
    move_date: date,
    ablation_name: str,
    prompt_version: str,
) -> str:
    payload = f"{ticker}|{move_date.isoformat()}|{ablation_name}|{prompt_version}"
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def cache_path(key: str, cache_dir: Path = DEFAULT_CACHE_DIR) -> Path:
    return cache_dir / f"{key}.json"


def read(
    ticker: str,
    move_date: date,
    ablation_name: str,
    prompt_version: str,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> Optional[Attribution]:
    path = cache_path(cache_key(ticker, move_date, ablation_name, prompt_version), cache_dir)
    if not path.exists():
        return None
    with open(path) as f:
        raw = json.load(f)
    return Attribution(**raw)


def write(
    attribution: Attribution,
    ablation_name: str,
    prompt_version: str,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = cache_key(
        attribution.ticker,
        attribution.move_date,
        ablation_name,
        prompt_version,
    )
    path = cache_path(key, cache_dir)
    with open(path, "w") as f:
        # pydantic v2: model_dump_json handles dates cleanly
        f.write(attribution.model_dump_json(indent=2))
    return path


def clear(cache_dir: Path = DEFAULT_CACHE_DIR) -> int:
    """Delete every cached Attribution. Returns count of files removed."""
    if not cache_dir.exists():
        return 0
    n = 0
    for p in cache_dir.glob("*.json"):
        p.unlink()
        n += 1
    return n
