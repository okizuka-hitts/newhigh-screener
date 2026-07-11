"""listed_info 取得・保存のテスト(注入フック経由・冪等・欠損許容)。"""

from helpers import FakeClock

from screener import config
from screener.api.client import HttpResponse, JQuantsClient
from screener.api.rate_limiter import RateLimiter
from screener.db import connect, init_db
from screener.fetch import fetch_listed_info

ID_TOKEN = "id-token"


def _client(pages):
    clock = FakeClock()
    limiter = RateLimiter(config.effective_rate_limit_per_min(), time_func=clock.time, sleep_func=clock.sleep)

    def handler(method, url, params=None, headers=None, body=None):
        if url.endswith(config.AUTH_REFRESH_ENDPOINT):
            return HttpResponse(200, {"idToken": ID_TOKEN})
        key = params.get("pagination_key") if params else None
        return HttpResponse(200, pages[key])

    return JQuantsClient(api_key="rt", transport=handler, rate_limiter=limiter, time_func=clock.time)


def _conn():
    conn = connect(":memory:")
    init_db(conn)
    return conn


SAMPLE = {
    None: {
        "info": [
            {
                "Date": "2026-07-10",
                "Code": "13010",
                "CompanyName": "極洋",
                "Sector33Code": "0050",
                "Sector33CodeName": "水産・農林業",
            },
            {
                "Date": "2026-07-10",
                "Code": "72030",
                "CompanyName": "トヨタ自動車",
                "Sector33Code": "3700",
                "Sector33CodeName": "輸送用機器",
            },
        ]
    }
}


def test_saves_code_name_and_sector33():
    conn = _conn()
    saved = fetch_listed_info(_client(SAMPLE), conn)
    assert saved == 2
    row = conn.execute(
        "SELECT company_name, sector33_code, sector33_name FROM listed_info WHERE code = ?",
        ("72030",),
    ).fetchone()
    assert row["company_name"] == "トヨタ自動車"
    assert row["sector33_code"] == "3700"
    assert row["sector33_name"] == "輸送用機器"


def test_rerun_is_idempotent():
    conn = _conn()
    fetch_listed_info(_client(SAMPLE), conn)
    fetch_listed_info(_client(SAMPLE), conn)
    count = conn.execute("SELECT COUNT(*) AS c FROM listed_info").fetchone()["c"]
    assert count == 2  # 重複行が生じない


def test_pagination_is_followed():
    pages = {
        None: {"info": [{"Code": "1", "CompanyName": "A"}], "pagination_key": "p1"},
        "p1": {"info": [{"Code": "2", "CompanyName": "B"}]},
    }
    conn = _conn()
    saved = fetch_listed_info(_client(pages), conn)
    assert saved == 2


def test_missing_sector33_is_tolerated():
    pages = {None: {"info": [{"Code": "9999", "CompanyName": "名無し"}]}}
    conn = _conn()
    saved = fetch_listed_info(_client(pages), conn)
    assert saved == 1
    row = conn.execute("SELECT sector33_code FROM listed_info WHERE code = ?", ("9999",)).fetchone()
    assert row["sector33_code"] is None


def test_item_without_code_is_skipped():
    pages = {None: {"info": [{"CompanyName": "コード無し"}, {"Code": "1", "CompanyName": "A"}]}}
    conn = _conn()
    saved = fetch_listed_info(_client(pages), conn)
    assert saved == 1
