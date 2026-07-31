# Director 要求仕様書 (SPEC.md)

## 1. 概要と目的

director は、複数のAIエージェント（Claude Code、Codex CLIなど）による自動開発ループを指揮・監督するシステムです。

既存の mail (エージェント間通信) および orchestrator (CLIプロセス運行管理) の上位に位置し、以下の役割を担います：
- 実装AIからの応答メール、成果物、QandA.md、Git差分、テスト結果を収集・分析する。
- 指揮AI（Codex CLI等）へ最小限かつ構造化されたコンテキストを渡し、次に行うべき判断を取得する。
- 判断結果に基づき、同じAIへ修正/続行指示を出すか、別AIへ作業を委任するか、完了/人間判断要求を行う。
- 人間が手動で指示と回答をコピーする現状(HITL)から、通常時は人間が監視し高リスク時のみ介入する(HOTL)運用へ移行する。

director自身は作業AI・指揮AIいずれのCLIプロセスも直接起動しない。すべてのAI起動はorchestratorへ委ね、directorとorchestrator・作業AI・指揮AIとの間の情報伝達はmail経由のメッセージ交換に統一する（2章）。

## 2. 責務の分離とシステム境界

| コンポーネント | 主要責務 | 境界・制約 |
|---|---|---|
| **mail** | AI間のメッセージ配送・未読確認・既読管理 | 本文の意味や進捗は判断しない。SQLite内部構造は非公開（公開API経由のみアクセス）。既読状態はdirectorの処理済み判定の正本ではない（5章）。 |
| **orchestrator** | 未読メール監視・CLIプロセスの起動・終了検出・二重起動防止・エラー通知・RATE_LIMITED時の代替AI引継ぎ | 成果物の仕様的正当性は判断しない。設定ファイルとmailのみを境界とし、Internalクラスの直接importは禁止。CLI起動・タイムアウト・リトライ・代替AI選択はorchestratorの責務であり、directorはこれらを重複実装しない。 |
| **director** | 開発作業の進捗判断・次指示の生成・Q&A管理・完了判定 | 作業AI・指揮AIいずれのCLIも自らのPythonコードから直接起動しない（subprocess.run等での一発実行を行わない）。すべてのAI起動はorchestratorを介する。director自身も、orchestratorから起動されるCLIプロセス（例: `python director.py --once`）になり得る。 |

### 指揮AIの起動境界（非同期メール方式）

directorは、作業AI・指揮AIのいずれについても、CLIプロセスを自身のPythonコードから直接起動しない。指揮AI（Codex CLI等）への判断依頼も、次の非同期メール方式に統一する。

```text
director
  → 指揮AIへ判断依頼メール送信（Decision-IDを発行し、OUTBOX_PENDING→SENTへ遷移。6章）
  → directorはDECISION_PENDINGを永続化して終了してよい
  → orchestratorが指揮AI宛ての未読メールを検知しCLIを起動
  → 指揮AIが構造化された判断結果（10章のJSON）をメールで返信
  → orchestratorがdirector宛ての未読メールを検知しdirector CLIを再起動
  → directorが受信したJSONをスキーマ検証し、妥当なら次の指示メールを送信
```

CLIの起動可否判定、タイムアウト、リトライ、RATE_LIMITED判定、代替AIへの引継ぎは、orchestrator/SPEC.mdで定義された既存機能をそのまま利用し、director側へ同じ機能を重複実装しない。directorが管理するのは「判断待ち（DECISION_PENDING）の期限」と、期限超過時のHUMAN_REQUIRED遷移だけである（7章）。

### 作業AIの起動関係

director が mail 宛に指示メールを送信すると、orchestrator が未読メールを検知して対象作業AIのCLIを起動・監視・完了通知する。この関係は変更しない。

### director自身の起動と応答義務（reply-check整合性）

orchestrator/SPEC.md 30章「返信メールの確認」は、CLIを起動したAIが元メールの送信者（または本文が明示する返信先UID）へ返信することを前提に、返信がなければ`NO_REPLY`をログ・通知する。directorは複数エージェント間の仲介・転送を行うため、起動契機となった元メールの送信者と、判断結果に基づく次の指示の送信先が一致しないことが構造的に生じる。

暫定方針（QandA.md Q004、人間の最終承認待ち）: directorは、起動契機となった元メールの送信者（本文に有効な返信先UIDの指定があればそのUID）へ、判断結果に基づく指示メールとは別に、短い受理通知（ACK。処理不要である旨を明記し、追加のAI起動を誘発しない文面とする）を必ず返信する。ACKの送信をもってorchestratorのreply-check（30章）を満たす。判断結果に基づく実際の指示メール・完了報告・HUMAN_REQUIRED要求は、Decision-ID単位で別途送信する（6章）。

## 3. 前提条件と実行環境

- **OS**: Windows 11
- **シェル**: PowerShell
- **言語・依存**: Python 3.10以上、Python標準ライブラリのみ使用（pip install や env は必須としない）。
- **配置**: director/ フォルダ単位で別プロジェクトにコピー可能。
- **パス解決**: カレントディレクトリに依存せず、Path(__file__) を基準に相対・絶対パスを解決する。
- **データ分離**: mail および orchestrator の SQLite DB 内部構造を直接参照せず、公開APIまたはドキュメントされたファイル境界のみを使用。

## 4. Directorの最小機能要件

1. **自身のMailユーザー登録**: director はUID000000などの予約UIDを使用せず、名前`director`で`mail.register_user()`に自己登録し、発行されたUIDを永続化（例: `director/runtime/self.json`）して再利用する。`mail.initialize()`が実行されデータが失われた場合は再登録する。UIDをPythonコードへ固定値として埋め込まない（QandA.md Q002）。
2. **宛てメールの受信・最新応答取得**: 自身宛ての未読メールを取得し、依頼ID (request_id) ごとに作業を追跡する。ただし、mailの既読状態はdirectorの処理済み判定の正本としては扱わない。処理済み判定はdirector専用の永続ジャーナル（5章）と`find_mails()`による照合で行う。
3. **情報収集**: QandA.md のOPEN項目、関連仕様 (memo.md 等)、Git差分 (git diff --stat, 変更ファイル一覧)、テスト結果、レビュー結果を収集。
4. **コンテキスト構築**: トークン消費を抑えた最小限のプロンプト・コンテキストを作成。
5. **指揮AIからの構造化判断取得**: 指揮AIへメールで判断を依頼し（2章）、JSON形式の検証可能な判断データをメール返信で取得する。directorが指揮AI CLIを直接起動することはない。
6. **指示メール送信**: 判断結果に基づき、対象AI宛てに次指示メールを mail.send_mail() で送信する。送信前にOUTBOX_PENDINGを永続化し、二重送信を防止する（6章）。
7. **ログとチェックポイント**: 全判断理由と根拠を JSONL (director/logs/director.jsonl) およびチェックポイント (director/runtime/checkpoint_{request_id}.json) に記録する。チェックポイントは5章の状態遷移と整合させる。
8. **一巡実行モード (--once)**: 8章で定義する「一巡処理」を1回実行して終了するモードをサポートする。
9. **安全停止と二重指示防止**: Request-IDとは別にDecision-IDを発行し、処理済みメールの再処理・二重送信を防止する（6章）。

## 5. Inbox/Outboxの状態遷移とクラッシュ復旧

mailの既読/未読フラグは「配送されたかどうか」を表すだけであり、directorが「その依頼をどこまで処理したか」を表す正本ではない。理由は次のとおりである。

- `receive_mail()`は取得と同時に既読化するため、既読化とdirector側の処理完了は別の事象である（既読化直後にdirectorがクラッシュした場合、処理は未完了のまま既読済みになる）。
- 既読状態を再送や再判定のトリガーに使えない（未読へ戻す操作を前提にしないため。mail/SPEC.md・mail/README.md参照）。

directorは、依頼ID(request_id)ごとに次の状態を持つ永続ジャーナルを`director/runtime/journal_{request_id}.json`に保存する。ジャーナルは既存の引継ぎ用チェックポイント(`director/runtime/checkpoint_{request_id}.json`、4章)と対になり、チェックポイントが「人間が読む要約」、ジャーナルが「機械が読む状態遷移ログ」を担う。

### 状態一覧

```text
DISCOVERED        … find_mails()/receive_mail()で対象メールを検出したが未着手
DECISION_PENDING  … 指揮AIへ判断依頼メールを送信済みで、返信待ち（2章・7章）
DECIDED           … 指揮AIからの判断メールを受信し、スキーマ検証に成功した
OUTBOX_PENDING    … 判断結果に基づく指示メールの送信を試みる直前に永続化する状態
SENT              … 送信を試みたメールが実際にmail DBへ記録されたことを確認した
COMPLETED         … この依頼IDに対する一連の処理が完了した
HUMAN_REQUIRED    … 自動処理を停止し、人間の判断を待っている

WAITING_FOR_DECISION … 作業AIがBlocking質問を送信し、質問結果JSONとcheckpointを報告して終了した状態。Job全体は非終端だが、そのCLI起動単位は正常終了とする。同一CLIプロセス内で回答を待たない。

作業AIの質問手順は「QandA.mdへOPEN質問を追加 → 質問メール送信 → `agent_reply.py wait --result-file`でWAITING_FOR_DECISION送信 → checkpoint保存確認 → 終了コード0」とする。directorは質問とWAITING通知を受信した後、`DECISION_PENDING`へ進む。

orchestratorのCLIタイムアウト通知は`status: TIMED_OUT`、`job_id`、`decision_id`、`agent_uid`、`exit_code`、`timeout_sec`、`stdout_log`、`stderr_log`、`occurred_at`を含む構造化フィールドを持つ。質問メールとタイムアウト通知が併存する場合、質問成功へ補正せず、当該CLI実行をタイムアウトとして記録し、directorは原則`HUMAN_REQUIRED`へ遷移する。
```

### 状態遷移

```text
DISCOVERED -> DECISION_PENDING -> DECIDED -> OUTBOX_PENDING -> SENT -> COMPLETED
DECISION_PENDING -> HUMAN_REQUIRED   （期限超過。7章）
DECIDED          -> HUMAN_REQUIRED   （JSONスキーマ不正、confidence不足など。10章）
OUTBOX_PENDING   -> HUMAN_REQUIRED   （Decision-IDの重複検出。6章）
HUMAN_REQUIRED   -> DECISION_PENDING または OUTBOX_PENDING （人間の承認後。9章）
```

各状態遷移は、遷移前にジャーナルへ書き込みを行ってから対応する操作（メール送信、指揮AIへの判断依頼など）を実行する。すなわち「状態を書いてから行動する」順序を守り、「行動してから状態を書く」順序にしない。

### クラッシュ復旧

directorは`--once`実行のたびに、対象の依頼IDについて次の順でジャーナルを確認してから通常の処理へ進む。

1. `OUTBOX_PENDING`のまま残っている依頼IDがあれば、6章の手順で送信済みかどうかを`find_mails()`で確認し、`SENT`または`HUMAN_REQUIRED`へ確定させる。
2. `DECISION_PENDING`のまま残っている依頼IDがあれば、7章の期限判定を行う。
3. 上記いずれにも該当しない依頼IDについて、通常どおり未読メール・QandA回答・チェックポイントを確認する。

「メール送信後、チェックポイント（ジャーナル）更新前にクラッシュした場合」は、上記1の手順そのものが復旧手順である。`find_mails()`で送信済みメールが実在すれば`SENT`とみなし、再送しない。実在しなければ未送信とみなし、同じDecision-IDで送信を再試行する。

## 6. 二重送信防止（Decision-ID）

### Decision-IDの発行

- Request-ID（orchestrator/SPEC.md 17章の`JOB-...`形式）とは別に、directorはメール1通の送信単位ごとに一意な`DEC-{UTC日時}-{依頼内連番2桁}-{ランダム4桁}`形式のDecision-IDを発行する。例: `DEC-20260731T120000Z-01-A1F9`。
- directorが送信するメールの件名には、既存のRequest-IDに加えてDecision-IDも角括弧で含める。

```text
[JOB-20260730T063015Z-A3F91C2D][DEC-20260731T120000Z-01-A1F9] REVISE指示: ...
```

  `mail.find_mails(request_id=...)`は件名の部分文字列一致（`%[<request_id>]%`）で判定するため（mail/agent_mail.py、mail/README.md）、Decision-IDも同じ角括弧表記であれば、Request-IDと混同せずそれぞれ独立に検索できる。件名の上限は512文字であり、改行・NUL文字を含められないため（mail/SPEC.md参照）、2つのIDを含めても制約内に収まるよう本文側の要約は簡潔にする。

### 送信前の永続化

1. 送信内容（宛先UID、件名、本文、Decision-ID）を確定した時点で、ジャーナルへ`OUTBOX_PENDING`として書き込む（5章）。
2. `mail.send_mail()`を呼び出す。
3. 送信が成功したら（例外が発生せず戻り値のmail_idを得たら）、ジャーナルを`SENT`へ更新し、返却されたmail_idを記録する。

### 再起動時の確認

directorの再起動直後、ジャーナルに`OUTBOX_PENDING`のまま残っているDecision-IDがあれば、次の条件で`mail.find_mails()`を呼び出す。

```python
find_mails(
    sender_uid=director_uid,
    recipient_uid=<ジャーナルに記録した宛先UID>,
    request_id=<Decision-ID>,
)
```

- 該当0件: 未送信と判断し、同じDecision-ID・同じ宛先・同じ本文で送信を再試行する（新しいDecision-IDを発行しない）。
- 該当1件: 既に送信済みと判断し、そのmail_idでジャーナルを`SENT`へ確定する。再送しない。
- 該当2件以上: 同一Decision-IDのメールが複数存在する異常状態のため、自動処理を継続せず`HUMAN_REQUIRED`へ遷移し、該当するDecision-IDとメールID一覧を人間へ通知する（9章）。

同一Decision-IDのメールを人為的に複数回送信すること（同じ内容の手動再送などを含む）を、director自身の処理としては行わない。

## 7. 判断待ち（DECISION_PENDING）の期限とHUMAN_REQUIRED遷移

directorは指揮AIのCLIを直接起動しないため（2章）、CLIタイムアウト・起動失敗・認証切れなどのエラー処理は行わない。これらはorchestratorの既存機能（cli_timeout_sec、max_retries、RATE_LIMITED時の代替AI引継ぎ）にすべて委ねる（QandA.md Q003）。

directorが自ら管理するのは、判断依頼メールを送信してから判断結果メールを受信するまでの「判断待ち期限」だけである。

- 判断依頼メール送信時に、ジャーナルへ`DECISION_PENDING`と期限（例: 送信時刻 + `decision_wait_timeout_sec`）を記録する。`decision_wait_timeout_sec`はdirector/config.jsonの設定項目とし、orchestratorの`cli_timeout_sec`や`reply_check_timeout_sec`より十分大きい値（両者の合計を上回る値）を既定値とする。これは、orchestrator側の起動・リトライ・reply-check・代替AI引継ぎが完了するまでの時間を、director側の期限が先に切ってしまわないようにするためである。
- directorが起動されるたびに、`DECISION_PENDING`のまま期限を超過している依頼IDがないか確認する。
- 期限を超過している場合は、指揮AIからの判断結果が得られなかったとみなし、`HUMAN_REQUIRED`へ遷移する。通知には、依頼ID・Decision-ID・判断依頼を送った指揮AIのUID・送信時刻・期限・（分かる範囲で）orchestrator側のログ・引継ぎ状況を含める。
- 期限内であれば、通常どおり未読メールの確認を継続し、判断結果メールの到着を待つ。

directorはCLIの終了コード・標準出力・RATE_LIMITED判定などをorchestratorから直接受け取らない。これらの情報が必要な場合は、orchestratorが送信するエラー通知メール（orchestrator/SPEC.md「CLI異常時の送信元通知」章）を、通常の受信メールと同様に確認する。

## 8. --onceモード（一巡処理）の定義

`--once`は「未読メール1件の処理」ではなく、次を決められた順序で処理する「一巡処理」として定義する。

```text
1. 復旧中のジャーナル確認（5章のクラッシュ復旧手順）
   1a. OUTBOX_PENDINGの確認・確定
   1b. DECISION_PENDINGの期限確認（7章）
2. 自身宛ての未読メールの確認（mail.check_mail() → mail.receive_mail()）
3. 未読メールがあれば、メールID昇順で1件ずつ処理する
4. QandA.mdのOPEN項目のうち、この起動で解決可能なものを処理する
5. 上記いずれの対象もなければNO_WORKとして正常終了する
```

- 1回の`--once`起動で処理する最大件数は、director/config.jsonの`max_mails_per_once`（既定値は要検討、当面5件を仮の既定値とする）で設定可能にする。上限に達した場合は、残りの対象を処理せず、処理済み・未処理の状況をチェックポイントへ記録して正常終了する（次回の`--once`起動が続きを処理する）。
- 初版では複数の依頼IDを並列に処理しない。2章・4章の処理はすべてメールID順の直列処理とする。同一起動内で複数の依頼IDにまたがる処理が必要な場合も、1件ずつ順番に完了させてから次の対象へ進む。
- 常時監視モード（引数なし起動）を初版の必須要件とするかどうかは、orchestratorが`director`をどのように起動するか（2章「director自身の起動と応答義務」およびQandA.md Q005）に依存するため、当面`--once`モードだけを正式な起動方法とする。

## 9. HUMAN_REQUIREDからの復帰操作

HUMAN_REQUIRED状態からの復帰は、あいまいな自然文だけで判定しない。人間からの操作は、内部mailパッケージを介した構造化メールとして受け付けることを正式ルートとする。Gmailなど外部メールで人間が指示した内容は、対象外とする（本節末尾「内部メールと外部メールの区別」を参照）。

### 操作の種類

```text
APPROVE  … 提示された対応案を承認し、そのまま実行を再開する
REJECT   … 提示された対応案を却下する（理由の記載を推奨する）
ANSWER   … QandA.mdのOPEN項目に対する回答（QandA.mdの更新をもって回答とする。11章）
RESUME   … WAITやタイムアウトなどで停止した処理を、状態を変えずに再開する
```

### 承認メールの形式

人間からの復帰操作メールは、宛先をdirectorのUIDとし、本文に次を機械可読な形式（`キー: 値`の行）で含める。承認対象のDecision-IDは必須とする。

```text
Decision-ID: DEC-20260731T120000Z-01-A1F9
Action: APPROVE
Reason: (任意)
```

- `Decision-ID`が本文に含まれない、または対応するジャーナルエントリが見つからない場合、directorはその操作を受け付けず、再度`HUMAN_REQUIRED`として差し戻す（不明なDecision-IDを推測で処理しない）。
- `Action`が上記4種以外、または欠落している場合も同様に差し戻す。

### 承認後の状態遷移

```text
HUMAN_REQUIRED --(APPROVE)--> 提示された対応案の種類に応じて DECISION_PENDING または OUTBOX_PENDING へ遷移し、5章の通常フローを再開する
HUMAN_REQUIRED --(REJECT)-->  当該依頼IDの自動処理を終了し、COMPLETED（不成立）として記録する。以降の対応は人間が新しい依頼として指示する
HUMAN_REQUIRED --(ANSWER)-->  QandA.mdの該当項目がANSWEREDへ更新されたことを次回起動時に検出し、DECISION_PENDING等の元の状態へ戻す
HUMAN_REQUIRED --(RESUME)-->  停止直前の状態（DECISION_PENDING等）へ復帰し、期限（7章）を再設定する
```

### 内部メールと外部メールの区別

directorが判断・復帰操作の入出力として扱うのは、`mail`パッケージ（SQLite）上のAIエージェント間メールだけである。Gmailなど外部のメールサービスは、directorの直接の入出力対象としない。人間がGmail等で受け取った通知に基づいて判断した内容は、人間が内部mailパッケージ経由でdirectorへ送信し直すことで初めてdirectorの処理対象になる。外部メールサービスとの連携（自動転送・自動要約送信など）が必要な場合は、別の依頼として要求仕様に追加し、本改訂の対象外とする。

## 10. 判断結果の分類と出力フォーマット

指揮AIからの返答は、以下の7種類の分類コードのいずれかを含まなければならない。

- CONTINUE: 同じ担当AIへ次の作業ステップを指示。
- REVISE: 成果物・コード・テスト・文書の修正指示。
- DELEGATE: 別のAI（例: Claude -> Codex）へ担当変更。
- ANSWER_QANDA: QandA.mdのOPEN質問に回答し、質問元へ回答を送信。
- WAIT: 外部条件（API制限回復、別作業完了）を待つ。
- HUMAN_REQUIRED: 自動判断せず、人間の判断を求める。送信先は、director/config.jsonで指定された人間ユーザー名（既定`human_controller`）を`mail.list_users()`または起動時の登録結果から解決したUIDとする。`HUMAN`のような固定文字列はmailのUID形式（`^UID[0-9]{6,}$`）を満たさないため、UIDとして直接使用しない。
- COMPLETE: 完了条件を満たしたため、依頼を終了する。

### 機械検証可能なJSONスキーマ案

以下は有効なJSONであり、`json.loads()`で検証できる。directorは指揮AIからの返信本文（またはその一部）をこのスキーマで検証し、検証に失敗したJSON（構文エラー、必須キー欠落、型不一致）をそのまま採用しない。検証に失敗した場合は5章の`DECIDED`へ進まず、`HUMAN_REQUIRED`へ遷移する。

```json
{
  "request_id": "JOB-20260730T063015Z-A3F91C2D",
  "decision_id": "DEC-20260731T120000Z-01-A1F9",
  "decision_type": "REVISE",
  "target_agent_uid": "UID000002",
  "reason": "単体テストで2件の例外エラーが発生しているため。",
  "evidence": ["orchestrator/tests/test_runtime.py:L45", "mail_id:102"],
  "next_instruction": "test_runtime.pyのImportErrorを解消し、再度テストを実行してください。",
  "referenced_files": ["orchestrator/tests/test_runtime.py"],
  "referenced_mail_ids": [102],
  "confidence": 0.95,
  "requires_human_approval": false
}
```

`decision_id`は6章のDecision-IDと同一の値を使用する（指揮AIが提案し、directorが採番規則を検証する、または指揮AIは`decision_type`と`reason`のみ返しdirectorが受信後にDecision-IDを採番する、のいずれかとする。採否は実装時に確定する）。

## 11. QandA.md の運用仕様

- **質問フォーマット**:
  ```markdown
  ## Q001
  - Status: OPEN
  - Request-ID: REQ-20260731-001
  - From: UID000002
  - To: director
  - Severity: MEDIUM
  - Blocking: YES
  - Category: SPEC
  - Question: DBパスのデフォルト値を相対パスにすべきか？
  - Proposed-Answer: Relative path from project root
  - Evidence: memo.md L60
  ```
- **回答時記録**:
  ```markdown
  - Status: ANSWERED
  - Answered-By: director
  - Decision: Relative path from project root
  - Reason: フォルダ可搬性を維持するため
  - Answered-At: 2026-07-31T11:40:00Z
  ```
- **HUMAN_REQUIRED（人間転送）条件**:
  - 要求仕様そのものの変更
  - 不可逆・高リスク操作（本番デプロイ、破壊的DB変更、GitHub push/merge）
  - 秘密情報・個人情報・課金に関わる判断
  - 指揮AIの信頼度低下 (confidence < 0.7) や複数AIの意見対立
- **ANSWER操作との関係**: 9章の`ANSWER`操作は、人間がQandA.mdの該当項目を`ANSWERED`へ更新することそのものを指す。directorは次回起動時にQandA.mdを再読込し、関連する依頼IDの状態を復帰させる。

## 12. コンテキスト圧縮とトークン節約

- 毎回リポジトリ全体のファイルを指揮AIに送ることを禁止。
- 送信情報の上限（既定 32 KiB / 8000 トークン程度）を設定。
- 優先順位: (1) 最新メール応答 > (2) OPEN Q&A > (3) テスト失敗ログ / Git diff stat > (4) 過去決定要約。
- コード差分は git diff --stat を優先し、巨大な差分は変更ファイルの重要箇所のみ抽出。

## 13. 自動判断(HOTL)と人間の関与(HITL)の境界

- **HOTL (通常自動運転)**: 低リスクな単体テスト修正、ドキュメント更新、軽微なバグ修正指示。
- **HITL (人間承認必須)**:
  - GitHubへの push / merge / PR確定
  - 外部（Gmail等）へのメール送信
  - 大量ファイル削除・破壊的操作
  - 本番環境変更・課金処理
  - director 自身の安全制約・上限設定の変更
  - director自身のコード・設定・判断ロジックの変更全般（16章に列挙する対象）

## 14. ループ防止と安全制約

- max_decisions_per_request: 1依頼あたり最大 15 回の判断まで。
- max_revisions_per_step: 同一工程での修正指示は最大 3 回まで。
- max_qanda_retries: 同一質問の再確認は 2 回まで。
- 超過時は自動的に HUMAN_REQUIRED メールを人間宛てに送信し安全停止。

## 15. セキュリティと堅牢性

- メール本文・成果物をプロンプトインジェクションとして無条件実行しない。
- プロジェクト外パス（パストラバーサル）へのアクセスを拒否。
- director自身はAI CLIを起動しないため、CLI起動に関するsubprocess呼出しの安全性（shell=True回避、引数リスト起動）はorchestrator側の責務とする（2章）。directorがファイル操作等でsubprocessを利用する場合も、同様にshell=Trueを避け引数リストで起動する。
- ログ出力における認証情報・秘密情報の自動マスキング。

## 16. Director自身の自己改善・開発管理と自己変更制限

- director 自身のコード変更は、実装AIとは別のAI（例: Claudeが実装、Codexがレビュー）に委任する。
- director 自身の安全制約、停止条件の変更は必ず HUMAN_REQUIRED とし、自動承認を禁止する。
- 実行中 director 自身のファイルを上書き変更することを禁止する。

### 自己変更として扱う対象

次の変更は、directorのコード変更と同等の高リスク操作として扱い、人間承認（HITL、13章）を必須とする。directorがこれらの変更を自動的に承認すること（自己承認）を禁止する。

```text
directorのPythonコード
config.json
システムプロンプト（指揮AI・作業AIへの指示テンプレートを含む）
コンテキスト生成と圧縮処理（12章）
JSON判断スキーマ（10章）
HITL／HOTL判定規則（13章）
ループ回数や制限値（14章のmax_decisions_per_request等、7章のdecision_wait_timeout_sec、8章のmax_mails_per_once等）
秘密情報マスキング規則（15章）
directorが参照する正式仕様（本SPEC.md、mail/SPEC.md、orchestrator/SPEC.md）
```

上記いずれについても、変更案の承認は9章の`APPROVE`操作（対象Decision-IDを明示した人間からの内部メール）によってのみ成立する。director自身の判断（指揮AIからの確信度が高い、複数AIが同意している等）だけで上記を変更してはならない。

## 17. 初期実証試験シナリオ

1. 人間が要求を memo.md に保存。
2. 初回のみ orchestrator / director を手動起動。
3. director が Claude Code へ SPEC.md 作成をメールで依頼。
4. Claude Code が SPEC.md を作成しメール返信。
5. director が Codex CLI へレビュー依頼をメール送信。
6. Codex CLI がレビュー結果を返信。
7. 指摘があれば director が Claude Code へ REVISE 指示。
8. APPROVED に達したら COMPLETE と判定して報告。
