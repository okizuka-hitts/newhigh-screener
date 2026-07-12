---
name: jira-ns
description: JIRA NSプロジェクト(newhigh-screener)のチケット操作手順書。NS-XXの参照・状態観察・ストーリー/タスク/バグの起票・ステータス遷移・コメント・Confluence検証記録を行うときは必ずこのスキルを使う。EPIC駆動ループの状態観察や「チケットを見て/作って/進めて」という指示、Verifierのバグ起票・検証記録もすべて対象。cloudIdやチケットタイプID・遷移IDはここに固定済みなので、APIで再発見しないこと。
---

# JIRA NS プロジェクト操作

NSプロジェクトのチケット操作は claude.ai 連携の Atlassian Rovo MCP ツール(`mcp__claude_ai_Atlassian_Rovo__*`)で行う。ツールは遅延ロードされているため、**最初に ToolSearch(`select:ツール名`)でスキーマを読み込んでから呼び出す**。

ポリシー(何を・いつやるか)は `.claude/rules/jira-workflow.md` と `.claude/rules/loop-operation.md` が正。このスキルはその実行手順(どのツールをどのIDで叩くか)だけを扱う。

## 固定値(再発見しない)

| 項目 | 値 |
|---|---|
| cloudId | `8f3edd0c-e639-4dee-8955-5caf0d1addf6`(= hittslabs.atlassian.net) |
| プロジェクト | キー `NS` / ID `10066` |
| Confluence 検証記録スペース | `NS`(詳細は `verification/config.md`) |

### チケットタイプID

| タイプ | ID | 用途 |
|---|---|---|
| エピック | `10076` | 人間のみ起票。エージェントは作らない |
| ストーリー | `10079` | 受け入れ基準から切り出す作業単位 |
| タスク | `10078` | 実装以外の作業(環境整備等) |
| バグ | `10081` | 発見した欠陥(Verifier起票は `verifier` ラベル) |

(`機能 10080`・`Subtask 10077` はこのプロジェクトでは使わない)

### ステータスと遷移ID

遷移はグローバル(どの状態からでも指定可能)。`transitionJiraIssue` には**遷移ID**を渡す。

| ステータス | ステータスID | 遷移ID |
|---|---|---|
| 未着手 | 10071 | `11` |
| 進行中 | 10072 | `21` |
| レビュー中 | 10073 | `31` |
| 完了 | 10074 | `41` |

### ラベル

`needs-human` / `verifier` / `test-quality`(意味は jira-workflow.md 参照)

## 操作レシピ

### 参照

```
getJiraIssue { cloudId, issueIdOrKey: "NS-1", responseContentFormat: "markdown" }
```

コメントも読むときは `fields: ["summary","description","status","comment"]` を付ける。

### 状態観察(ループの1周の起点)

```
searchJiraIssuesUsingJql { cloudId, jql: "parent = NS-1 AND statusCategory != Done ORDER BY status DESC, created ASC" }
```

- 優先順は 進行中 → 積み残しバグ → 新規ストーリー。バグだけ見るときは `AND issuetype = バグ` を足す
- EPIC一覧: `project = NS AND issuetype = エピック AND status = "進行中"`

### 起票(ストーリー/タスク/バグ)

```
createJiraIssue {
  cloudId,
  projectKey: "NS",
  issueTypeName: "ストーリー",   // または "タスク" / "バグ"
  parentIssueKey: "NS-1",        // 必須。親のないチケットを作らない
  summary: "簡潔な要約",
  description: <下のテンプレート>,
  labels: [...]                  // 該当時のみ
}
```

説明文テンプレート(jira-workflow.md の必須項目。省略しない):

```markdown
## 事象/目的
(なぜこのチケットが必要か)

## 作業内容(バグは再現手順)
(何をするか / どう再現するか)

## 受け入れ条件
- [ ] (このチケット固有の完了判定。機械確認可能な形で)

## 対応する親EPICの受け入れ基準
(親EPICのどの受け入れ基準に対応するか引用)
```

### ステータス遷移

```
transitionJiraIssue { cloudId, issueIdOrKey: "NS-5", transition: { id: "21" } }
```

遷移のタイミング(loop-operation.md / jira-workflow.md の規定):

- 着手時: `21`(進行中)+ 着手コメント
- PR作成+ストーリー検証(軽量モード)PASS後: `31`(レビュー中)+ PR URLをコメント(DEFERRED項目があれば明記)
- 人間がPRをマージしたのを確認後: `41`(完了)。**マージ前に完了にしない**(遷移は常に事実の後追い)
- EPIC: ループ正常終了時(全子チケットがレビュー中以上+完全検証PASS報告をコメント済み)にエージェントが `31`(レビュー中)へ遷移。**完了(`41`)への遷移は人間のみ**

### コメント

```
addCommentToJiraIssue { cloudId, issueIdOrKey: "NS-5", commentBody: "..." }
```

作業ログは「やったこと/わかったこと/次にやること」の3点で。中断時は必ず進捗コメントを残す。

### Confluence 検証記録(Verifier専用)

```
getConfluenceSpaces / createConfluencePage / updateConfluencePage
```

- スペース `NS` に `[EPICキー] 検証記録 YYYY-MM-DD #連番 (判定)` を作成し、`[EPICキー] 検証履歴` 索引ページに追記する
- J-Quantsの生データを本文・添付に含めない(集計結果のみ)

## トラブルシューティング

- **MCPツールが見つからない/認証エラー**: claude.ai 連携の対話認証が前提のため、Routine・ヘッドレス実行では使えないことがある。その場合は JIRA REST API を直接使う: `.env` に `JIRA_API_TOKEN`(+メールアドレス)を用意し、`https://hittslabs.atlassian.net/rest/api/3/...` へ Basic 認証で curl。エンドポイントとIDはこのスキルの固定値がそのまま使える。トークンが無ければ `needs-human` で停止する
- **遷移が失敗する**: `getTransitionsForJiraIssue` で現在利用可能な遷移を確認してから再試行(ワークフロー変更でIDが変わった場合は本スキルの表を人間の承認を得て更新)
- **タイプ名の指定に失敗する**: `issueTypeName` の代わりにタイプIDを使う
