"""fetch パイプラインのオーケストレーション。

上場銘柄一覧 → 日足 → 財務 → 分割補正 を順に実行する。cli はこの関数へ委譲し、
自身は薄く保つ(code-style.md)。
"""

from __future__ import annotations

import datetime as _dt
import logging
import sqlite3

from screener.api.client import JQuantsClient
from screener.fetch.adjust import detect_and_adjust
from screener.fetch.daily_quotes import fetch_daily_quotes
from screener.fetch.listed_info import fetch_listed_info
from screener.fetch.statements import fetch_statements

logger = logging.getLogger("screener.fetch")


def run_fetch(
    client: JQuantsClient,
    conn: sqlite3.Connection,
    *,
    reference_date: _dt.date | None = None,
) -> dict[str, int]:
    """取得パイプラインを通しで実行し、各段の件数サマリを返す。"""
    listed = fetch_listed_info(client, conn)
    quotes = fetch_daily_quotes(client, conn, reference_date=reference_date)
    statements = fetch_statements(client, conn)
    adjusted = detect_and_adjust(client, conn, reference_date=reference_date)
    summary = {
        "listed_info": listed,
        "daily_quotes": quotes,
        "statements": statements,
        "adjusted_codes": len(adjusted),
    }
    logger.info("fetch完了: %s", summary)
    return summary
