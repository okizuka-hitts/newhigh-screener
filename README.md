# newhigh-screener

国内株式の新高値(52週高値更新)銘柄をスクリーニングするPython CLIです。
データソースは J-Quants API(ライトプラン)のみを使用します。

> **注意**: 本ツールは投資助言を行うものではありません。出力は情報提供のみを目的とし、投資判断は自己責任で行ってください。J-Quants APIから取得した生データはリポジトリに含まれません(利用規約に基づき再配布しません)。

## セットアップ

```bash
pip install -e ".[dev]"
cp .env.example .env   # JQUANTS_API_KEY を設定
```

## 使い方

```bash
screener fetch [--verify]          # データ取得 / 完全性検査
screener detect --date 2026-07-10 [--csv out.csv]
screener tune --sector 3250 | --all
```

各コマンドの仕様・新高値の定義はEPIC実装時に本READMEへ追記されます。

## 開発

このリポジトリはEPIC駆動ループで開発されています(JIRA NSプロジェクトが単一の真実)。
開発プロセスは [CLAUDE.md](CLAUDE.md)、受け入れ検証の仕組みは [.claude/agents/verifier.md](.claude/agents/verifier.md) と [verification/](verification/) を参照してください。

コミット前チェックは [.claude/rules/code-style.md](.claude/rules/code-style.md) の品質ゲートを実行してください。

## ライセンス

MIT
