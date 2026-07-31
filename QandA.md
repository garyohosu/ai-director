# QandA.md

## Q001

- Status: ANSWERED
- Request-ID: REQ-20260731-SPEC
- From: AntiGravity
- To: HUMAN
- Severity: HIGH
- Blocking: NO
- Category: SPEC
- Question: mail および orchestrator のコピー元コミットID（リポジトリのコミットハッシュ）の確定方法について。
- Proposed-Answer: memo.mdの記述例（mail: f24af8b, orchestrator: 86c696a）を暫定値とし、人間がコピー元の正しいコミットを確定した段階で COMPONENTS.md を更新する。
- Evidence: memo.md L83-L96
- Answered-By: claude_designer
- Decision: mail: f24af8b、orchestrator: 86c696a。取込日: 2026-07-31。取込時のローカル変更: なし。
- Reason: コピー元リポジトリの確認により確定。
- Answered-At: 2026-07-31T03:04:17.000Z

## Q002

- Status: ANSWERED
- Request-ID: REQ-20260731-SPEC
- From: AntiGravity
- To: HUMAN
- Severity: MEDIUM
- Blocking: NO
- Category: DESIGN
- Question: director 自身の UID 管理について。既存 orchestrator では人間やシステム用の UID が予約・登録されていますが、director 用のデフォルト UID（例: UID000000 または DIRECTOR）の規約を固定すべきか？
- Proposed-Answer: config.json で director_uid として設定可能にしつつ、デフォルト値として UID000000 を推奨値とする。
- Evidence: director/SPEC.md 第4章
- Answered-By: human_controller
- Decision: UID000000などの予約UIDは採用しない。directorも通常のmailユーザーとして名前 directorで登録し、mail.register_user()が返したUIDを永続化して再利用する。mail.initialize()実行後は再登録する。UIDをPythonコードへ固定値として埋め込まない。
- Reason: mailパッケージの仕様に合わせた採番・保存方式とするため。
- Answered-At: 2026-07-31T03:04:17.000Z

## Q003

- Status: ANSWERED
- Request-ID: REQ-20260731-SPEC
- From: AntiGravity
- To: HUMAN
- Severity: MEDIUM
- Blocking: NO
- Category: ARCHITECTURE
- Question: orchestrator 経由で director が起動された際、director 内部から指揮AI（Codex CLI）を同期的に呼び出す処理のタイムアウト時間やAPIエラー時のリトライ方針。
- Proposed-Answer: director 内の CLI 呼出タイムアウトは orchestrator の cli_timeout_sec より短い値（例: 300秒）に設定し、指揮AI呼出失敗時は WAIT または HUMAN_REQUIRED へフォールバックする。
- Evidence: director/SPEC.md 第2章・第9章
- Answered-By: human_controller
- Decision: directorは指揮AIのCLIを直接起動しない。CLIタイムアウト・起動失敗・リトライ・RATE_LIMITED時の代替AI引継ぎは、すべてorchestratorの既存機能をそのまま利用する。directorが自ら管理するのは、判断依頼メール送信後の「判断待ち（DECISION_PENDING）」の期限のみとし、期限を超えて指揮AIからの判断結果メールが届かない場合はHUMAN_REQUIREDへ遷移する。
- Reason: orchestratorの既存機能と統合し、二重管理を防ぐため。
- Answered-At: 2026-07-31T03:04:17.000Z

## Q004

- Status: ANSWERED
- Request-ID: DIRECTOR-SPEC-001
- From: claude_designer
- To: HUMAN
- Severity: HIGH
- Blocking: NO
- Category: ARCHITECTURE
- Question: directorの応答義務（orchestratorのreply-check整合性）と終端通知の扱いについて。
- Proposed-Answer: ACKを受信したら完了とする。
- Evidence: director/SPEC.md 2章
- Answered-By: human_controller
- Decision: 承認する。ただしACK（ACK_RECEIVED）だけで作業完了とは判定しない。受信時に元のRequest-ID・Decision-IDを保持して送信元へACK_RECEIVEDを返信した上で、処理終了時に必ず終端通知（COMPLETED, FAILED, HUMAN_REQUIRED, REJECTED, CANCELLED）を送信する。送信元は終端通知を待って完了扱いとする。また、ファイル変更だけでメールを送らず終了した場合は成功とみなさず DELIVERY_FAILED とする。
- Reason: 人間からの指示に基づき、作業完了の判定基準と応答責任の範囲を二段階（ACK＋終端通知）で厳密化するため。
- Answered-At: 2026-07-31T12:28:55Z

## Q005

- Status: ANSWERED
- Request-ID: DIRECTOR-SPEC-001
- From: claude_designer
- To: HUMAN
- Severity: MEDIUM
- Blocking: NO
- Category: ARCHITECTURE
- Question: orchestratorのdirector対応（director用CLIアダプターおよび設定追加）の扱いについて。
- Proposed-Answer: 本改訂では手動起動とし、orchestrator拡張は別依頼とする。
- Evidence: orchestrator/README.md, director/SPEC.md 2章
- Answered-By: human_controller
- Decision: 承認する。orchestratorへdirector用CLIアダプターおよび設定を追加してよい。既存のmail/orchestrator公開仕様を壊さず、directorは可変UIDで通常登録する。起動・タイムアウト管理はorchestrator、判断・委任はdirectorが担う。
- Reason: 人間からの指示に基づき、 orchestratorでの統合運用を正式許可するため。
- Answered-At: 2026-07-31T12:28:55Z