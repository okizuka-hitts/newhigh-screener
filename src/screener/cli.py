"""screener CLIエントリポイント。

各サブコマンドの実装はEPIC(JIRA NS)ごとに追加される。
"""

import argparse
import sys


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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 2

    print(f"screener {args.command}: 未実装です(対応するEPICで実装されます)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
