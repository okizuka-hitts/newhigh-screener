# CLAUDE.md

## プロジェクト概要

国内株式の新高値(52週高値更新)銘柄をスクリーニングするPython CLI。
要件・制約・用語定義は `spec/overview.md` が正。

## 開発プロセス(EPIC駆動ループ)

- JIRA NSプロジェクト(hittslabs.atlassian.net)が単一の真実。EPIC単位で実装を進める
- 各EPICの受け入れ基準が唯一の完了定義。スナップショットは `verification/acceptance/`(更新は人間のみ)
- 受け入れ検証は独立したVerifier(`.claude/agents/verifier.md`)が行い、記録はConfluence(スペース: NS)に蓄積する
- 仕様変更は人間の承認を経て `spec/` を更新してからコミットする
- 開発プロセスの詳細(ループ運用・JIRAワークフロー・テスト・コーディング・セキュリティ)は `.claude/rules/` を参照
- JIRA/Confluenceの操作手順(cloudId・チケットタイプID・遷移ID・起票テンプレート)は `jira-ns` スキルを使う

## 制約

- **J-Quants API を実装・テスト・修正する前に、必ず最新の公式仕様書(<https://jpx-jquants.com/ja/spec>)を確認する。** APIはバージョン移行等で変わり得るため、特定バージョンを前提にせず、ベースURL・エンドポイントのパス・認証方式・レートリミット・レスポンス形式を最新仕様と照合してから着手する(定数は `spec/overview.md` の版非依存方針に従いコードで一元管理)
- J-Quants APIへのアクセスはライトプラン上限の50%以内(実装+検証の合計で守る)
- 株式分割を検知した場合(AdjustmentFactor ≠ 1)、データを再取得し分割補正を行う
- 取得データ・APIキーはコミットしない(`.env` と `data/` は gitignore 済み)
- `verification/` 配下はVerifier(および人間)所有。実装ループによる作成・変更・削除は禁止

## コマンド

```bash
pip install -e ".[dev]"   # セットアップ
```

コミット前チェックは `.claude/rules/code-style.md` の**品質ゲート**を実行する(定義はそちらのみ)。
