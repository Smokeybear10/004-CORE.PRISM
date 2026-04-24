"""
Equal-weighted long/short basket backtest.

Fetches adjusted closes from Yahoo Finance and reports daily PnL plus Sharpe,
hit rate, and max drawdown. Note: this file lives under backtest/, which
CLAUDE.md assigns to Person 4 — coordinate before merging.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import yfinance as yf

from schema import BacktestResult

TRADING_DAYS_PER_YEAR = 252
DateLike = date | str


def backtest_basket(
    longs: list[str],
    start_date: DateLike,
    end_date: DateLike,
    shorts: list[str] | None = None,
    *,
    strategy_name: str = "equal_weight_basket",
    initial_capital: float = 1.0,
) -> tuple[BacktestResult, pd.DataFrame]:
    """
    Equal-weighted basket over [start_date, end_date). Each position gets 1/N of
    the notional; longs contribute +weight, shorts -weight. Gross exposure = 1.

    Returns (metrics, panel). panel columns: portfolio_return, cumulative_pnl, equity.
    """
    shorts = shorts or []
    if not longs and not shorts:
        raise ValueError("basket is empty: provide at least one long or short ticker")

    tickers = list(dict.fromkeys(longs + shorts))
    raw = yf.download(
        tickers,
        start=start_date,
        end=end_date,
        auto_adjust=True,
        progress=False,
    )
    if raw.empty:
        raise ValueError(
            f"no price data returned for {tickers} in {start_date}..{end_date}"
        )

    prices = raw["Close"]
    if isinstance(prices, pd.Series):
        prices = prices.to_frame(name=tickers[0])
    prices = prices.reindex(columns=tickers).dropna(how="all").ffill()

    rets = prices.pct_change(fill_method=None).dropna(how="all").fillna(0.0)

    n = len(longs) + len(shorts)
    weights = pd.Series(0.0, index=tickers)
    for t in longs:
        weights[t] += 1.0 / n
    for t in shorts:
        weights[t] -= 1.0 / n

    portfolio_ret = rets.dot(weights)
    equity = initial_capital * (1.0 + portfolio_ret).cumprod()
    cumulative_pnl = equity - initial_capital

    daily_mean = float(portfolio_ret.mean())
    daily_std = float(portfolio_ret.std(ddof=1))
    sharpe = (
        float(np.sqrt(TRADING_DAYS_PER_YEAR) * daily_mean / daily_std)
        if daily_std > 0
        else 0.0
    )
    peak = equity.cummax()
    max_dd = float((equity / peak - 1.0).min())
    hit_rate = float((portfolio_ret > 0).mean())

    result = BacktestResult(
        strategy_name=strategy_name,
        n_trades=len(longs) + len(shorts),
        sharpe=sharpe,
        hit_rate=hit_rate,
        avg_return=daily_mean,
        max_drawdown=max_dd,
        notes=(
            f"{len(portfolio_ret)} trading days, "
            f"{len(longs)} longs, {len(shorts)} shorts"
        ),
    )
    panel = pd.DataFrame(
        {
            "portfolio_return": portfolio_ret,
            "cumulative_pnl": cumulative_pnl,
            "equity": equity,
        }
    )
    return result, panel
