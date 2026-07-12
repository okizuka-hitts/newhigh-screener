"""株式分割・併合の検知と分割補正(NFR-2)。

daily_quotes に AdjustmentFactor ≠ 1 の行がある銘柄を「分割・併合あり」として検知し、
J-Quantsが算出する分割補正済み系列(Adjustment* 列)を主要列(四本値・出来高)へ反映して
DBを更新する。

**取得との関係(NS-12)**: 日足は by-date 一括取得(営業日ごとに当日全銘柄)で取得され、
分割銘柄の補正済み系列(Adjustment*)も毎回の一括取得で最新化される。したがって分割補正は
**追加のAPI再取得を行わず**、DB上で Adjustment* を主要列へ適用するだけでよい(実行毎の
不要な銘柄別再取得=NFR-1予算の無駄を排除)。新規分割は一括取得が Adjustment* を更新し、
本処理が再適用することで反映される。
"""

from __future__ import annotations

import logging
import sqlite3

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


def detect_and_adjust(conn: sqlite3.Connection) -> list[str]:
    """分割・併合を検知し、該当銘柄に分割補正を適用する。補正した銘柄コード一覧を返す。

    追加のAPI再取得は行わない(補正済み系列は日足の一括取得で取得済み)。DB操作のみ。

    Args:
        conn: 対象DB接続(直近の日足取得で Adjustment* が最新化されていること)。
    """
    affected = find_split_affected_codes(conn)
    for code in affected:
        apply_adjustment(conn, code)
    if affected:
        logger.info(
            "分割補正: %d銘柄に補正適用(API再取得なし・一括取得の補正済み系列を反映) %s",
            len(affected),
            affected,
        )
    return affected
