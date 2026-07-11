"""config モジュールのテスト(定数の存在・実効レート・環境変数の取得)。"""

from pathlib import Path

import pytest

from screener import config


def test_endpoints_and_base_url_present():
    assert config.JQUANTS_BASE_URL.startswith("https://")
    assert config.AUTH_REFRESH_ENDPOINT.startswith("/")
    assert config.LISTED_INFO_ENDPOINT.startswith("/")
    assert config.DAILY_QUOTES_ENDPOINT.startswith("/")
    assert config.STATEMENTS_ENDPOINT.startswith("/")


def test_rate_limit_constants_and_safety_factor():
    assert config.RATE_SAFETY_FACTOR == 0.5
    assert config.JQUANTS_RATE_LIMIT_PER_MIN > 0
    # 実効上限は上限×安全係数(50%)。
    assert config.effective_rate_limit_per_min() == pytest.approx(
        config.JQUANTS_RATE_LIMIT_PER_MIN * 0.5
    )
    # 実効上限は必ず生の上限より小さい(=絞り込みが効いている)。
    assert config.effective_rate_limit_per_min() < config.JQUANTS_RATE_LIMIT_PER_MIN


def test_fetch_window_constants():
    assert config.RECENT_MONTHS == 3
    assert config.LOOKBACK_WEEKS == 52


def test_get_api_key_from_env(monkeypatch):
    monkeypatch.setenv(config.API_KEY_ENV, "dummy-refresh-token")
    assert config.get_api_key() == "dummy-refresh-token"


def test_get_api_key_missing_raises_without_leaking(monkeypatch):
    monkeypatch.delenv(config.API_KEY_ENV, raising=False)
    with pytest.raises(RuntimeError) as excinfo:
        config.get_api_key()
    # メッセージに環境変数名は出てよいが、鍵の値そのものは扱っていない。
    assert config.API_KEY_ENV in str(excinfo.value)


def test_get_db_path_default(monkeypatch):
    monkeypatch.delenv(config.DB_PATH_ENV, raising=False)
    assert config.get_db_path() == config.DEFAULT_DB_PATH


def test_get_db_path_override(monkeypatch, tmp_path):
    target = tmp_path / "custom.db"
    monkeypatch.setenv(config.DB_PATH_ENV, str(target))
    assert config.get_db_path() == Path(str(target))
