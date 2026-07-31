# 引継ぎメモ（2026-07-31 REAL04終了時点）

## 現状

REAL04実AI完全ループ試験は **FAILED**。詳細は `director/records/REAL04_FAILURE.md` を参照。
コミット済み: `b031ba4`（ブランチ `agent/director-waiting-protocol`、push済み）。

director→Claude#1（ACK/委任/Q010/WAITING_FOR_DECISION）までは正常動作。しかしorchestratorの返信確認ロジックがこれを誤ってNO_REPLYと判定し、必須確認「directorに対するNO_REPLYが発生しない」に違反したため、その場で停止（stop.request）。Codexは一度も起動していない。Q010はOPENのまま。コード修正は一切行っていない。

## 次にやること

### 1. 人間へQ&Aとして諮るべき2つの根本原因（コード修正の前に判断が必要）

1. **Invocation-ID未伝達問題**
   `orchestrator/launcher.py` の `FIXED_INSTRUCTION_TEMPLATE` はエージェントへUIDは伝えるが、そのCLI起動自身のInvocation-IDを伝えない。一方 `orchestrator/mail_adapter.py` の `MailReplyQuery.find_terminal_reply`（`_invocation_matches`）は、終端返信メールの件名/本文に**そのエージェント自身の起動に割り当てられたInvocation-ID文字列がそのまま含まれること**を要求する。real AIエージェント（Claude/Codex）は自分のInvocation-IDを知らないため、直前に見た別のIDを転記するなどして一致せず、正しい返信が「返信なし」と誤判定される。
   - 論点: FIXED_INSTRUCTION_TEMPLATEにInvocation-IDを追加してAIに伝えるか、`find_terminal_reply`のInvocation-ID一致要件を緩和/廃止するか。

2. **directorの第三者転送と返信確認モデルの不一致**
   `resolve_reply_to_uid`は既定で「起点メールの送信者へ直接返信するはず」という前提だが、directorがBlocking質問をCodexへ転送する動作（`DECISION_REQUEST`送信）は仕様どおり第三者（Codex）宛てであり、起点送信者（Claude）への直接返信ではない。そのため返信確認が常に失敗する。
   - 論点: directorのようにQUESTION受信→別エージェントへ転送する起動パターンを、orchestratorの返信確認モデルにどう組み込むか（`resolve_reply_to_uid`の例外ルール追加か、director専用の終端判定基準を設けるか）。

3. **（副次的リスク、優先度は上記2つより低い）NO_REPLY通知メールの誤起動連鎖**
   NO_REPLY通知メール自体が対象エージェントの通常受信箱に届くため、orchestratorがこれを新規タスクとして誤って再起動してしまう（今回、意図しない3回目のClaude起動が発生しCLIタイムアウトで終わった）。通知メールを「処理対象タスクではない」と区別する仕組みが必要か検討する。

### 2. 修正の進め方（人間の指示済みルールを厳守）

- 上記1・2はコード修正が必要な事項。**Codexレビューでのレビュー結果と人間承認を得るまでpushしない**（これまでの運用ルールと同じ）。
- 修正は最小限にとどめ、mail/orchestrator/directorそれぞれの正式リポジトリ（`C:\PROJECT\aiagent-mail`, `C:\PROJECT\ai-orchestrator`）側で行い、検証後にai-directorへ再コピーする方針（memo.mdの運用方針を踏襲）。
- 修正後は新しいJob-ID・Decision-ID・Invocation-IDでREAL05を実施し、REAL01〜REAL04のruntime/メール/checkpoint/状態/成果物は再利用しない。

### 3. REAL05実施時の設定引き継ぎ事項

- `orchestrator/config.json`は現在REAL04の試験用値のまま（`cli_timeout_sec=600`, `max_run_duration_sec=2400`）。長時間自動運転用と検証試験用の設定は分ける方針だったので、REAL05も小規模検証なら概ねこのままでよいが、値は都度確認すること。
- `director/records/_pre_real04_archive_20260731T072022Z/`にREAL01〜03の退避データ（DB・状態・ログ）が残っている。コミットしない。今回REAL04用に退避した際と同じ要領で、REAL05前にも現行のDB・runtime・checkpoint・成果物を退避してから新規開始すること。

## 未コミットの既知の差分（本セッションと無関係、扱い注意）

- `memo.md`: 本セッション開始前から存在していた未コミットの変更（このセッションでは一切変更していない）。内容を確認のうえ、必要なら別途コミット判断すること。
- `_tmp_check_mail.py`, `_tmp_find_mails.py`, `_tmp_poll.py`: 過去セッションのスクラッチファイル。中身未確認。不要なら削除、必要なら`.gitignore`検討。
- `mail/`, `orchestrator/`: このリポジトリでは非追跡のまま（vendored snapshotとしてコピーする計画がmemo.mdにあるが未実施）。方針を続けるか改めて確認すること。
