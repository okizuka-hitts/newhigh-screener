"""screener CLIエントリポイント。

各サブコマンドの実装はEPIC(JIRA NS)ごとに追加される。
"""

import argparse
import logging
import sys

from screener.api.client import JQuantsClient
from screener.db import connect, init_db
from screener.fetch import run_fetch, verify_data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="screener",
        description="国内株式の新高値(52週高値更新)銘柄スクリーニング",
    )
    subparsers = parser.add_subparsers(dest="command")

    fetch = subparsers.add_parser("fetch", help="データ取得 / 完全性検査")
    fetch.add_argument("--verify", action="store_true", help="完全性検査を実行する")

    detect = subparsers.add_parser("detect", help="新高値銘柄の検出")
    detect.add_argument("--date", required=True, help="対象日 (YYYY-MM-DD)")
    detect.add_argument("--csv", help="CSV出力先パス")

    tune = subparsers.add_parser("tune", help="業種別フィルタパラメータのバックテスト調整")
    tune_target = tune.add_mutually_exclusive_group(required=True)
    tune_target.add_argument("--sector", help="対象のSector33コード")
    tune_target.add_argument("--all", action="store_true", help="全33業種を対象にする")

    return parser


def _cmd_fetch(args: argparse.Namespace) -> int:
    """fetchサブコマンド。取得パイプライン実行 / `--verify` で完全性検査。"""
    conn = connect()
    init_db(conn)

    if args.verify:
        report = verify_data(conn)
        if report.informational:
            print(f"参考: {len(report.informational)}銘柄は対象期間に取引実績なし(欠損対象外)。")
        if report.complete:
            print("データは完全です。")
            return 0
        print("データ欠損を検出しました。fetch を実行してデータを補完してください:", file=sys.stderr)
        for issue in report.issues:
            print(f"  - {issue}", file=sys.stderr)
        return 1

    client = JQuantsClient()
    summary = run_fetch(client, conn)
    print(
        "fetch完了: "
        f"銘柄 {summary['listed_info']}件 / 日足 {summary['daily_quotes']}件 / "
        f"財務 {summary['statements']}件 / 分割補正 {summary['adjusted_codes']}銘柄"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 2

    if args.command == "fetch":
        try:
            return _cmd_fetch(args)
        except RuntimeError as exc:
            # 認証・API・設定エラー等は1行で理由を示して終了する(スタックトレースを見せない)。
            print(f"エラー: {exc}", file=sys.stderr)
            return 1

    print(f"screener {args.command}: 未実装です(対応するEPICで実装されます)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
