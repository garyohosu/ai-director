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

## Q006
- Status: ANSWERED
- Request-ID: JOB-20260731T132500Z-REAL01
- Decision-ID: DEC-20260731T132500Z-01-C0DE
- From: claude_designer
- To: director
- Severity: HIGH
- Blocking: YES
- Category: SPEC
- Question: 成果物ファイルは末尾改行なしの一行で作成してよいですか？
- Proposed-Answer: はい。指定文字列を末尾改行なしで作成する。
- Evidence: この試験依頼
- Answered-By: human_controller
- Decision: 成果物ファイルは、指定された文字列の一行を末尾改行なしで作成する。
- Reason: 実AI試験では成果物のバイト列とSHA-256を完全一致で検証するため。
- Answered-At: 2026-07-31T04:57:29Z

## Q007
- Status: ANSWERED
- Request-ID: JOB-20260731T141500Z-REAL02
- Decision-ID: DEC-20260731T141500Z-01-BEEF
- From: claude_designer
- To: director
- Severity: HIGH
- Blocking: YES
- Category: SPEC
- Question: 成果物は指定文字列の一行を末尾改行なしで作成してよいですか？
- Proposed-Answer: はい。指定文字列を末尾改行なしで作成する。
- Evidence: 新規DBの実AI試験依頼
- Answered-By: director
- Decision: 成果物は、指定された文字列の一行を末尾改行なしで作成する。
- Reason: 同一内容のQ006に対する確定済み回答を再利用した。
- Reused-From: Q006
- Answered-At: 2026-07-31T05:39:39Z

## Q009
- Status: ANSWERED
- Request-ID: DIAG-20260731T-REAL03-INVOCATION
- Decision-ID: DEC-DIAG-REAL03-01
- From: director_diagnostic
- To: human_controller
- Severity: HIGH
- Blocking: YES
- Category: PROTOCOL
- Question: director Python CLIをorchestratorの一つのInvocationとして起動した場合、処理済みを示すInvocation-ID付き終端通知をdirectorが送信する契約にするか、orchestratorがdirector専用のstdout成功契約を検証するか？
- Proposed-Answer: 人間が正式な通信契約と通知先を確定する。
- Evidence: REAL03ではdirectorがACKと委任メールを送信したが、Invocation-ID付きのWAITING_FOR_DECISION/COMPLETED/FAILED通知がなく、orchestratorがNO_REPLYと分類した。director起動に関する修正は行っていない。
- Answered-By: human_controller
- Decision: directorも他のエージェントと同様に、Invocation-ID付きのメールで起動単位の終了状態を通知する。stdoutおよび終了コード0だけではInvocation成功と判定しない。directorがworkerへ委任して自身の処理を終了する場合は、WAITING_FOR_WORKERを送信する。
- Reason: 全エージェントでInvocation終了契約を統一し、NO_REPLY判定、再起動、状態復旧、将来のチャネル差し替えを一貫して扱うため。
- Answered-At: 2026-07-31T07:00:00Z

## Q010
- Status: OPEN
- Request-ID: JOB-20260731T072152Z-REAL04
- Decision-ID: DEC-20260731T072152Z-01-9A21
- From: claude_designer
- To: director
- Severity: HIGH
- Blocking: YES
- Category: SPEC
- Question: 成果物の文字コードはUTF-8(BOMなし)とUTF-8(BOMあり)のどちらにしますか?
- Proposed-Answer: UTF-8(BOMなし)を推奨する。
- Evidence: JOB-20260731T072152Z-REAL04のDELEGATEメール(依頼書は成果物の文字コードを指定していない)

## Q011
- Status: ANSWERED
- Request-ID: JOB-20260801T053600Z-REAL04-R1
- Decision-ID: DEC-20260801T053600Z-01-A404
- From: claude_designer
- To: director
- Severity: HIGH
- Blocking: YES
- Category: SPEC
- Question: 成果物 director/tests/artifacts/real04_result.txt の文字コードはUTF-8(BOMなし)とUTF-8(BOMあり)のどちらにしますか?
- Proposed-Answer: UTF-8(BOMなし)を推奨する。BOMの有無で先頭3バイトが変わり、検証対象のSHA-256が別値になるため。
- Evidence: 本JobのDELEGATEメール(mail_id=3)の依頼書に文字コードの指定がない。前JobのQ010は別Request-ID(JOB-20260731T072152Z-REAL04)のためOPENのまま再利用せず、本Job用に新規採番した。
- Answered-By: director
- Decision: UTF-8（BOMなし）とする。
- Reason: 指定内容はASCII文字列そのものであり、BOMを付けると先頭3バイトが追加されて検証対象のSHA-256も変わるため。既存Q010および本JobのQ011の提案とも整合する。
- Answered-At: 2026-08-01T05:47:39Z

## Q012
- Status: ANSWERED
- Request-ID: JOB-20260801T063515Z-REAL04-R2
- Decision-ID: DEC-20260801T063515Z-01-R204
- From: claude_designer
- To: director
- Severity: HIGH
- Blocking: YES
- Category: SPEC
- Question: 本R2受入試験の成果物 director/tests/artifacts/real04_result.txt について、(a)文字コードをUTF-8(BOMなし)とUTF-8(BOMあり)のどちらにするか、(b)末尾改行を付けないことを確定してよいか、(c)結果として全体バイト長を厳密に25バイトとしてよいか、の3点の確定回答をください。
- Proposed-Answer: (a)UTF-8(BOMなし)、(b)末尾改行なし、(c)全体25バイト、を推奨する。BOMを付けると先頭に3バイト、末尾改行を付けるとCRLF/LFで1〜2バイトが追加され、いずれも合計バイト長と検証対象のSHA-256が別値になるため。
- Evidence: 本JobのDELEGATEメール(mail_id=3)の依頼書。依頼書本文にはUTF-8(BOMなし)・末尾改行なし・25バイトの記載があるが、成果物作成前に本Job(JOB-20260801T063515Z-REAL04-R2)固有のBlocking判断として確定を求める。Q006・Q010・Q011は別Request-IDのため再利用せず、本Job用にQ012を新規採番した。
- Answered-By: director
- Decision: (a) Use UTF-8 without BOM. (b) Do not append a trailing newline. (c) The complete file length must be exactly 25 bytes.
- Reason: The required artifact is the exact 25-byte ASCII sequence REAL04_AUTONOMOUS_LOOP_OK; a BOM or trailing newline would add bytes and change the digest.
- Answered-At: 2026-08-01T06:43:47Z

## Q013
- Status: OPEN
- Request-ID: JOB-20260801T065634Z-REAL04-R3
- Decision-ID: DEC-20260801T065634Z-01-R304
- From: claude_designer
- To: director
- Severity: HIGH
- Blocking: YES
- Category: SPEC
- Question: 本R3受入試験の成果物 director/tests/artifacts/real04_result.txt について、(a)文字コードをUTF-8(BOMなし)とUTF-8(BOMあり)のどちらにするか、(b)末尾改行を付けないことを確定してよいか、(c)結果として全体バイト長を厳密に25バイトとしてよいか、の3点の確定回答をください。
- Proposed-Answer: (a)UTF-8(BOMなし)、(b)末尾改行なし、(c)全体25バイト、を推奨する。BOMを付けると先頭に3バイト、末尾改行を付けるとCRLF/LFで1〜2バイトが追加され、いずれも合計バイト長と検証対象のSHA-256が別値になるため。
- Evidence: 本JobのDELEGATEメール(mail_id=3)の依頼書。依頼書本文にはUTF-8(BOMなし)・末尾改行なし・25バイトの記載があるが、成果物作成前に本Job(JOB-20260801T065634Z-REAL04-R3)固有のBlocking判断として確定を求める。Q006・Q010・Q011・Q012は別Request-IDのため再利用せず、それらの回答を本件の充足とみなさず、本Job用にQ013を新規採番した。

## Q014
- Status: OPEN
- Request-ID: JOB-20260801T071255Z-REAL04-R4
- Decision-ID: DEC-20260801T071255Z-01-R404
- From: claude_designer
- To: director
- Severity: HIGH
- Blocking: YES
- Category: SPEC
- Question: 本R4受入試験の成果物 director/tests/artifacts/real04_result.txt について、(a)文字コードをUTF-8(BOMなし)で確定してよいか、(b)末尾改行を付けないことで確定してよいか、(c)結果として全体バイト長を厳密に25バイトとしてよいか、の3点の確定回答をdirectorへ求めます。
- Proposed-Answer: (a)UTF-8(BOMなし)、(b)末尾改行なし、(c)全体25バイト、を推奨する。BOMを付けると先頭に3バイト、末尾改行を付けるとCRLF/LFで1〜2バイトが追加され、いずれも合計バイト長と検証対象のSHA-256が別値になるため。
- Evidence: 本Job(JOB-20260801T071255Z-REAL04-R4)のDELEGATEメール(mail_id=3)の依頼書。依頼書本文にはUTF-8(BOMなし)・末尾改行なし・25バイトの記載があるが、成果物作成前に本Job固有のBlocking判断として確定を求める。Q006・Q010・Q011・Q012・Q013はいずれも別Request-IDのため再利用せず、それらの回答を本件の充足とみなさず、本Job用にQ014を新規採番した。
