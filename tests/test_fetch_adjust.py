"""分割補正のテスト(V2日足フィールド・検知・再取得・補正値一致・冪等・注入E2E)。"""

import datetime as dt

from helpers import FakeClock

from screener import config
from screener.api.client import HttpResponse, JQuantsClient
from screener.api.rate_limiter import RateLimiter
from screener.db import connect, init_db
from screener.fetch import (
    detect_and_adjust,
    fetch_daily_quotes,
    find_split_affected_codes,
    run_fetch,
)

REF = dt.date(2026, 7, 10)


def _client(responder):
    clock = FakeClock()
    limiter = RateLimiter(
        config.effective_rate_limit_per_min(), time_func=clock.time, sleep_func=clock.sleep
    )

    def handler(method, url, params=None, headers=None, body=None):
        return HttpResponse(200, responder(params))

    return JQuantsClient(api_key="k", transport=handler, rate_limiter=limiter)


def _conn():
    conn = connect(":memory:")
    init_db(conn)
    return conn


# 2:1分割を注入。分割前日(07-08)は生値100だが補正後(AdjC)は50、出来高は生値1000→補正後2000。
# 分割日(07-09)に AdjFactor=0.5(検知トリガ)。V2フィールド名(O/H/L/C/Vo/AdjFactor/AdjC/AdjVo)。
def _split_responder(params):
    return {
        "data": [
            {"Code": params["code"], "Date": "2026-07-08",
             "O": 100.0, "H": 100.0, "L": 100.0, "C": 100.0, "Vo": 1000.0,
             "AdjFactor": 1.0, "AdjO": 50.0, "AdjH": 50.0, "AdjL": 50.0,
             "AdjC": 50.0, "AdjVo": 2000.0},
            {"Code": params["code"], "Date": "2026-07-09",
             "O": 50.0, "H": 50.0, "L": 50.0, "C": 50.0, "Vo": 2000.0,
             "AdjFactor": 0.5, "AdjO": 50.0, "AdjH": 50.0, "AdjL": 50.0,
             "AdjC": 50.0, "AdjVo": 2000.0},
        ]
    }


def _no_split_responder(params):
    return {
        "data": [
            {"Code": params["code"], "Date": "2026-07-08",
             "O": 10.0, "H": 10.0, "L": 10.0, "C": 10.0, "Vo": 500.0,
             "AdjFactor": 1.0, "AdjC": 10.0, "AdjVo": 500.0},
        ]
    }


def test_detects_split_affected_code():
    conn = _conn()
    fetch_daily_quotes(_client(_split_responder), conn, codes=["13010"], reference_date=REF)
    assert find_split_affected_codes(conn) == ["13010"]


def test_no_split_is_not_detected():
    conn = _conn()
    fetch_daily_quotes(_client(_no_split_responder), conn, codes=["72030"], reference_date=REF)
    assert find_split_affected_codes(conn) == []


def test_adjustment_fires_and_matches_independent_calc():
    conn = _conn()
    client = _client(_split_responder)
    fetch_daily_quotes(client, conn, codes=["13010"], reference_date=REF)

    affected = detect_and_adjust(client, conn, reference_date=REF)
    assert affected == ["13010"]

    row = conn.execute(
        "SELECT close, volume FROM daily_quotes WHERE code = ? AND date = ?",
        ("13010", "2026-07-08"),
    ).fetchone()
    raw_close, cumulative_factor = 100.0, 0.5
    assert row["close"] == raw_close * cumulative_factor  # 50.0
    assert row["volume"] == 1000.0 / cumulative_factor  # 2000.0


def test_adjustment_is_idempotent():
    conn = _conn()
    client = _client(_split_responder)
    fetch_daily_quotes(client, conn, codes=["13010"], reference_date=REF)
    detect_and_adjust(client, conn, reference_date=REF)
    first = conn.execute(
        "SELECT close FROM daily_quotes WHERE code = ? AND date = ?", ("13010", "2026-07-08")
    ).fetchone()["close"]
    detect_and_adjust(client, conn, reference_date=REF)
    second = conn.execute(
        "SELECT close FROM daily_quotes WHERE code = ? AND date = ?", ("13010", "2026-07-08")
    ).fetchone()["close"]
    assert first == second == 50.0


def test_no_split_does_not_trigger_adjustment():
    conn = _conn()
    client = _client(_no_split_responder)
    fetch_daily_quotes(client, conn, codes=["72030"], reference_date=REF)
    assert detect_and_adjust(client, conn, reference_date=REF) == []
    row = conn.execute(
        "SELECT close FROM daily_quotes WHERE code = ? AND date = ?", ("72030", "2026-07-08")
    ).fetchone()
    assert row["close"] == 10.0


# --- NS-12: 分割補正の再取得を撤廃し実行毎の不要なAPI呼び出しを抑止 --------------


def _counting_client(responder):
    """呼び出し(url・params)を記録する注入クライアント。(client, calls) を返す。"""
    calls: list[dict] = []
    clock = FakeClock()
    limiter = RateLimiter(
        config.effective_rate_limit_per_min(), time_func=clock.time, sleep_func=clock.sleep
    )

    def handler(method, url, params=None, headers=None, body=None):
        calls.append({"url": url, "params": params or {}})
        return HttpResponse(200, responder(url, params or {}))

    return JQuantsClient(api_key="k", transport=handler, rate_limiter=limiter), calls


def _bydate_daily(adj_close, adj_volume):
    """by-date取得の日足レスポンダ。分割日(07-09)に AdjFactor=0.5 を返す。"""

    def _resp(url, params):
        date = params["date"]
        return {
            "data": [
                {
                    "Code": "13010", "Date": date,
                    "O": 100.0, "H": 100.0, "L": 100.0, "C": 100.0, "Vo": 1000.0,
                    "AdjFactor": 0.5 if date == "2026-07-09" else 1.0,
                    "AdjO": adj_close, "AdjH": adj_close, "AdjL": adj_close,
                    "AdjC": adj_close, "AdjVo": adj_volume,
                }
            ]
        }

    return _resp


def test_detect_and_adjust_makes_no_refetch_api_calls():
    # by-date一括取得で補正済み列がDBに入っている前提では、detect_and_adjustは
    # 銘柄別の再取得APIを一切呼ばない(AC1)。
    conn = _conn()
    seed_client, _ = _counting_client(_bydate_daily(50.0, 2000.0))
    fetch_daily_quotes(seed_client, conn, dates=["2026-07-08", "2026-07-09"], reference_date=REF)
    assert find_split_affected_codes(conn) == ["13010"]

    adj_client, adj_calls = _counting_client(_bydate_daily(50.0, 2000.0))
    affected = detect_and_adjust(adj_client, conn, reference_date=REF)

    assert affected == ["13010"]
    assert adj_calls == []  # 再取得API 0回
    row = conn.execute(
        "SELECT close, volume FROM daily_quotes WHERE code=? AND date=?", ("13010", "2026-07-08")
    ).fetchone()
    assert row["close"] == 50.0  # 補正済み列が主要列へ反映
    assert row["volume"] == 2000.0


def test_new_split_refires_recorrection_via_bydate():
    # 新規分割(Adjustment*の更新)を注入すると、by-date一括取得→apply_adjustmentで
    # 再補正が発火する(AC2)。
    conn = _conn()
    c1, _ = _counting_client(_bydate_daily(50.0, 2000.0))
    fetch_daily_quotes(c1, conn, dates=["2026-07-08", "2026-07-09"], reference_date=REF)
    detect_and_adjust(c1, conn, reference_date=REF)
    first = conn.execute(
        "SELECT close FROM daily_quotes WHERE code=? AND date=?", ("13010", "2026-07-08")
    ).fetchone()["close"]
    assert first == 50.0

    # 後方調整が更に進み AdjC=25 に更新される
    c2, _ = _counting_client(_bydate_daily(25.0, 4000.0))
    fetch_daily_quotes(c2, conn, dates=["2026-07-08", "2026-07-09"], reference_date=REF)
    detect_and_adjust(c2, conn, reference_date=REF)
    second = conn.execute(
        "SELECT close FROM daily_quotes WHERE code=? AND date=?", ("13010", "2026-07-08")
    ).fetchone()["close"]
    assert second == 25.0  # 再補正が発火


def _full_pipeline_responder(url, params):
    if url.endswith(config.LISTED_INFO_ENDPOINT):
        return {"data": [{"Code": "13010", "CoName": "極洋"}]}
    if url.endswith(config.CALENDAR_ENDPOINT):
        return {"data": [{"Date": "2026-07-10", "HolDiv": "1"}]}
    if url.endswith(config.DAILY_QUOTES_ENDPOINT):
        # by-date(date指定)。分割銘柄を含める。
        return {
            "data": [
                {"Code": "13010", "Date": params["date"], "C": 100.0, "Vo": 1000.0,
                 "AdjFactor": 0.5, "AdjC": 50.0, "AdjVo": 2000.0}
            ]
        }
    if url.endswith(config.STATEMENTS_ENDPOINT):
        return {"data": [{"DiscNo": "d1", "Code": "13010"}]}
    raise AssertionError(url)


def test_run_fetch_has_no_bycode_daily_refetch():
    # パイプライン全体でも日足取得は by-date のみで、分割補正のための by-code 再取得が
    # 発生しない(AC1・二重取得の撤廃)。
    conn = _conn()
    client, calls = _counting_client(_full_pipeline_responder)
    summary = run_fetch(client, conn, reference_date=REF)

    assert summary["adjusted_codes"] == 1  # 分割銘柄を検知・補正している
    daily_calls = [c for c in calls if c["url"].endswith(config.DAILY_QUOTES_ENDPOINT)]
    assert daily_calls, "日足取得が行われていること"
    # by-code(codeパラメータ)の再取得は1件も無く、すべて by-date(dateパラメータ)。
    assert all("code" not in c["params"] for c in daily_calls)
    assert all("date" in c["params"] for c in daily_calls)
    row = conn.execute(
        "SELECT close FROM daily_quotes WHERE code=? AND date=?", ("13010", "2026-07-10")
    ).fetchone()
    assert row["close"] == 50.0  # 補正が主要列へ反映
