"""SQLite接続ヘルパ。

行を辞書ライクに扱える `sqlite3.Row` を既定にし、外部キー制約を有効化する。
親ディレクトリが無ければ作成する(既定DBは `data/` 配下)。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from screener import config


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """DBへ接続する。

    Args:
        db_path: 接続先。省略時は `config.get_db_path()`。`":memory:"` も可。

    親ディレクトリを必要に応じて作成する。`sqlite3.Row` と外部キー制約を有効化する。
    """
    path = Path(db_path) if db_path is not None else config.get_db_path()
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
