"""fetch パイプラインのオーケストレーション。

上場銘柄一覧 → 日足 → 財務 → 分割補正 を順に実行する。cli はこの関数へ委譲し、
自身は薄く保つ(code-style.md)。日足・財務は営業日ループ(by-date)で一括取得するため、
取引カレンダーは1度だけ取得して両者で共有する。
"""

from __future__ import annotations

import datetime as _dt
import logging
import sqlite3

from screener.api.client import JQuantsClient
from screener.fetch.adjust import detect_and_adjust
from screener.fetch.calendar import trading_days
from screener.fetch.daily_quotes import fetch_daily_quotes, fetch_window
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
    reference_date = reference_date or _dt.date.today()
    from_date, to_date = fetch_window(reference_date)

    listed = fetch_listed_info(client, conn)
    days = trading_days(client, from_date, to_date)
    quotes = fetch_daily_quotes(client, conn, reference_date=reference_date, dates=days)
    statements = fetch_statements(client, conn, reference_date=reference_date, dates=days)
    adjusted = detect_and_adjust(conn)  # API再取得なし(補正済み系列は一括取得済み)
    summary = {
        "listed_info": listed,
        "trading_days": len(days),
        "daily_quotes": quotes,
        "statements": statements,
        "adjusted_codes": len(adjusted),
    }
    logger.info("fetch完了: %s", summary)
    return summary
