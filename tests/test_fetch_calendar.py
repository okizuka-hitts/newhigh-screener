"""取引カレンダー(trading_days)のテスト(HolDiv 営業日フィルタ)。"""

from helpers import FakeClock

from screener import config
from screener.api.client import HttpResponse, JQuantsClient
from screener.api.rate_limiter import RateLimiter
from screener.fetch import trading_days


def _client(data):
    clock = FakeClock()
    limiter = RateLimiter(
        config.effective_rate_limit_per_min(), time_func=clock.time, sleep_func=clock.sleep
    )

    def transport(method, url, params=None, headers=None, body=None):
        assert url.endswith(config.CALENDAR_ENDPOINT)
        assert params["from"] and params["to"]
        return HttpResponse(200, {"data": data})

    return JQuantsClient(api_key="k", transport=transport, rate_limiter=limiter)


def test_returns_only_business_days_sorted():
    data = [
        {"Date": "2026-05-08", "HolDiv": "1"},  # 平日=営業日
        {"Date": "2026-05-02", "HolDiv": "0"},  # 土曜=非営業日
        {"Date": "2026-05-04", "HolDiv": "3"},  # 祝日(平日)=非営業日
        {"Date": "2026-05-01", "HolDiv": "1"},  # 営業日
        {"Date": "2026-05-07", "HolDiv": "2"},  # 半日立会=営業日
    ]
    days = trading_days(_client(data), "2026-05-01", "2026-05-08")
    assert days == ["2026-05-01", "2026-05-07", "2026-05-08"]


def test_excludes_weekends_and_holidays():
    data = [
        {"Date": "2026-05-02", "HolDiv": "0"},
        {"Date": "2026-05-03", "HolDiv": "0"},
        {"Date": "2026-05-04", "HolDiv": "3"},
    ]
    assert trading_days(_client(data), "2026-05-02", "2026-05-04") == []


def test_ignores_entries_without_date():
    data = [{"HolDiv": "1"}, {"Date": "2026-05-08", "HolDiv": "1"}]
    assert trading_days(_client(data), "2026-05-01", "2026-05-08") == ["2026-05-08"]
