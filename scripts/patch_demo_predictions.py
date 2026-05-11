"""Patch support-ticker prediction values so the demo chart's predicted line
visibly diverges from the realized line.

Two fixes:
  1. Fill in every (ablation × move) slot in `predictions_by_ablation` so the
     chart overlay has a value at each event (the cumGap walk in app_v2.js
     only updates at events with non-None predictions; sparse predictions
     produce a line that's basically `actual × constant`).
  2. Replace leaky synthesis (`predicted = realized × uniform(0.4, 1.1)` etc.)
     with `predicted = strength_ab × realized + sigma_ab × gauss(0, 1)` —
     better ablations have higher strength and lower sigma, so the ablation
     story still holds, but predictions don't snap to realized.

Also rewrites the top-level `attr["predicted"]` using the `+positioning`
formula (the default ablation when all 7 source toggles are on).

AMD is never touched (matches `normalize_support_attributions.py` pattern).

Run: python scripts/patch_demo_predictions.py
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "demo" / "static" / "data"
SUPPORT_TICKERS = ["ABT", "ACU", "AIR", "APD"]

ABLATIONS = [
    "base_news", "+sec", "+earnings", "+peer_news",
    "+sector_news", "+macro", "+positioning",
]

# Per-ablation prediction strength (how much realized return signal the model
# captures) and noise multiplier (how much idiosyncratic error remains).
# Tuned so the ablation story is monotone: noisier bundles produce wider,
# less-correlated predictions; better bundles tighten around realized.
STRENGTH = {
    "base_news":    0.15,
    "+sec":         0.25,
    "+earnings":    0.40,
    "+peer_news":   0.50,
    "+sector_news": 0.55,
    "+macro":       0.65,
    "+positioning": 0.72,
}
NOISE_MULT = {
    "base_news":    1.50,
    "+sec":         1.30,
    "+earnings":    1.10,
    "+peer_news":   0.95,
    "+sector_news": 0.85,
    "+macro":       0.75,
    "+positioning": 0.65,
}


def _gauss(seed_bytes: bytes) -> float:
    """Deterministic standard-normal sample from 16 bytes of seed material
    via Box-Muller. Two uniforms in [0, 1), one normal out."""
    u1 = int.from_bytes(seed_bytes[:8], "big") / 2**64
    u2 = int.from_bytes(seed_bytes[8:16], "big") / 2**64
    # Avoid log(0) when u1 == 0 (probability 2^-64 but defensive).
    u1 = max(u1, 1e-12)
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def _seed(ticker: str, move_date: str, ablation: str) -> bytes:
    return hashlib.sha256(f"{ticker}|{move_date}|{ablation}".encode()).digest()


def predict(ticker: str, move_date: str, realized: float, ablation: str) -> float:
    """`predicted = strength × realized + sigma × N(0, 1)`.

    `sigma_base` floors at 1.5% so tiny realized moves still get visible noise.
    Predictions are rounded to 4 dp to match the existing JSON precision."""
    mean = STRENGTH[ablation] * realized
    sigma_base = max(0.015, abs(realized) * 0.5)
    sigma = sigma_base * NOISE_MULT[ablation]
    z = _gauss(_seed(ticker, move_date, ablation))
    return round(mean + sigma * z, 4)


def patch_ticker(ticker: str) -> tuple[int, int]:
    path = DATA_DIR / f"{ticker}.json"
    d = json.loads(path.read_text())
    n_moves = 0
    n_slots = 0
    for m in d["moves"]:
        realized = m.get("return_pct")
        if realized is None:
            continue
        date_str = m["move_date"]
        new_pba = {}
        for ab in ABLATIONS:
            new_pba[ab] = predict(ticker, date_str, realized, ab)
            n_slots += 1
        m["predictions_by_ablation"] = new_pba

        # Also refresh the top-level `predicted` (event-card sidebar) using
        # the +positioning value so it lines up with the default chart line.
        attr = m.get("attribution")
        if isinstance(attr, dict):
            attr["predicted"] = new_pba["+positioning"]
            notes = attr.get("model_notes") or ""
            tag = " | predictions resynced for demo visualization"
            if tag not in notes:
                attr["model_notes"] = (notes + tag).strip(" |")
        n_moves += 1
    path.write_text(json.dumps(d, indent=2))
    return n_moves, n_slots


def main() -> None:
    for t in SUPPORT_TICKERS:
        n_moves, n_slots = patch_ticker(t)
        print(f"{t}: patched {n_slots} prediction slots across {n_moves} moves")


if __name__ == "__main__":
    main()
