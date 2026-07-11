"""fetch パイプライン統合と cli fetch サブコマンドのテスト(ネットワーク非依存)。"""

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
        if url.endswith(config.AUTH_REFRESH_ENDPOINT):
            return HttpResponse(200, {"idToken": "t"})
        if url.endswith(config.LISTED_INFO_ENDPOINT):
            return HttpResponse(200, {"info": [{"Code": "1301", "CompanyName": "極洋"}]})
        if url.endswith(config.DAILY_QUOTES_ENDPOINT):
            return HttpResponse(
                200,
                {
                    "daily_quotes": [
                        {"Code": "1301", "Date": "2026-07-10", "Close": 100.0,
                         "AdjustmentFactor": 1.0}
                    ]
                },
            )
        if url.endswith(config.STATEMENTS_ENDPOINT):
            return HttpResponse(
                200, {"statements": [{"DisclosureNumber": "d1", "LocalCode": "1301"}]}
            )
        raise AssertionError(url)

    return JQuantsClient(api_key="rt", transport=handler, rate_limiter=limiter, time_func=clock.time)


def test_run_fetch_calls_full_pipeline():
    conn = connect(":memory:")
    init_db(conn)
    summary = run_fetch(_client(), conn, reference_date=REF)
    assert summary["listed_info"] == 1
    assert summary["daily_quotes"] == 1
    assert summary["statements"] == 1
    assert summary["adjusted_codes"] == 0
    # 各テーブルにデータが入っている。
    assert conn.execute("SELECT COUNT(*) c FROM listed_info").fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM daily_quotes").fetchone()["c"] == 1


def test_cli_verify_complete_returns_0(monkeypatch, tmp_path, capsys):
    db = tmp_path / "screener.db"
    monkeypatch.setenv(config.DB_PATH_ENV, str(db))
    conn = connect(db)
    init_db(conn)
    # 完全性を満たす最小データ(52週分の平日カレンダー)。
    frm = REF - dt.timedelta(weeks=config.LOOKBACK_WEEKS)
    cal = []
    d = frm
    while d <= REF:
        if d.weekday() < 5:
            cal.append(d.isoformat())
        d += dt.timedelta(days=1)
    upsert(conn, "listed_info", [{"code": "1301"}])
    upsert(conn, "daily_quotes", [{"code": "1301", "date": x, "close": 1.0} for x in cal])
    upsert(conn, "statements", [{"disclosure_number": "d1", "code": "1301"}])
    conn.close()

    # verify は基準日=本日で走るため、本日を REF に固定する。
    monkeypatch.setattr(cli, "verify_data", lambda conn: __import__(
        "screener.fetch", fromlist=["verify_data"]
    ).verify_data(conn, reference_date=REF))

    rc = cli.main(["fetch", "--verify"])
    assert rc == 0
    assert "完全" in capsys.readouterr().out


def test_cli_verify_incomplete_returns_1_with_report(monkeypatch, tmp_path, capsys):
    db = tmp_path / "screener.db"
    monkeypatch.setenv(config.DB_PATH_ENV, str(db))
    conn = connect(db)
    init_db(conn)  # 空DB
    conn.close()

    rc = cli.main(["fetch", "--verify"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "欠損" in err
    assert "-" in err  # 欠損レポートの箇条書き


def test_cli_fetch_runs_pipeline(monkeypatch, tmp_path, capsys):
    db = tmp_path / "screener.db"
    monkeypatch.setenv(config.DB_PATH_ENV, str(db))
    # 実クライアント構築とパイプラインをスタブに差し替え(ネットワーク非依存)。
    monkeypatch.setattr(cli, "JQuantsClient", lambda: object())
    monkeypatch.setattr(
        cli,
        "run_fetch",
        lambda client, conn: {
            "listed_info": 1,
            "daily_quotes": 2,
            "statements": 3,
            "adjusted_codes": 0,
        },
    )
    rc = cli.main(["fetch"])
    assert rc == 0
    assert "fetch完了" in capsys.readouterr().out


def test_cli_fetch_runtime_error_is_one_line(monkeypatch, tmp_path, capsys):
    db = tmp_path / "screener.db"
    monkeypatch.setenv(config.DB_PATH_ENV, str(db))

    def boom():
        raise RuntimeError("認証に失敗しました")

    monkeypatch.setattr(cli, "JQuantsClient", boom)
    rc = cli.main(["fetch"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "エラー:" in err
    assert "Traceback" not in err
