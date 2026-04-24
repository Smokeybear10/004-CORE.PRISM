"""
Price data loading, adjustment, and significant-move detection.

Reads from the private HF repo `BridgewaterAIHackathon/BW-AI-Hackathon`
(see docs/hf_schemas.md for file layout). Caches locally under data/cache/.

Public API:
    load_prices(tickers, as_of)           -> pd.DataFrame
    load_splits(ticker, as_of)            -> pd.DataFrame
    load_dividends(ticker, as_of)         -> pd.DataFrame
    adjust_for_splits(prices, splits)     -> pd.DataFrame
    detect_moves(prices, ...)             -> list[PriceMove]
"""
from __future__ import annotations

from prices.yahoo_loader import (
    adjust_for_splits,
    load_dividends,
    load_prices,
    load_splits,
)
from prices.price_moves import detect_moves

__all__ = [
    "load_prices",
    "load_splits",
    "load_dividends",
    "adjust_for_splits",
    "detect_moves",
]
