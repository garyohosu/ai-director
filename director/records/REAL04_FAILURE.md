# REAL04 実AI完全ループ試験 失敗記録

- 総合判定: **FAILED**（実AI初回往復までは成立、Codex起動前に人間指示によりその場で停止）
- Job-ID: `JOB-20260731T072152Z-REAL04`
- Decision-ID（初期）: `DEC-20260731T072152Z-01-9A21`
- 発生日: 2026-07-31

## 使用コンポーネント

- mail: `f24af8bf5ddc366f2c2b1be318c8ca97b30abc39`
- orchestrator: `d21218c13787e869254f42050ec256216a26e013`
- director: `1fa84bb518bd20d99c24ae3a328cce8cedea7e08`

## タイムアウト設定（今回のみ、設定ファイルのみ変更・コード変更なし）

`orchestrator/config.json`（gitでは非追跡のローカル設定ファイル）:

| 項目 | 変更前 | 今回使用 | 対応する運用ルール |
|---|---|---|---|
| `cli_timeout_sec` | 1800 | 600 | 各CLI内部タイムアウト |
| `max_run_duration_sec` | 14400 | 2400 | orchestrator全体監視期限 |
| `terminal_grace_sec` | 2 | 2（変更なし） | reply-check猶予 |
| `reply_check_timeout_sec` | 30 | 30（変更なし） | ※下記の理由により変更しなかった |

外側ハーネス期限は `timeout 2700 py -3 orchestrator/orchestrator.py`（bash `timeout` コマンド、常時監視モード）で実施。

`reply_check_timeout_sec`（返信メール確認の待機窓）は「reply-check猶予」ではなく、CLI正常終了後に返信メールを探す待機時間であり、これを2秒に短縮するとNO_REPLYを誘発するため変更しなかった。「reply-check猶予」に対応するのは`terminal_grace_sec`（既に2秒）と判断した。この対応付けが人間の意図と異なる場合は訂正を求める。

## 事前準備

- 新規試験用DB: 既存の `mail/data/agent_mail.db`（REAL01〜REAL03のメール含む）を `director/records/_pre_real04_archive_20260731T072022Z/` へ退避し、REAL04では空のDBから開始した（削除ではなく退避）。
- 同様に退避: `director/.state`, `director/runtime`, `director/logs/director.jsonl`, `director/tests/artifacts`, `orchestrator/runtime`, `orchestrator/checkpoints`, `orchestrator/logs`, `mail/logs`。
- ユーザー登録は退避前のDBと同じ順序で再実行し、既存config.json記載のUIDと一致させた: `claude_1`→UID000001, `codex_1`→UID000002, `codex_2`→UID000003, `human_controller`→UID000004, `claude_designer`→UID000005, `codex_reviewer`→UID000006, `orchestrator`→UID000007, `director`→UID000008（新規採番、config.jsonは`"uid": "AUTO"`のため一致確認は不要）。
- `orchestrator/runtime/stop.request` が残存していないことを確認済み。

## 試験経路（実際に発生した経路）

```
human_controller → director（正常終了）
  → Claude 1回目（Q010をOPENで作成、WAITING_FOR_DECISION送信、正常終了）
  → orchestrator が Claude 1回目を NO_REPLY と誤判定（誤判定、根拠は下記）
  → director 2回目（QUESTIONメールを処理し、Codexへ DECISION_REQUEST 送信、正常終了）
  → orchestrator が director 2回目も NO_REPLY と誤判定（誤判定、根拠は下記）
  → NO_REPLY通知メールが claude_designer の未読受信箱に着信
  → orchestrator が claude_designer を3回目起動（意図しない起動。通知メールを実行可能なタスクとして処理しようとした）
  → 3回目のClaudeはCLIタイムアウト（600秒）まで応答メールを送らず、HUMAN_REQUIREDへ分類
  → 【この時点でHuman（本試験の実施者）がNO_REPLY発生を検知し、仕様どおり停止】
  → orchestrator/runtime/stop.request を作成し、以後の新規起動を禁止
  → 実行中だったClaude 3回目の終了を待ってorchestratorが正常終了（強制終了なし）
```

Codex（codex_reviewer）は一度も起動されなかった（0回）。人間による停止のため、Codexへの `DECISION_REQUEST`（mail_id=8）は送信済みだが未起動・未応答のまま。

## 全メールIDと経路

| mail_id | From | To | Subject（要約） |
|---|---|---|---|
| 1 | human_controller | director | REAL04 real-AI full loop test request |
| 2 | director | human_controller | STATUS: ACK |
| 3 | director | claude_designer | DELEGATE（Invocation `INV-20260731T072527546Z-001-4646C590`） |
| 4 | director | human_controller | STATUS: WAITING_FOR_WORKER |
| 5 | claude_designer | director | QUESTION（Q010） |
| 6 | claude_designer | director | STATUS: WAIT（body: `"status": "WAITING_FOR_DECISION"`） |
| 7 | orchestrator | director | [NO_REPLY] claude_designerの処理に失敗（誤判定） |
| 8 | director | codex_reviewer | DECISION_REQUEST |
| 9 | orchestrator | claude_designer | [NO_REPLY] directorの処理に失敗（誤判定） |

mail_id=9 は claude_designer 宛ての未読メールとして扱われ、3回目のClaude起動（`INV-20260731T073209261Z-001-BB815CCE`）を誘発した。3回目の起動はどの宛先にも返信を送らず終了した（新規メールなし）。

## director/Claude/Codexの起動回数

- director: 2回（1回目 SUCCESS、2回目 NO_REPLY誤判定）
- claude_designer: 2回（1回目 NO_REPLY誤判定、2回目＝本来の「2回目」ではなく通知メール起因の意図しない3回目起動、HUMAN_REQUIREDでタイムアウト終了）
- codex_reviewer: 0回

## 全Invocation-ID

- director 1回目: `INV-20260731T072527546Z-001-4646C590`
- claude_designer 1回目: 実起動のInvocation-IDはorchestrator側runtime状態ファイルが上書きされ直接確認できなかったが、director宛のNO_REPLY通知（mail 7）の`invocation_id`欄で `INV-20260731T072532855Z-001-43A01739` と記録されている。
- director 2回目: `INV-20260731T073133905Z-001-A0B8370C`
- claude_designer 3回目（意図しない起動）: `INV-20260731T073209261Z-001-BB815CCE`

いずれも別Invocation-IDで起動しており、Invocation-IDの重複はない。

## 全状態遷移（director JobRecord）

DISCOVERED → ACK_SENT → DELEGATION_PENDING → WAITING_FOR_WORKER → WORKER_WAITING_QUESTION → WAITING_FOR_DECISION → DECISION_PENDING（停止時点の最終状態）

Job状態は `DECISION_PENDING` のまま。COMPLETEDには到達していない。

## Q010とCodexの回答

- Q010は作成された（Status: OPEN、Blocking: YES、Category: SPEC）。
- 質問文はユーザー指定の文言と一致: 「成果物の文字コードはUTF-8(BOMなし)とUTF-8(BOMあり)のどちらにしますか?」
- Proposed-Answer（Claudeの参考回答、Decisionではない）: 「UTF-8(BOMなし)を推奨する。」
- Codexは一度も起動されなかったため、正式なDecisionは存在しない。Q010は現在も**OPEN**のまま。
- Q006/Q007の限定的な再利用シグネチャ（`成果物`・`指定文字列`・`一行`・`末尾改行なし`の4語完全一致）とは一致せず、Q010は正しく新規のBlocking判断としてCodexへ回送された（この一点は設計どおり正常動作）。

## Context Packetのサイズとトークン数

- パス: `director/checkpoints/JOB-20260731T072152Z-REAL04/DEC-20260731T072152Z-01-9A21-context.md`
- バイト数: 6957 bytes（目標の8 KiB=8192 bytes以下を満たす）
- 推定トークン数: 1128
- 実トークン数: 取得不可（Codex CLIが起動されなかったため実測なし）

## 成果物

- 作成されていない（Claude 1回目の役割はQ010提示までであり、成果物作成は「回答受領後の2回目起動」で行う設計だったため、Codex未起動の本試験では到達しない）。
- `director/tests/artifacts/real04_result.txt` は存在しない。バイト列・SHA-256は該当なし。

## NO_REPLY、TIMEOUT、重複通知の有無

- **NO_REPLY: 発生した（2件、下記の根拠により誤判定と判断）**
  - mail_id=7: director宛て。origin_mail=mail_id=3（Claude 1回目のDELEGATE起点）。
  - mail_id=9: claude_designer宛て。origin_mail=mail_id=5（director 2回目のQUESTION処理起点）。
- **TIMEOUT: 発生した（1件）**
  - claude_designer 3回目起動（`INV-...BB815CCE`）が `cli_timeout_sec=600` 秒に到達し、強制終了。分類は`HUMAN_REQUIRED`（理由欄は「制限時間内に処理が終了しませんでした」）。通知メールは生成されなかった（`orchestrator.jsonl`上の`next_recipient`が`null`）。
- **重複通知: なし**（同一Invocationに対する二重のNO_REPLY/TIMEOUT通知は確認されていない）。

### NO_REPLY誤判定の根拠（コード調査、修正は行っていない）

1. **claude_designer 1回目（mail_id=7の原因）**: `orchestrator/mail_adapter.py` の `MailReplyQuery.find_terminal_reply` は、候補メールの `subject+body` に **launch時にorchestratorが割り当てたInvocation-ID文字列がそのまま含まれること**（`_invocation_matches`）を終端返信の必須条件とする。一方 `orchestrator/launcher.py` の `FIXED_INSTRUCTION_TEMPLATE` はエージェントへUIDは伝えるが、**そのInvocation-IDを一切伝えない**。Claudeは自身の返信件名にInvocation-IDタグを付けるため、直前に受信したDELEGATEメールの件名からタグ（`INV-20260731T072527546Z-001-4646C590`＝director 1回目自身のID）を転記したが、これはorchestratorがClaude自身の起動に割り当てたID（`INV-...43A01739`）とは異なる文字列だった。結果として`_invocation_matches`が常に不一致となり、`"status": "WAITING_FOR_DECISION"`を含む正しい返信（mail_id=6）が候補から除外され続けた。
2. **director 2回目（mail_id=9の原因）**: `resolve_reply_to_uid` は既定で「起点メールの送信者」を返信先とみなす（本件ではQUESTIONの送信者＝claude_designer）。しかしdirectorのBlocking質問処理は仕様どおりCodexへ`DECISION_REQUEST`を転送する設計であり、Claudeへ直接返信するものではない。そのため`find_terminal_reply`の`recipient_uid`フィルタ（claude_designerのUID）が、director実際の送信先（codex_reviewerのUID）と一致せず、常に「返信未検出」となった。

2つの原因は独立しており、(1)はInvocation-IDをAIへ伝達していない指示テンプレートの欠落、(2)はdirectorの第三者転送パターンとorchestratorの「起点送信者へ直接返信」モデルの不整合である。いずれもコード修正が必要な事項であり、本試験ではコード変更を行っていない。

### 誤判定の波及

NO_REPLY通知メール自体が対象エージェントの通常の未読受信箱に届くため、orchestratorはこれを新規タスクとして扱い、claude_designerを意図せず3回目起動した。3回目のセッションは実行可能なタスクを持たず、CLIタイムアウトまで応答せずに終了した。多段のNO_REPLY連鎖が起きる設計上のリスクが実証された（本件ではHUMAN_REQUIRED classificationで連鎖は1段で止まった）。

## 人間介入の有無

あり。上記の誤判定を検知した時点で、試験手順の「失敗時はその場で停止」に従い `orchestrator/runtime/stop.request` を作成して常時監視ループを停止させた。これは仕様が許可する「停止」操作であり、AIへの代行応答・プロンプト変更・コード修正・再試行は一切行っていない。停止後、実行中だった3回目のClaude起動は強制終了せず、CLIタイムアウトによる自然終了を待ってからorchestratorプロセス自体が正常終了した（`オーケストレーターを終了します。`のログを確認済み、`timeout`によるSIGTERMではなくorchestrator自身のwatch loop終了）。

## 必須確認チェックリストの結果

| 確認項目 | 結果 |
|---|---|
| directorがACKを送る | ○ |
| directorが委任メールIDを保存する | ○（mail_id=3, JobRecord.delegate_mail_id=3） |
| directorがWAITING_FOR_WORKERを送って正常終了する | ○ |
| directorに対するNO_REPLYが発生しない | **×**（mail_id=7、director宛て） |
| Claude 1回目が別Invocation-IDで起動する | ○ |
| ClaudeがACK、Q010、WAITING_FOR_DECISIONを送る | 部分的○（ACKに相当する専用メールはFIXED_INSTRUCTION_TEMPLATEに規定がなく送信されていないが、Q010作成とWAITING_FOR_DECISION相当の通知(body JSON)は送信済み） |
| WAITING検知後、ClaudeをTIMEOUT扱いしない | 文言上は○（TIMEOUTではなくNO_REPLYに分類）だが、意図（WAITINGを正常成功として扱う）は**×** |
| directorがContext Packetを生成する | ○（6957 bytes, 推定1128トークン） |
| Codexが1回だけ起動する | **未達（0回）** |
| CodexがACKと判断結果を送る | 未達 |
| Q010がANSWEREDになる | **×（OPENのまま）** |
| Claude 2回目が新しいInvocation-IDで起動する | 意図した「回答後の2回目」は未達。意図しない3回目起動のみ発生 |
| Claudeが成果物を作成する | 未達 |
| 内容、末尾改行、文字コード、SHA-256が一致する | 未達（成果物なし） |
| ClaudeがCOMPLETEDを送る | 未達 |
| directorがhuman_controllerへCOMPLETEDを送る | 未達 |
| Job状態がCOMPLETEDになる | **×（DECISION_PENDING）** |
| 人間の途中介入がない | 途中で仕様どおりの停止操作のみ実施（AI代行・プロンプト変更・コード修正なし） |

## 未解決事項

- `_invocation_matches`が要求するInvocation-ID一致を、real AIエージェント（Claude/Codex）がどう満たすべきかが未定義。`FIXED_INSTRUCTION_TEMPLATE`へInvocation-IDを追加するか、`find_terminal_reply`のInvocation-ID要件を緩和するかは人間の判断が必要。
- directorがCodexへ処理を転送する場合の「返信確認」モデル（`resolve_reply_to_uid`の既定値）をどう扱うべきかが未定義。DECISION_REQUEST送信をdirector自身の起動に対する正しい終端応答として認識させる仕組みが必要。
- NO_REPLY通知メールが対象エージェントの通常受信箱に届き、新規タスクとして誤起動される経路について、通知メールを「タスクではない」と区別する設計が必要。
- 上記はすべてコード修正が必要な事項であり、本試験では一切変更していない。Codexレビューと人間承認を経ない限りコード変更・再試験は行わない。

## Git状態

- ブランチ: `agent/director-waiting-protocol`
- コミット対象: `QandA.md`（Q010追加）、`director/knowledge/*.md`（自動再生成された参照メタデータ）、本ファイル（`director/records/REAL04_FAILURE.md`）
- コミット対象外: `mail/`, `orchestrator/`（gitで非追跡）、`memo.md`（本試験と無関係な既存の未コミット変更が試験開始前から存在。本試験では変更していない）、`dream.md`（グローバル規約によりコミット対象外）、生成DB・ログ・runtime・checkpoint・Context Packet実体・成果物・archiveディレクトリ・`__pycache__`
- 退避データ: `director/records/_pre_real04_archive_20260731T072022Z/`（REAL01〜REAL03のDB・状態・ログ。コミットしない）
