"""株式分割・併合の検知と分割補正(NFR-2)。

daily_quotes に AdjustmentFactor ≠ 1 の行がある銘柄を「分割・併合あり」として検知し、
該当銘柄の過去データを再取得したうえで、J-Quantsが算出する分割補正済み系列
(Adjustment* 列)を主要列(四本値・出来高)へ反映してDBを更新する。

J-Quantsの `AdjustmentClose` 等は「生値 × 累積補正係数」で計算された後方調整済みの値であり、
これを主要列へ書き戻すことで、分割をまたいで連続した株価・出来高系列になる。
"""

from __future__ import annotations

import datetime as _dt
import logging
import sqlite3

from screener.api.client import JQuantsClient
from screener.fetch.daily_quotes import fetch_daily_quotes

logger = logging.getLogger("screener.fetch")


def find_split_affected_codes(conn: sqlite3.Connection) -> list[str]:
    """AdjustmentFactor ≠ 1 を含む銘柄コードを返す(分割・併合の検知)。"""
    rows = conn.execute(
        "SELECT DISTINCT code FROM daily_quotes "
        "WHERE adjustment_factor IS NOT NULL AND adjustment_factor <> 1 "
        "ORDER BY code"
    ).fetchall()
    return [r["code"] for r in rows]


def apply_adjustment(conn: sqlite3.Connection, code: str) -> int:
    """指定銘柄の主要列(四本値・出来高)を補正済み列で上書きする。更新行数を返す。

    補正済み列が NULL の行は元の値を保持する(COALESCE)。同一データでは冪等。
    """
    cur = conn.execute(
        """
        UPDATE daily_quotes
        SET open   = COALESCE(adjustment_open, open),
            high   = COALESCE(adjustment_high, high),
            low    = COALESCE(adjustment_low, low),
            close  = COALESCE(adjustment_close, close),
            volume = COALESCE(adjustment_volume, volume)
        WHERE code = ?
        """,
        (code,),
    )
    conn.commit()
    return cur.rowcount


def detect_and_adjust(
    client: JQuantsClient,
    conn: sqlite3.Connection,
    *,
    reference_date: _dt.date | None = None,
) -> list[str]:
    """分割・併合を検知し、該当銘柄を再取得・補正する。補正した銘柄コード一覧を返す。

    Args:
        client: J-Quantsクライアント(再取得に使用)。
        conn: 対象DB接続。
        reference_date: 再取得の期間基準日。省略時は本日。
    """
    affected = find_split_affected_codes(conn)
    for code in affected:
        # 過去データを再取得し、最新の補正済み系列(Adjustment*)を取得する。
        fetch_daily_quotes(client, conn, codes=[code], reference_date=reference_date)
        apply_adjustment(conn, code)
    if affected:
        logger.info("分割補正: %d銘柄を再取得・補正しました %s", len(affected), affected)
    return affected
