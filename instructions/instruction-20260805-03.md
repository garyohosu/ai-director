# ai-director正式リポジトリへの複数AIフェイルオーバー制御反映

## 目的

`C:\PROJECT\csv`でライブ実証に成功した複数AIフェイルオーバーの状態管理と意思決定処理を、正式な`ai-director`リポジトリへ反映する。

このリポジトリの責務は、ジョブの状態遷移、委任、再開、Context Packet、意思決定、ジャーナル、完了判定、HUMAN_REQUIRED判定である。

## 対象リポジトリ

```text
C:\project\ai-director
```

参照元：

```text
C:\PROJECT\csv
```

## 前提

以下の順に正式リポジトリへの反映が完了していることを確認する。

1. `aiagent-mail`
2. `ai-orchestrator`
3. `ai-director`

先行リポジトリのコミットハッシュと依存関係を結果ファイルへ記載する。

## 作業開始前

```powershell
cd C:\project\ai-director
git status --short
git branch --show-current
git pull
```

未コミット変更を破棄しない。

`AGENTS.md`、README、Director状態遷移、ジャーナル、Context Packet、`agent_reply.py`、`DirectorState`、`InvocationResult`、委任・再開処理、HUMAN_REQUIRED条件を先に確認する。

## 背景

AI Directorは、Claude Code、Codex CLI、Antigravity、Grok CLIという複数AIを使い分け、一つのAIが利用量制限などで停止しても、別AIへ仕事を引き継いで作業を継続するためのオーケストレーターである。

4AI連携については、過去の「AI人狼」「神託会議」で複数AIを独立した判断主体として扱った実績がある。

今回のAI Directorは、その構成を開発作業へ発展させたものである。

統合環境では、実際のCodex利用量制限を起点として以下が成功した。

```text
codex_reviewer
→ RATE_LIMITED
→ grok_reviewer
→ 設計レビュー
→ 成果物更新
→ 完了返信
→ JOB-CSV-004 COMPLETED
```

## 設計原則

### 論理的役割とAIを分離する

通常の依頼は、特定AI名ではなく能力・役割として扱う。

例：

```text
reviewer
```

実際の候補：

- codex_reviewer
- grok_reviewer
- antigravity_reviewer
- claude_reviewer

特定AIによる検証が明示的に必要な場合だけ、AIを固定する。

### メールを指示の正本とする

ジョブ指示、Decision-ID、対象成果物、返信方法はAIメーリングシステムへ記録する。

directorは、fallback先へ元の作業を引き継ぐとき、CLIへ作業指示全文を直接渡さない。

新しい宛先へ委任メールを作成し、AIがそのメールを読んで処理する。

## 実装要件

### 1. 状態遷移

レート制限を、通常の失敗と区別する。

必要な状態またはイベントの例：

- RUNNING
- RATE_LIMITED
- HANDOFF_PENDING
- DELEGATED
- COMPLETED
- HUMAN_REQUIRED
- FAILED

Codexがレート制限になっただけで、元ジョブを即座に`FAILED`へしない。

期待する遷移：

```text
RUNNING
→ RATE_LIMITED
→ HANDOFF_PENDING
→ DELEGATED to fallback
→ RUNNING
→ COMPLETED
```

全fallback候補が失敗した場合のみ、`HUMAN_REQUIRED`または最終`FAILED`へ移行する。

### 2. InvocationResult

`InvocationResult`または同等構造へ、必要な情報を保持できるようにする。

例：

- status
- classification
- provider
- agent
- invocation_id
- attempt_id
- retry_at
- retry_after
- exit_code
- error summary
- fallback eligible
- handoff target

既存利用箇所との互換性を維持する。

### 3. DirectorState

以下を追跡できるようにする。

- 現在の担当エージェント
- 元の担当エージェント
- attempt count
- handoff count
- visited agents
- fallback candidates
- 最終結果
- root mail ID
- current mail ID
- Decision-ID
- Invocation-ID
- retry可能時刻

状態をジョブJSONやジャーナルへ保存し、再起動後も追跡できること。

### 4. fallback判定

以下のような代替可能エラーでは、fallback候補を評価する。

- rate limit
- session limit
- token limit
- CLI一時障害
- タイムアウト
- 認証の一時問題
- availability check失敗

ただし、エラー分類ごとにfallbackの可否を設定可能にする。

以下では即座に自動fallbackしない場合がある。

- セキュリティ違反
- 人間の承認が必要
- 破壊的操作の判断
- 要件の重大な曖昧さ
- 複数AIの結論が重大な点で対立
- reply protocolの重大な違反

### 5. 候補選択

役割に対応する候補一覧から、利用可能なAIを選択する。

候補順をコードへ固定しない。

設定例：

```text
reviewer:
  - codex_reviewer
  - grok_reviewer
  - antigravity_reviewer
  - claude_reviewer
```

Antigravityは旧Gemini CLI系統の後継として扱う。

現時点でAntigravityが未導入なら、利用不能候補としてスキップする。

旧Gemini CLIを現行候補へ戻さない。

### 6. 循環防止

以下を実装または確認する。

- visited agentsへ記録済みの候補を再選択しない
- 最大handoff回数を守る
- 同じattemptを二重作成しない
- 完了後のジョブを再委任しない
- 古い返信で新しい状態を上書きしない
- 同じ失敗イベントを二重処理しない
- director再起動後も履歴を維持する

### 7. ジャーナル

以下のイベントを記録する。

- initial delegation
- invocation started
- rate limit detected
- fallback decision
- handoff mail created
- fallback agent started
- fallback agent completed
- artifact updated
- original job completed
- HUMAN_REQUIRED decision

各イベントに可能な範囲で以下を含める。

- timestamp
- job ID
- Decision-ID
- mail ID
- invocation ID
- attempt ID
- source agent
- target agent
- classification
- handoff count
- visited agents

プロンプト全文や機密情報を不要に複製しない。

### 8. 完了判定

fallback先のAIが完了返信を返したら、その返信を元ジョブへ関連付ける。

以下を確認してから`COMPLETED`にする。

- JOB-IDが一致する
- Decision-IDまたは派生関係が正しい
- reply protocolを満たす
- 必須成果物が存在する
- 対象成果物の更新が確認できる
- 古いattemptからの返信ではない
- 終端通知が未送信である

最終通知はhuman_controllerへ一度だけ送る。

### 9. HUMAN_REQUIRED

以下の場合だけHUMAN_REQUIREDとする。

- 全fallback候補が利用不能
- 最大handoff回数へ到達
- 人間の承認が必要
- 重大な設計判断が必要
- セキュリティ上継続できない
- 複数AIの結果が自動統合できない
- 対応アダプターが存在しない

通知には以下を含める。

- 試行したAI
- 各AIの結果
- failure classification
- retry可能時刻
- 未試行候補と理由
- 人間に必要な操作

### 10. Context Packet

fallback先へ必要な作業情報を引き継ぐ。

ただし、前のAIによる未検証の結論を事実として扱わない。

fallback先には以下を渡す。

- 元の要求
- 対象ファイル
- 制約
- 完了条件
- reply protocol
- 前の試行が完了しなかった理由

以下は参考情報と明示する。

- 前AIの途中出力
- 前AIの推測
- 未確認の判断

## テスト要件

最低限、以下をテストする。

### 状態遷移

- RUNNINGからRATE_LIMITED
- RATE_LIMITEDからHANDOFF_PENDING
- fallback委任後のRUNNING
- fallback成功後のCOMPLETED
- 全候補失敗後のHUMAN_REQUIRED
- 途中のレート制限でFAILEDへ誤遷移しない

### 候補選択

- reviewer役から利用可能な候補を選ぶ
- Codex制限中にGrokを選ぶ
- Antigravity未導入をスキップする
- 旧Gemini CLIを選択しない
- visited agentsを再選択しない
- 候補順を設定から変更できる

### 循環と冪等性

- 無限handoffを防止する
- 最大handoff回数を守る
- 同一イベントの二重処理を防止する
- 同一完了通知を一度だけ送る
- 古いattemptの返信を無視する
- 再起動後に状態を復元する

### 完了判定

- Grokの完了返信を元のCodexジョブへ関連付ける
- 必須成果物を確認する
- reply protocol違反を検出する
- 成果物不足を完了扱いにしない
- JOB-ID不一致を拒否する

### 既存機能

- 委任
- 再開
- Context Packet
- agent_reply.py
- SYSTEM_ALERT制御
- task_eligible制御
- 既存テスト全件成功

## ドキュメント

以下を必要に応じて更新する。

- README
- 状態遷移図
- エラー分類
- fallback設計
- AIメーリングシステムとの責務分担
- Antigravityと旧Gemini CLIの扱い
- AI人狼・神託会議からAI Directorへ発展した背景

統合環境のライブ実証結果も記録する。

記録すべき事実：

- 実施日：2026-08-05
- 対象：JOB-CSV-004
- Codex：RATE_LIMITED
- fallback先：grok_reviewer
- handoff count：1
- Grok：COMPLETED
- 最終判定：重大・高優先度の問題なし
- 一時プロンプトファイル残留なし

正式リポジトリで再実証していない場合、統合環境での実績であることを明記する。

## Git管理対象

コミット対象：

- 状態遷移
- fallback判断
- DirectorState
- InvocationResult
- Context Packet
- ジャーナル
- 設定例
- テスト
- ドキュメント

コミットしないもの：

- runtimeジョブJSONの実データ
- メールDB
- ログ
- 一時ファイル
- PID
- キャッシュ
- 認証情報
- ローカル専用設定

## 完了条件

- レート制限時に元ジョブが即FAILEDにならない
- fallback候補を選択できる
- メールで引き継げる
- fallback完了を元ジョブへ反映できる
- 最終通知を一度だけ送る
- 循環と無限試行を防止できる
- 全候補失敗時だけHUMAN_REQUIREDになる
- 既存テストと新規テストが全件成功する
- 結果報告を作成した
- コミットした
- プッシュした

## 結果報告

リポジトリ直下へ以下を作成する。

```text
result-20260805-03.md
```

次を記載する。

1. 結論
2. 変更ファイル
3. 状態遷移の変更
4. fallback判断の変更
5. DirectorStateとInvocationResultの変更
6. Context Packetの変更
7. ジャーナルの変更
8. HUMAN_REQUIRED条件
9. 追加・更新したテスト
10. テストコマンド
11. テスト結果
12. 先行リポジトリのコミットハッシュ
13. 残る制約
14. コミットハッシュ
15. プッシュ先ブランチ

## コミットとプッシュ

推奨コミットメッセージ：

```text
feat: continue director jobs through AI fallback
```

今回の変更だけを選択してコミットする。

```powershell
git status --short
git diff --check
git add <今回の対象ファイル>
git commit -m "feat: continue director jobs through AI fallback"
git push
```

実行データやユーザーの未関連変更を含めないこと。

作業完了後に`git status --short`を再確認し、結果を`result-20260805-03.md`へ記録すること。