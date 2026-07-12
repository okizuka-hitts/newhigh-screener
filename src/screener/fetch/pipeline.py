"""fetch パイプラインのオーケストレーション。

上場銘柄一覧 → 日足 → 財務 → 分割補正 を順に実行する。cli はこの関数へ委譲し、
自身は薄く保つ(code-style.md)。日足・財務は営業日ループ(by-date)で一括取得するため、
取引カレンダーは1度だけ取得して両者で共有する。

**日次差分モード(NS-15)**: DBに最終取得営業日がある場合、その翌日〜基準日の
**未取得の営業日分のみ**を取得する(取得済み期間への重複リクエストを発行しない)。
新規営業日が無い場合(休場日に実行 or 既に最新まで取得済み)は、データ取得エンドポイント
(銘柄一覧・日足・財務)へのリクエストを一切発行せずスキップして返す。
DBが空(初回)なら従来どおり52週分のフル取得にフォールバックする。

休場スキップ判定は取引カレンダー(`/markets/calendar`)1件のみで行う。カレンダーは
休場判定に不可欠なメタデータであり、受け入れ基準の「データ取得リクエスト」(＝日足・
財務・銘柄一覧のデータ取得)には含めない(NS-16 ADR)。
"""

from __future__ import annotations

import datetime as _dt
import logging
import sqlite3
import time as _time

from screener.api.client import JQuantsClient
from screener.fetch.adjust import detect_and_adjust
from screener.fetch.calendar import trading_days
from screener.fetch.daily_quotes import fetch_daily_quotes, fetch_window
from screener.fetch.listed_info import fetch_listed_info
from screener.fetch.statements import fetch_statements

logger = logging.getLogger("screener.fetch")


def last_stored_trading_date(conn: sqlite3.Connection) -> _dt.date | None:
    """DBに保存済みの最新の日足営業日を返す。1件も無ければ None(初回=フル取得)。"""
    row = conn.execute("SELECT MAX(date) AS d FROM daily_quotes").fetchone()
    value = row["d"] if row is not None else None
    return _dt.date.fromisoformat(value) if value else None


def _skipped_summary(*, reason: str, message: str, elapsed: float) -> dict[str, object]:
    """データ取得を発行しなかった(休場/最新済み)場合のサマリ。"""
    logger.info("fetch skip(%s): %s / 所要 %.2fs", reason, message, elapsed)
    return {
        "skipped": True,
        "skip_reason": reason,
        "message": message,
        "listed_info": 0,
        "trading_days": 0,
        "daily_quotes": 0,
        "statements": 0,
        "adjusted_codes": 0,
        "elapsed_seconds": elapsed,
    }


def _resolve_target_days(
    client: JQuantsClient,
    conn: sqlite3.Connection,
    reference_date: _dt.date,
) -> tuple[str, list[str] | None, str]:
    """取得対象の営業日リストと実行モードを決める。

    Returns:
        (mode, days, message): days が None のときはスキップ(message に理由)。
        mode は "full"(初回フル) / "incremental"(差分) / "skip"。
    """
    last = last_stored_trading_date(conn)
    if last is None:
        from_date, to_date = fetch_window(reference_date)
        return "full", trading_days(client, from_date, to_date), ""

    incr_from = last + _dt.timedelta(days=1)
    if incr_from > reference_date:
        # 既に基準日(=最終取得営業日)まで取得済み。カレンダーも引かない。
        return "skip", None, f"最新の営業日({last.isoformat()})まで取得済みです。取得をスキップしました。"

    days = trading_days(client, incr_from.isoformat(), reference_date.isoformat())
    if not days:
        # 差分ウィンドウに営業日が無い ＝ 基準日は休場日(to=基準日を含めても不在のため)。
        return "skip", None, f"本日({reference_date.isoformat()})は休場日です。データ取得をスキップしました。"
    return "incremental", days, ""


def run_fetch(
    client: JQuantsClient,
    conn: sqlite3.Connection,
    *,
    reference_date: _dt.date | None = None,
) -> dict[str, int | object]:
    """取得パイプラインを通しで実行し、各段の件数サマリを返す。

    日次差分モードで動作し、未取得の営業日分のみを取得する。休場日・最新済みでは
    データ取得を発行せずスキップする(`skipped=True`)。DBが空なら52週フル取得。
    """
    reference_date = reference_date or _dt.date.today()
    started = _time.monotonic()

    mode, days, message = _resolve_target_days(client, conn, reference_date)
    if days is None:
        return _skipped_summary(
            reason="skip", message=message, elapsed=_time.monotonic() - started
        )

    listed = fetch_listed_info(client, conn)
    quotes = fetch_daily_quotes(client, conn, reference_date=reference_date, dates=days)
    statements = fetch_statements(client, conn, reference_date=reference_date, dates=days)
    adjusted = detect_and_adjust(conn)  # API再取得なし(補正済み系列は一括取得済み)
    elapsed = _time.monotonic() - started
    summary: dict[str, int | object] = {
        "skipped": False,
        "mode": mode,
        "listed_info": listed,
        "trading_days": len(days),
        "daily_quotes": quotes,
        "statements": statements,
        "adjusted_codes": len(adjusted),
        "elapsed_seconds": elapsed,
    }
    logger.info("fetch完了(%s): %s / 所要 %.2fs", mode, summary, elapsed)
    return summary
