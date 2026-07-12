"""config モジュールのテスト(定数の存在・実効レート・環境変数の取得)。V2準拠。"""

from pathlib import Path

import pytest

from screener import config


def test_endpoints_and_base_url_present():
    assert config.JQUANTS_BASE_URL.startswith("https://")
    assert config.JQUANTS_BASE_URL.endswith("/v2")
    assert config.LISTED_INFO_ENDPOINT == "/equities/master"
    assert config.DAILY_QUOTES_ENDPOINT == "/equities/bars/daily"
    assert config.STATEMENTS_ENDPOINT == "/fins/summary"
    assert config.CALENDAR_ENDPOINT == "/markets/calendar"
    assert config.RESPONSE_DATA_KEY == "data"


def test_v2_auth_is_static_api_key_header():
    # V2はX-API-KEYヘッダの静的キー。トークンリフレッシュ関連の定数は持たない。
    assert config.API_KEY_HEADER == "X-API-KEY"
    assert config.API_KEY_ENV == "JQUANTS_API_KEY"
    assert not hasattr(config, "AUTH_REFRESH_ENDPOINT")
    assert not hasattr(config, "ID_TOKEN_TTL_SECONDS")


def test_rate_limit_constants_and_safety_factor():
    assert config.RATE_SAFETY_FACTOR == 0.5
    # V2ライトプランの公表上限 = 60 req/min。
    assert config.JQUANTS_RATE_LIMIT_PER_MIN == 60
    # 実効上限は上限×安全係数(50%) = 30 req/min。
    assert config.effective_rate_limit_per_min() == pytest.approx(30.0)
    assert config.effective_rate_limit_per_min() < config.JQUANTS_RATE_LIMIT_PER_MIN


def test_fetch_window_constants():
    assert config.RECENT_MONTHS == 3
    assert config.LOOKBACK_WEEKS == 52


def test_get_api_key_from_env(monkeypatch):
    monkeypatch.setenv(config.API_KEY_ENV, "dummy-api-key")
    assert config.get_api_key() == "dummy-api-key"


def test_get_api_key_missing_raises_without_leaking(monkeypatch):
    monkeypatch.delenv(config.API_KEY_ENV, raising=False)
    with pytest.raises(RuntimeError) as excinfo:
        config.get_api_key()
    assert config.API_KEY_ENV in str(excinfo.value)


def test_get_db_path_default(monkeypatch):
    monkeypatch.delenv(config.DB_PATH_ENV, raising=False)
    assert config.get_db_path() == config.DEFAULT_DB_PATH


def test_get_db_path_override(monkeypatch, tmp_path):
    target = tmp_path / "custom.db"
    monkeypatch.setenv(config.DB_PATH_ENV, str(target))
    assert config.get_db_path() == Path(str(target))
