"""分割補正のテスト(V2日足フィールド・検知・再取得・補正値一致・冪等・注入E2E)。"""

import datetime as dt

from helpers import FakeClock

from screener import config
from screener.api.client import HttpResponse, JQuantsClient
from screener.api.rate_limiter import RateLimiter
from screener.db import connect, init_db
from screener.fetch import detect_and_adjust, fetch_daily_quotes, find_split_affected_codes

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
