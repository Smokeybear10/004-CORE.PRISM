"""
FINRA short interest data ingestion.

Fetches bi-monthly short interest data from FINRA for all tickers,
computing days-to-cover and float percentage metrics.
"""

from datetime import date
from typing import List, Optional

from schema import ShortInterestRecord


def fetch_short_interest(ticker: Optional[str], as_of: date) -> List[ShortInterestRecord]:
    """
    Fetch FINRA short interest data for a specific ticker or all tickers.

    Args:
        ticker: Stock ticker symbol, or None for all tickers
        as_of: Only return records with settlement_date <= as_of (no foreknowledge)

    Returns:
        List of ShortInterestRecord objects with calculated metrics

    Source: FINRA Short Interest files (bi-monthly, free)
    """
    # TODO: Implement FINRA short interest ingestion
    # - Download FINRA short interest files (bi-monthly)
    # - Parse pipe-delimited format
    # - Calculate days_to_cover and float_short_percent
    # - Filter by settlement_date <= as_of
    # - If ticker specified, filter by ticker symbol
    raise NotImplementedError("Placeholder - implement FINRA short interest ingestion")


def download_finra_files(start_date: date, end_date: date) -> List[str]:
    """
    Download FINRA short interest files for a date range.

    Args:
        start_date: Start of date range
        end_date: End of date range

    Returns:
        List of downloaded file paths

    FINRA publishes short interest data twice monthly:
        - Mid-month settlement (around 15th)
        - End-month settlement (last business day)
    """
    # TODO: Implement FINRA file download
    # - Identify bi-monthly settlement dates in range
    # - Download from FINRA website
    # - Handle file format variations over time
    # - Return local file paths for parsing
    raise NotImplementedError("Placeholder - implement FINRA file download")


def parse_finra_file(filepath: str) -> List[ShortInterestRecord]:
    """
    Parse a single FINRA short interest file.

    Args:
        filepath: Path to FINRA short interest file

    Returns:
        List of ShortInterestRecord objects

    File format:
        - Pipe-delimited text file
        - Columns: Date, Symbol, ShortInterest, AverageVolume, DaysToCover, etc.
        - One record per ticker per settlement date
    """
    # TODO: Implement FINRA file parsing
    # - Read pipe-delimited format
    # - Extract settlement_date, ticker, shares_short
    # - Calculate derived metrics (days_to_cover, float_short_percent)
    # - Handle missing/invalid data gracefully
    # - Return structured records
    raise NotImplementedError("Placeholder - implement FINRA file parsing")


def calculate_derived_metrics(shares_short: int, avg_daily_volume: Optional[int], shares_float: Optional[int]) -> tuple[Optional[float], Optional[float]]:
    """
    Calculate days-to-cover and float percentage from raw data.

    Args:
        shares_short: Number of shares sold short
        avg_daily_volume: Average daily trading volume
        shares_float: Total shares in public float

    Returns:
        Tuple of (days_to_cover, float_short_percent)
    """
    # TODO: Implement metric calculations
    # - days_to_cover = shares_short / avg_daily_volume (if volume > 0)
    # - float_short_percent = shares_short / shares_float (if float available)
    # - Handle division by zero and missing data
    # - Return None for invalid calculations
    raise NotImplementedError("Placeholder - implement derived metric calculations")


def identify_squeeze_candidates(records: List[ShortInterestRecord],
                              min_days_to_cover: float = 10.0,
                              min_float_short: float = 0.20) -> List[str]:
    """
    Identify potential short squeeze candidates based on metrics.

    Args:
        records: List of short interest records
        min_days_to_cover: Minimum days to cover threshold
        min_float_short: Minimum short float percentage threshold

    Returns:
        List of ticker symbols meeting squeeze criteria

    Criteria:
        - High days to cover (low liquidity)
        - High short float percentage
        - Recent increase in short interest
    """
    # TODO: Implement squeeze candidate identification
    # - Filter by days_to_cover >= min_days_to_cover
    # - Filter by float_short_percent >= min_float_short
    # - Look for increasing short interest trends
    # - Return ranked list of squeeze candidates
    raise NotImplementedError("Placeholder - implement squeeze candidate analysis")


def save_short_interest_to_parquet(records: List[ShortInterestRecord], filepath: str) -> None:
    """
    Save short interest data to parquet format.

    Args:
        records: List of ShortInterestRecord objects
        filepath: Output parquet file path
    """
    # TODO: Implement parquet serialization
    # - Convert ShortInterestRecord objects to DataFrame
    # - Ensure UTC timestamps in ISO-8601 format
    # - Write to parquet with appropriate compression
    # - Include calculated metrics (days_to_cover, float_short_percent)
    raise NotImplementedError("Placeholder - implement parquet output")