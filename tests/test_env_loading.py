"""NS-13 回帰テスト: .env の自動ロード(依存なし・既存env優先)。"""

import os

from screener import config


def _write_env(tmp_path, text):
    p = tmp_path / ".env"
    p.write_text(text, encoding="utf-8")
    return p


def test_regression_env_only_resolves_api_key(tmp_path, monkeypatch):
    # バグ再現: export せず .env だけ置いた状態。旧実装では get_api_key が未設定エラーだった。
    monkeypatch.delenv(config.API_KEY_ENV, raising=False)
    env = _write_env(tmp_path, "JQUANTS_API_KEY=key-from-dotenv\n")
    config.load_env_file(env)
    assert os.environ[config.API_KEY_ENV] == "key-from-dotenv"
    assert config.get_api_key() == "key-from-dotenv"


def test_cwd_default_path(tmp_path, monkeypatch):
    monkeypatch.delenv(config.API_KEY_ENV, raising=False)
    _write_env(tmp_path, "JQUANTS_API_KEY=cwd-key\n")
    monkeypatch.chdir(tmp_path)
    config.load_env_file()  # 既定でカレントの .env
    assert config.get_api_key() == "cwd-key"


def test_existing_env_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv(config.API_KEY_ENV, "from-process-env")
    env = _write_env(tmp_path, "JQUANTS_API_KEY=from-dotenv\n")
    config.load_env_file(env)
    # 既存の環境変数を上書きしない(CI・テストの SCREENER_DB_PATH 等を壊さない)。
    assert os.environ[config.API_KEY_ENV] == "from-process-env"


def test_missing_file_is_noop(tmp_path, monkeypatch):
    monkeypatch.delenv(config.API_KEY_ENV, raising=False)
    config.load_env_file(tmp_path / "nope.env")
    assert config.API_KEY_ENV not in os.environ


def test_parses_comments_blank_export_and_quotes(tmp_path, monkeypatch):
    monkeypatch.delenv("A", raising=False)
    monkeypatch.delenv("B", raising=False)
    monkeypatch.delenv("C", raising=False)
    monkeypatch.delenv("D", raising=False)
    env = _write_env(
        tmp_path,
        "# comment line\n"
        "\n"
        "A=plain\n"
        "export B=exported\n"
        'C="double quoted"\n'
        "D='single quoted'\n"
        "NOEQUALS\n",
    )
    config.load_env_file(env)
    assert os.environ["A"] == "plain"
    assert os.environ["B"] == "exported"
    assert os.environ["C"] == "double quoted"
    assert os.environ["D"] == "single quoted"
    assert "NOEQUALS" not in os.environ


def test_cli_main_loads_dotenv(tmp_path, monkeypatch, capsys):
    # cli.main() 冒頭で .env がロードされ、export なしでも fetch が認証情報を得られる経路。
    from screener import cli

    monkeypatch.delenv(config.API_KEY_ENV, raising=False)
    monkeypatch.setenv(config.DB_PATH_ENV, str(tmp_path / "s.db"))
    _write_env(tmp_path, "JQUANTS_API_KEY=cli-dotenv-key\n")
    monkeypatch.chdir(tmp_path)

    captured = {}

    def fake_client():
        captured["key"] = config.get_api_key()
        raise RuntimeError("stop-after-auth-resolved")

    monkeypatch.setattr(cli, "JQuantsClient", fake_client)
    rc = cli.main(["fetch"])
    # .env のキーが解決された(未設定エラーではなく、スタブ到達)。
    assert captured["key"] == "cli-dotenv-key"
    assert rc == 1  # スタブが RuntimeError → 1行エラーで終了
    assert "Traceback" not in capsys.readouterr().err
