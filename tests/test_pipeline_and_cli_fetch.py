"""fetch パイプライン統合と cli fetch サブコマンドのテスト(V2・ネットワーク非依存)。"""

import datetime as dt

from helpers import FakeClock

from screener import cli, config
from screener.api.client import HttpResponse, JQuantsClient
from screener.api.rate_limiter import RateLimiter
from screener.db import connect, init_db, upsert
from screener.fetch import run_fetch

REF = dt.date(2026, 7, 10)


def _client():
    clock = FakeClock()
    limiter = RateLimiter(
        config.effective_rate_limit_per_min(), time_func=clock.time, sleep_func=clock.sleep
    )

    def handler(method, url, params=None, headers=None, body=None):
        if url.endswith(config.LISTED_INFO_ENDPOINT):
            return HttpResponse(200, {"data": [{"Code": "13010", "CoName": "極洋"}]})
        if url.endswith(config.DAILY_QUOTES_ENDPOINT):
            return HttpResponse(
                200,
                {"data": [{"Code": "13010", "Date": "2026-07-10", "C": 100.0, "AdjFactor": 1.0}]},
            )
        if url.endswith(config.STATEMENTS_ENDPOINT):
            return HttpResponse(200, {"data": [{"DiscNo": "d1", "Code": "13010"}]})
        raise AssertionError(url)

    return JQuantsClient(api_key="k", transport=handler, rate_limiter=limiter)


def test_run_fetch_calls_full_pipeline():
    conn = connect(":memory:")
    init_db(conn)
    summary = run_fetch(_client(), conn, reference_date=REF)
    assert summary["listed_info"] == 1
    assert summary["daily_quotes"] == 1
    assert summary["statements"] == 1
    assert summary["adjusted_codes"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM listed_info").fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM daily_quotes").fetchone()["c"] == 1


def test_cli_verify_complete_returns_0(monkeypatch, tmp_path, capsys):
    db = tmp_path / "screener.db"
    monkeypatch.setenv(config.DB_PATH_ENV, str(db))
    conn = connect(db)
    init_db(conn)
    frm = REF - dt.timedelta(weeks=config.LOOKBACK_WEEKS)
    cal = []
    d = frm
    while d <= REF:
        if d.weekday() < 5:
            cal.append(d.isoformat())
        d += dt.timedelta(days=1)
    upsert(conn, "listed_info", [{"code": "13010"}])
    upsert(conn, "daily_quotes", [{"code": "13010", "date": x, "close": 1.0} for x in cal])
    upsert(conn, "statements", [{"disclosure_number": "d1", "code": "13010"}])
    conn.close()

    import screener.fetch as fetch_mod
    monkeypatch.setattr(cli, "verify_data", lambda conn: fetch_mod.verify_data(conn, reference_date=REF))

    assert cli.main(["fetch", "--verify"]) == 0
    assert "完全" in capsys.readouterr().out


def test_cli_verify_incomplete_returns_1_with_report(monkeypatch, tmp_path, capsys):
    db = tmp_path / "screener.db"
    monkeypatch.setenv(config.DB_PATH_ENV, str(db))
    conn = connect(db)
    init_db(conn)
    conn.close()

    assert cli.main(["fetch", "--verify"]) == 1
    err = capsys.readouterr().err
    assert "欠損" in err
    assert "-" in err


def test_cli_fetch_runs_pipeline(monkeypatch, tmp_path, capsys):
    db = tmp_path / "screener.db"
    monkeypatch.setenv(config.DB_PATH_ENV, str(db))
    monkeypatch.setattr(cli, "JQuantsClient", lambda: object())
    monkeypatch.setattr(
        cli, "run_fetch",
        lambda client, conn: {
            "listed_info": 1, "daily_quotes": 2, "statements": 3, "adjusted_codes": 0
        },
    )
    assert cli.main(["fetch"]) == 0
    assert "fetch完了" in capsys.readouterr().out


def test_cli_fetch_runtime_error_is_one_line(monkeypatch, tmp_path, capsys):
    db = tmp_path / "screener.db"
    monkeypatch.setenv(config.DB_PATH_ENV, str(db))

    def boom():
        raise RuntimeError("APIキーが未設定です")

    monkeypatch.setattr(cli, "JQuantsClient", boom)
    assert cli.main(["fetch"]) == 1
    err = capsys.readouterr().err
    assert "エラー:" in err
    assert "Traceback" not in err
