"""
13F institutional holdings ingestion from SEC EDGAR.

Fetches quarterly 13F filings and computes holding deltas (new/increased/reduced/exited)
quarter-over-quarter for institutional funds.
"""

from datetime import date
from typing import List

from schema import HoldingRecord, HoldingDelta


def fetch_13f_holdings(fund_cik: str, as_of: date) -> List[HoldingRecord]:
    """
    Fetch all 13F holdings for a specific fund as of a given date.

    Args:
        fund_cik: SEC CIK identifier for the institutional fund
        as_of: Only return holdings with filing_date <= as_of (no foreknowledge)

    Returns:
        List of HoldingRecord objects for the fund's most recent filing <= as_of

    Source: SEC EDGAR 13F-HR filings
    """
    # TODO: Implement SEC EDGAR API integration
    # - Query SEC EDGAR for 13F-HR filings by CIK
    # - Filter by filing_date <= as_of
    # - Parse XML/SGML holdings tables
    # - Return most recent quarter's holdings
    raise NotImplementedError("Placeholder - implement SEC EDGAR 13F parsing")


def fetch_ticker_holders(ticker: str, as_of: date) -> List[HoldingRecord]:
    """
    Fetch all institutional holders of a specific ticker as of a given date.

    Args:
        ticker: Stock ticker symbol
        as_of: Only return holdings with filing_date <= as_of (no foreknowledge)

    Returns:
        List of HoldingRecord objects from all funds holding the ticker

    Source: Aggregated from SEC EDGAR 13F-HR filings
    """
    # TODO: Implement reverse lookup functionality
    # - Query all 13F filings for holdings of specific ticker
    # - Filter by filing_date <= as_of
    # - Aggregate across all funds
    # - Return consolidated holdings list
    raise NotImplementedError("Placeholder - implement ticker holder aggregation")


def compute_holding_deltas(fund_cik: str, current_quarter_end: date, prior_quarter_end: date) -> List[HoldingDelta]:
    """
    Compute quarter-over-quarter holding changes for a fund.

    This is a HoldingDelta emitter that compares two consecutive quarters
    and identifies new positions, increases, reductions, and exits.

    Args:
        fund_cik: SEC CIK identifier for the institutional fund
        current_quarter_end: End date of current quarter (e.g., 2024-03-31)
        prior_quarter_end: End date of prior quarter (e.g., 2023-12-31)

    Returns:
        List of HoldingDelta objects showing position changes

    Algorithm:
        1. Fetch holdings for both quarters
        2. Compare ticker-by-ticker
        3. Classify as NEW/INCREASED/REDUCED/EXITED
        4. Calculate share and value deltas
    """
    # TODO: Implement delta computation logic
    # - Fetch holdings for both quarters using fetch_13f_holdings()
    # - Create ticker -> HoldingRecord mappings for each quarter
    # - Compare positions and classify changes
    # - Generate HoldingDelta records with proper action types
    raise NotImplementedError("Placeholder - implement holding delta computation")


def save_holdings_to_parquet(holdings: List[HoldingRecord], filepath: str) -> None:
    """
    Save holdings data to parquet format.

    Args:
        holdings: List of HoldingRecord objects
        filepath: Output parquet file path
    """
    # TODO: Implement parquet serialization
    # - Convert HoldingRecord objects to DataFrame
    # - Ensure UTC timestamps in ISO-8601 format
    # - Write to parquet with appropriate compression
    raise NotImplementedError("Placeholder - implement parquet output")


def save_deltas_to_parquet(deltas: List[HoldingDelta], filepath: str) -> None:
    """
    Save holding deltas to parquet format.

    Args:
        deltas: List of HoldingDelta objects
        filepath: Output parquet file path
    """
    # TODO: Implement parquet serialization
    # - Convert HoldingDelta objects to DataFrame
    # - Ensure UTC timestamps in ISO-8601 format
    # - Write to parquet with appropriate compression
    raise NotImplementedError("Placeholder - implement parquet output")