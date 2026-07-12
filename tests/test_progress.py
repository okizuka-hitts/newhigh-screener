"""進行状況表示(NS-18)のテスト: TTY描画・非TTYフォールバック・ETA。

時刻・出力先・isattyを注入して決定的に検証する。ネットワーク非依存。
"""

import datetime as dt
import io
import logging

from helpers import FakeClock

from screener import config
from screener.api.client import HttpResponse, JQuantsClient
from screener.api.rate_limiter import RateLimiter
from screener.db import connect, init_db, upsert
from screener.fetch import ProgressReporter, run_fetch


def test_tty_shows_percent_bar_and_eta():
    clock = FakeClock()
    stream = io.StringIO()
    r = ProgressReporter(total=4, label="差分取得", stream=stream, time_func=clock.time, isatty=True)
    clock.now = 10.0  # 1件目まで10秒経過
    r.advance()  # 1/4 = 25%
    out = stream.getvalue()
    assert "25%" in out
    assert "(1/4)" in out
    assert "[" in out and "]" in out  # プログレスバー
    assert "#" in out and "-" in out  # 一部進捗・一部未了
    assert "残り" in out  # 残り推定時間
    # 1件10秒 → 残り3件で約30秒。
    assert "~30s" in out
    # TTYは \r で行を上書きし、改行しない。
    assert out.startswith("\r")
    assert "\n" not in out


def test_tty_finish_appends_newline_and_100pct():
    clock = FakeClock()
    stream = io.StringIO()
    r = ProgressReporter(total=2, label="x", stream=stream, time_func=clock.time, isatty=True)
    r.advance()
    r.finish()
    out = stream.getvalue()
    assert "100%" in out
    assert out.endswith("\n")


def test_non_tty_falls_back_to_log_lines(caplog):
    clock = FakeClock()
    stream = io.StringIO()
    r = ProgressReporter(total=2, label="差分取得", stream=stream, time_func=clock.time, isatty=False)
    with caplog.at_level(logging.INFO, logger="screener.fetch"):
        r.advance()
        r.advance()
        r.finish()
    # 非TTYではstreamへ書かず、ログへ進捗が残る。
    assert stream.getvalue() == ""
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("進捗" in m and "50%" in m for m in messages)
    assert any("進捗完了" in m and "100%" in m for m in messages)


def test_isatty_autodetected_from_stream():
    # streamにisatty()があればそれを使う(明示指定なし)。
    class FakeTTY(io.StringIO):
        def isatty(self):
            return True

    r = ProgressReporter(total=1, stream=FakeTTY())
    r.advance()
    assert r._isatty is True


def test_eta_none_before_first_advance():
    r = ProgressReporter(total=3, stream=io.StringIO(), isatty=True)
    # 0件完了時はETA不明表示。
    assert "~--s" in r._format()


def _client():
    clock = FakeClock()
    limiter = RateLimiter(
        config.effective_rate_limit_per_min(), time_func=clock.time, sleep_func=clock.sleep
    )

    def handler(method, url, params=None, headers=None, body=None):
        params = params or {}
        if url.endswith(config.LISTED_INFO_ENDPOINT):
            return HttpResponse(200, {"data": [{"Code": "13010", "CoName": "極洋"}]})
        if url.endswith(config.CALENDAR_ENDPOINT):
            return HttpResponse(200, {"data": [
                {"Date": "2026-07-09", "HolDiv": "1"}, {"Date": "2026-07-10", "HolDiv": "1"}
            ]})
        if url.endswith(config.DAILY_QUOTES_ENDPOINT):
            return HttpResponse(200, {"data": [
                {"Code": "13010", "Date": params["date"], "C": 100.0, "AdjFactor": 1.0}
            ]})
        if url.endswith(config.STATEMENTS_ENDPOINT):
            return HttpResponse(200, {"data": []})
        raise AssertionError(url)

    return JQuantsClient(api_key="k", transport=handler, rate_limiter=limiter)


def test_pipeline_emits_progress_during_fetch(caplog):
    # 非TTY(テスト実行)ではpipelineの進捗がログに残る。追加APIリクエストは発生しない。
    conn = connect(":memory:")
    init_db(conn)
    upsert(conn, "daily_quotes", [{"code": "13010", "date": "2026-07-08", "close": 1.0}])
    with caplog.at_level(logging.INFO, logger="screener.fetch"):
        summary = run_fetch(_client(), conn, reference_date=dt.date(2026, 7, 10))
    assert summary["mode"] == "incremental"
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("進捗" in m for m in messages)
    assert any("進捗完了" in m for m in messages)
