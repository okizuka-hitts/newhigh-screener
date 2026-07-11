"""日足四本値・出来高(daily_quotes)の取得と保存。

J-Quants `/prices/daily_quotes` を銘柄別に取得し、直近3か月＋過去52週を満たす期間で
`daily_quotes` テーブルへ冪等にupsertする。分割補正に必要な AdjustmentFactor 等の列も
併せて保存する(補正処理本体はNS-10)。
"""

from __future__ import annotations

import datetime as _dt
import logging
import sqlite3
from collections.abc import Iterable, Sequence
from typing import Any

from screener import config
from screener.api.client import JQuantsClient
from screener.db import upsert

logger = logging.getLogger("screener.fetch")

_DATA_KEY = "daily_quotes"


def fetch_window(reference: _dt.date) -> tuple[str, str]:
    """取得期間 (from, to) をISO文字列で返す。

    52週分(=直近3か月を包含)を遡り、基準日までを対象とする。
    """
    earliest = reference - _dt.timedelta(weeks=config.LOOKBACK_WEEKS)
    return earliest.isoformat(), reference.isoformat()


def _row_from_api(item: dict[str, Any]) -> dict[str, Any]:
    """APIの1日足レコードを daily_quotes の行に変換する。"""
    return {
        "code": item.get("Code"),
        "date": item.get("Date"),
        "open": item.get("Open"),
        "high": item.get("High"),
        "low": item.get("Low"),
        "close": item.get("Close"),
        "volume": item.get("Volume"),
        "adjustment_factor": item.get("AdjustmentFactor"),
        "adjustment_open": item.get("AdjustmentOpen"),
        "adjustment_high": item.get("AdjustmentHigh"),
        "adjustment_low": item.get("AdjustmentLow"),
        "adjustment_close": item.get("AdjustmentClose"),
        "adjustment_volume": item.get("AdjustmentVolume"),
    }


def _codes_from_db(conn: sqlite3.Connection) -> list[str]:
    return [r["code"] for r in conn.execute("SELECT code FROM listed_info ORDER BY code")]


def fetch_daily_quotes(
    client: JQuantsClient,
    conn: sqlite3.Connection,
    *,
    codes: Sequence[str] | None = None,
    reference_date: _dt.date | None = None,
) -> int:
    """日足を取得してDBへ保存する。保存件数(行数)を返す。

    Args:
        client: J-Quantsクライアント。
        conn: 保存先DB接続。
        codes: 対象銘柄コード。省略時は listed_info の全銘柄。
        reference_date: 期間の基準日。省略時は本日。
    """
    reference_date = reference_date or _dt.date.today()
    from_date, to_date = fetch_window(reference_date)
    target: Iterable[str] = codes if codes is not None else _codes_from_db(conn)

    total = 0
    for code in target:
        items = client.get_paginated(
            config.DAILY_QUOTES_ENDPOINT,
            _DATA_KEY,
            {"code": code, "from": from_date, "to": to_date},
        )
        rows = [_row_from_api(i) for i in items if i.get("Code") and i.get("Date")]
        total += upsert(conn, "daily_quotes", rows)
    logger.info("日足: %s〜%s / 保存 %d件", from_date, to_date, total)
    return total
