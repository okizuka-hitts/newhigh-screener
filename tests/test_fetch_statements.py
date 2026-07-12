"""財務データ(V2 /fins/summary) 取得・保存のテスト(by-date / by-code)。"""

import datetime as dt

from helpers import FakeClock

from screener import config
from screener.api.client import HttpResponse, JQuantsClient
from screener.api.rate_limiter import RateLimiter
from screener.db import connect, init_db
from screener.fetch import fetch_statements

REF = dt.date(2026, 7, 10)


def _client(handler):
    clock = FakeClock()
    limiter = RateLimiter(
        config.effective_rate_limit_per_min(), time_func=clock.time, sleep_func=clock.sleep
    )

    def transport(method, url, params=None, headers=None, body=None):
        return HttpResponse(200, handler(url, params or {}))

    return JQuantsClient(api_key="k", transport=transport, rate_limiter=limiter)


def _conn():
    conn = connect(":memory:")
    init_db(conn)
    return conn


def _stmt(disc, code, period, sales="1000"):
    return {
        "DiscNo": disc, "Code": code, "DiscDate": "2026-05-10",
        "CurPerType": period, "DocType": "FYFinancialStatements_Consolidated_JP",
        "Sales": sales, "OP": "200", "OdP": "180", "NP": "150",
        "CurFYEn": "2026-03-31",
    }


# --- by-code -----------------------------------------------------------------

def test_by_code_saves_statement_values():
    def handler(url, params):
        return {"data": [_stmt("1001", params["code"], "FY")]}

    conn = _conn()
    n = fetch_statements(_client(handler), conn, codes=["13010"])
    assert n == 1
    row = conn.execute(
        "SELECT code, net_sales, operating_profit, ordinary_profit, profit, "
        "type_of_current_period, fiscal_year_end FROM statements"
    ).fetchone()
    assert row["code"] == "13010"
    assert row["net_sales"] == 1000.0
    assert row["operating_profit"] == 200.0
    assert row["ordinary_profit"] == 180.0
    assert row["profit"] == 150.0
    assert row["type_of_current_period"] == "FY"
    assert row["fiscal_year_end"] == "2026-03-31"


def test_by_code_quarter_and_full_year_distinguished():
    def handler(url, params):
        return {"data": [_stmt("2001", params["code"], "1Q"), _stmt("2002", params["code"], "FY")]}

    conn = _conn()
    assert fetch_statements(_client(handler), conn, codes=["72030"]) == 2
    periods = {
        r["type_of_current_period"]
        for r in conn.execute("SELECT type_of_current_period FROM statements")
    }
    assert periods == {"1Q", "FY"}


# --- by-date -----------------------------------------------------------------

def test_by_date_uses_calendar_and_saves_all_disclosures():
    def handler(url, params):
        if url.endswith(config.CALENDAR_ENDPOINT):
            return {"data": [
                {"Date": "2026-05-08", "HolDiv": "1"},
                {"Date": "2026-05-09", "HolDiv": "0"},  # 土曜 → 除外
            ]}
        # その営業日の全開示(複数銘柄)。
        return {"data": [_stmt("d-" + params["date"] + "-a", "13010", "FY"),
                         _stmt("d-" + params["date"] + "-b", "72030", "1Q")]}

    conn = _conn()
    n = fetch_statements(_client(handler), conn, reference_date=REF)
    assert n == 2  # 営業日1日 × 2開示
    codes = {r["code"] for r in conn.execute("SELECT DISTINCT code FROM statements")}
    assert codes == {"13010", "72030"}


def test_by_date_explicit_dates():
    def handler(url, params):
        return {"data": [_stmt("x-" + params["date"], "13010", "FY")]}

    conn = _conn()
    n = fetch_statements(_client(handler), conn, dates=["2026-05-08", "2026-05-11"], reference_date=REF)
    assert n == 2


# --- 共通 --------------------------------------------------------------------

def test_rerun_is_idempotent():
    def handler(url, params):
        return {"data": [_stmt("3001", "13010", "FY")]}

    conn = _conn()
    fetch_statements(_client(handler), conn, codes=["13010"])
    fetch_statements(_client(handler), conn, codes=["13010"])
    assert conn.execute("SELECT COUNT(*) AS c FROM statements").fetchone()["c"] == 1


def test_empty_and_nonnumeric_values_become_none():
    def handler(url, params):
        return {"data": [_stmt("4001", "13010", "FY", sales="")]}

    conn = _conn()
    fetch_statements(_client(handler), conn, codes=["13010"])
    assert conn.execute("SELECT net_sales FROM statements").fetchone()["net_sales"] is None


def test_record_without_disclosure_number_skipped():
    def handler(url, params):
        return {"data": [{"Code": "13010", "Sales": "1"}]}

    conn = _conn()
    assert fetch_statements(_client(handler), conn, codes=["13010"]) == 0
