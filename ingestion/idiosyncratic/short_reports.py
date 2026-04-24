"""
Short-seller research report ingestion.

Scrapes research reports from prominent short-sellers targeting specific stocks.
Each report becomes a text chunk with stable chunk_id for model attribution.
"""

from datetime import date
from typing import List

from schema import ShortReport


# Known short-seller research publishers
SHORT_SELLERS = [
    "Scorpion Capital",
    "Hindenburg Research",
    "Muddy Waters Research",
    "Citron Research",
    "Kerrisdale Capital",
    "Spruce Point Capital"
]


def fetch_short_reports(publisher: str, as_of: date) -> List[ShortReport]:
    """
    Fetch all short-seller reports from a specific publisher as of a given date.

    Args:
        publisher: Publisher name (e.g., "Muddy Waters Research", "Hindenburg Research")
        as_of: Only return reports with publication_date <= as_of (no foreknowledge)

    Returns:
        List of ShortReport objects with stable chunk_ids

    Sources:
        - Publisher websites
        - Twitter/X feeds
        - SEC filings (when available)
    """
    # TODO: Implement scraping logic per publisher
    # - Scorpion Capital: website + Twitter
    # - Hindenburg Research: website RSS feed
    # - Muddy Waters: website archive
    # - Citron Research: website + Twitter
    # - Kerrisdale Capital: website reports section
    # - Spruce Point Capital: website research page
    #
    # For each report:
    # - Extract target ticker from title/content
    # - Parse publication date
    # - Extract thesis text (main allegation)
    # - Generate stable chunk_id format: "short_report_{publisher_slug}_{ticker}_{date}"
    # - Filter by publication_date <= as_of

    if publisher not in SHORT_SELLERS:
        raise ValueError(f"Unknown publisher: {publisher}. Known publishers: {SHORT_SELLERS}")

    raise NotImplementedError(f"Placeholder - implement {publisher} scraping")


def fetch_all_short_reports(as_of: date) -> List[ShortReport]:
    """
    Fetch all short-seller reports from all known publishers as of a given date.

    Args:
        as_of: Only return reports with publication_date <= as_of (no foreknowledge)

    Returns:
        Consolidated list of ShortReport objects from all publishers
    """
    # TODO: Implement bulk collection across all publishers
    # - Iterate through SHORT_SELLERS list
    # - Call fetch_short_reports() for each
    # - Consolidate results
    # - Remove duplicates by chunk_id
    # - Sort by publication_date descending
    raise NotImplementedError("Placeholder - implement bulk short report collection")


def extract_target_ticker(title: str, content: str) -> str:
    """
    Extract target stock ticker from report title and content.

    Args:
        title: Report title
        content: Report body text

    Returns:
        Stock ticker symbol (e.g., "AAPL", "TSLA")

    Algorithm:
        1. Look for explicit ticker mentions in title ($TICKER, TICKER:, etc.)
        2. Search for company name to ticker mapping
        3. Extract from regulatory filing references
    """
    # TODO: Implement ticker extraction logic
    # - Regex patterns for common ticker formats
    # - Company name -> ticker lookup table
    # - Handle edge cases (delisted stocks, name changes)
    raise NotImplementedError("Placeholder - implement ticker extraction")


def generate_chunk_id(publisher: str, ticker: str, publication_date: date) -> str:
    """
    Generate stable chunk_id for a short report.

    Args:
        publisher: Publisher name
        ticker: Target ticker
        publication_date: Publication date

    Returns:
        Stable chunk_id (e.g., "short_report_muddy_waters_BABA_2024-01-15")
    """
    # TODO: Implement stable ID generation
    # - Normalize publisher name to slug (spaces -> underscores, lowercase)
    # - Format: "short_report_{publisher_slug}_{ticker}_{date}"
    # - Ensure uniqueness for multiple reports same day
    raise NotImplementedError("Placeholder - implement chunk ID generation")


def save_reports_to_parquet(reports: List[ShortReport], filepath: str) -> None:
    """
    Save short reports to parquet format.

    Args:
        reports: List of ShortReport objects
        filepath: Output parquet file path
    """
    # TODO: Implement parquet serialization
    # - Convert ShortReport objects to DataFrame
    # - Ensure UTC timestamps in ISO-8601 format
    # - Write to parquet with appropriate compression
    # - Include token_count for each thesis_text
    raise NotImplementedError("Placeholder - implement parquet output")