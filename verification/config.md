# Verifier設定（Verifier所有・実装セッションから変更不可）

## Confluence

- 検証記録スペース: `N`（newhigh-screener / spaceId `12353538`）
- 記録ルートページ: `verifications`（pageId `12222467`）。検証関連ページはすべてこの配下に作る
- 索引ページ命名: `[EPICキー] 検証履歴`（verifications直下の子ページとして作成。全モードの検証記録を集約: 実行日・対象チケット・モード・判定・ページリンク）
- 記録ページ命名（1検証実行=1ページ。対応する索引ページの子として作成）:
  - EPIC受け入れ検証（完全モード）: `[EPICキー] 検証記録 YYYY-MM-DD #連番 (判定)`
  - ストーリー検証（軽量モード）: `[チケットキー] 検証記録 YYYY-MM-DD #連番 (判定)`

## JIRA

- プロジェクト: NS (newhigh-screener) / cloud: hittslabs.atlassian.net
- バグ起票時ラベル: `verifier`（テスト品質指摘は追加で `test-quality`）

## 検証パラメータ

- サンプリング突合の最小件数: 100
- サンプリングシード: Verifierが実行ごとに生成し、検証記録ページに記録する（実装側に事前開示しない）
- J-Quants APIレート上限の使用許容率: 50%（実装＋検証の合計で守る）

## このフォルダの所有権

`verification/` 配下はVerifier（および人間）所有。実装ループによる作成・変更・削除は禁止（CODEOWNERSで保護）。
- `verification/acceptance/` : EPICの受け入れ基準スナップショット（JIRAが正、更新は人間が行う）
- `verification/golden/` : 新高値検出の正解リスト（実装前に人間が確定）
- `verification/scripts/` : Verifierの独立検証スクリプト（Verifierが作成・管理）
