"""日次差分フェッチ(NS-16)のテスト: 差分判定・休場スキップ・所要時間ログ。

ネットワーク非依存。リクエストログを注入トランスポートで記録し、
「取得済み期間への重複リクエストが0件」「休場日はデータ取得0件」を機械確認する。
"""

import datetime as dt
import logging

from helpers import FakeClock

from screener import cli, config
from screener.api.client import HttpResponse, JQuantsClient
from screener.api.rate_limiter import RateLimiter
from screener.db import connect, init_db, upsert
from screener.fetch import last_stored_trading_date, run_fetch


def _weekdays(from_iso: str, to_iso: str) -> list[str]:
    """[from, to] の平日(=擬似営業日)を返す。"""
    d = dt.date.fromisoformat(from_iso)
    end = dt.date.fromisoformat(to_iso)
    out: list[str] = []
    while d <= end:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def _client_with_log(log: list[tuple[str, dict]]):
    """全リクエストを (endpoint, params) で記録するクライアントを返す。"""
    clock = FakeClock()
    limiter = RateLimiter(
        config.effective_rate_limit_per_min(), time_func=clock.time, sleep_func=clock.sleep
    )

    def handler(method, url, params=None, headers=None, body=None):
        params = params or {}
        if url.endswith(config.LISTED_INFO_ENDPOINT):
            log.append(("listed", dict(params)))
            return HttpResponse(200, {"data": [{"Code": "13010", "CoName": "極洋"}]})
        if url.endswith(config.CALENDAR_ENDPOINT):
            log.append(("calendar", dict(params)))
            days = _weekdays(params["from"], params["to"])
            return HttpResponse(200, {"data": [{"Date": d, "HolDiv": "1"} for d in days]})
        if url.endswith(config.DAILY_QUOTES_ENDPOINT):
            log.append(("daily", dict(params)))
            return HttpResponse(
                200,
                {"data": [{"Code": "13010", "Date": params["date"], "C": 100.0, "AdjFactor": 1.0}]},
            )
        if url.endswith(config.STATEMENTS_ENDPOINT):
            log.append(("statements", dict(params)))
            return HttpResponse(200, {"data": [{"DiscNo": f"d-{params['date']}", "Code": "13010"}]})
        raise AssertionError(url)

    return JQuantsClient(api_key="k", transport=handler, rate_limiter=limiter)


def _seed_quotes(conn, dates: list[str]) -> None:
    upsert(conn, "daily_quotes", [{"code": "13010", "date": d, "close": 1.0} for d in dates])


def test_last_stored_trading_date_empty_is_none():
    conn = connect(":memory:")
    init_db(conn)
    assert last_stored_trading_date(conn) is None


def test_incremental_fetches_only_new_days():
    # 既に 07-06〜07-08 を取得済み。基準日 07-10 で差分 07-09,07-10 のみ取得する。
    conn = connect(":memory:")
    init_db(conn)
    _seed_quotes(conn, ["2026-07-06", "2026-07-07", "2026-07-08"])
    log: list[tuple[str, dict]] = []
    client = _client_with_log(log)

    summary = run_fetch(client, conn, reference_date=dt.date(2026, 7, 10))

    assert summary["skipped"] is False
    assert summary["mode"] == "incremental"
    daily_dates = {p["date"] for name, p in log if name == "daily"}
    assert daily_dates == {"2026-07-09", "2026-07-10"}
    # 取得済み期間(07-06〜07-08)への日足リクエストは0件。
    assert daily_dates.isdisjoint({"2026-07-06", "2026-07-07", "2026-07-08"})
    # 財務も差分の営業日のみ。
    stmt_dates = {p["date"] for name, p in log if name == "statements"}
    assert stmt_dates == {"2026-07-09", "2026-07-10"}


def test_holiday_skip_issues_no_data_requests(capsys):
    # 最終取得=金曜。基準日=土曜(休場日)。データ取得0件でスキップ・exit相当。
    conn = connect(":memory:")
    init_db(conn)
    _seed_quotes(conn, ["2026-07-10"])  # 2026-07-10 は金曜
    log: list[tuple[str, dict]] = []
    client = _client_with_log(log)

    summary = run_fetch(client, conn, reference_date=dt.date(2026, 7, 11))  # 土曜

    assert summary["skipped"] is True
    assert "休場日" in summary["message"]
    data_calls = [name for name, _ in log if name in {"listed", "daily", "statements"}]
    assert data_calls == []  # データ3エンドポイントへのリクエスト0件


def test_up_to_date_skip_issues_no_requests_at_all():
    # 基準日=最終取得日。カレンダーも含め一切リクエストしない。
    conn = connect(":memory:")
    init_db(conn)
    _seed_quotes(conn, ["2026-07-10"])
    log: list[tuple[str, dict]] = []
    client = _client_with_log(log)

    summary = run_fetch(client, conn, reference_date=dt.date(2026, 7, 10))

    assert summary["skipped"] is True
    assert "取得済み" in summary["message"]
    assert log == []  # カレンダーも引かない


def test_initial_full_fetch_when_db_empty():
    # DBが空なら52週フル取得にフォールバック(mode=full)。
    conn = connect(":memory:")
    init_db(conn)
    log: list[tuple[str, dict]] = []
    client = _client_with_log(log)

    summary = run_fetch(client, conn, reference_date=dt.date(2026, 7, 10))

    assert summary["skipped"] is False
    assert summary["mode"] == "full"
    # フル窓(52週)の営業日数ぶん日足を取得している。
    assert summary["trading_days"] > 200


def test_elapsed_time_is_logged(caplog):
    conn = connect(":memory:")
    init_db(conn)
    _seed_quotes(conn, ["2026-07-08"])
    client = _client_with_log([])
    with caplog.at_level(logging.INFO, logger="screener.fetch"):
        run_fetch(client, conn, reference_date=dt.date(2026, 7, 10))
    assert any("所要" in r.getMessage() for r in caplog.records)


def test_cli_fetch_skip_prints_message_returns_0(monkeypatch, tmp_path, capsys):
    db = tmp_path / "screener.db"
    monkeypatch.setenv(config.DB_PATH_ENV, str(db))
    monkeypatch.setattr(cli, "JQuantsClient", lambda: object())
    monkeypatch.setattr(
        cli, "run_fetch",
        lambda client, conn: {"skipped": True, "message": "本日(2026-07-11)は休場日です。"},
    )
    assert cli.main(["fetch"]) == 0
    assert "休場日" in capsys.readouterr().out
