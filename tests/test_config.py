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


# --- .env 自動ロード(NS-13 回帰テスト) --------------------------------------


def test_load_env_file_reads_dotenv_without_export(monkeypatch, tmp_path):
    """バグ再現: `.env` を置くだけ(export なし)で get_api_key が解決すること。"""
    monkeypatch.delenv(config.API_KEY_ENV, raising=False)
    (tmp_path / ".env").write_text(f"{config.API_KEY_ENV}=from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    config.load_env_file()

    assert config.get_api_key() == "from-dotenv"


def test_load_env_file_does_not_override_existing_env(monkeypatch, tmp_path):
    """既存のプロセス環境変数が `.env` より優先される(CI/テストの上書きを壊さない)。"""
    monkeypatch.setenv(config.DB_PATH_ENV, "/from/process/env.db")
    (tmp_path / ".env").write_text(f"{config.DB_PATH_ENV}=/from/dotenv.db\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    config.load_env_file()

    assert config.get_db_path() == Path("/from/process/env.db")


def test_load_env_file_missing_is_noop(monkeypatch, tmp_path):
    """`.env` が無いディレクトリでは何もしない(例外を出さない)。"""
    monkeypatch.delenv(config.API_KEY_ENV, raising=False)
    monkeypatch.chdir(tmp_path)

    config.load_env_file()  # 例外を送出しない

    with pytest.raises(RuntimeError):
        config.get_api_key()


def test_load_env_file_ignores_comments_blanks_and_strips_quotes(monkeypatch, tmp_path):
    """コメント行・空行を無視し、`export ` 接頭辞と対の引用符を正しく処理する。"""
    monkeypatch.delenv(config.API_KEY_ENV, raising=False)
    (tmp_path / ".env").write_text(
        "# これはコメント\n\n" f'export {config.API_KEY_ENV}="quoted-value"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    config.load_env_file()

    assert config.get_api_key() == "quoted-value"


def test_main_invokes_load_env_file(monkeypatch):
    """CLIエントリポイント main() が `.env` ロードを1回だけ呼ぶことを確認する。"""
    from screener import cli

    calls = {"n": 0}
    monkeypatch.setattr(config, "load_env_file", lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))

    cli.main([])  # コマンド無し → return 2。load_env_file は呼ばれる

    assert calls["n"] == 1
