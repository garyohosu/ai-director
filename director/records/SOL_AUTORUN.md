# REAL04 自律修復・再試験記録

- 開始日時: 2026-08-01 (Asia/Tokyo)
- 対象: `ai-director`, `ai-orchestrator`, `aiagent-mail`
- 方針: 既存の未コミット変更を保持してレビューし、各フェーズを実装・全テスト・独立レビュー後にローカルコミットする。pushは行わない。

## 初期調査

- `ai-director`: `agent/director-waiting-protocol`。Invocation-ID対策の未コミット変更と新規設計書・補助モジュールが存在。
- `ai-orchestrator`: `agent/spec-proofread-qanda`。Invocation-ID環境変数・指示テンプレート・本文照合の未コミット変更が存在。
- `aiagent-mail`: `main`。作業ツリーはclean。
- 3リポジトリとも開始時の直近履歴・status・staged/unstaged diffを確認した。既存変更は破棄していない。
- 初期 `git diff --check`: directorのテストに21件、orchestratorの実装・テストに6件の末尾空白があり失敗。
- 初期全テスト: director 32件、orchestrator 137件、mail 56件が成功。ただしdirectorの結果ファイル事前読込に `except Exception: pass` があり、壊れた入力を重複成功として扱える欠陥を確認。

## フェーズ1: Invocation-ID実装の完成

### 調査結果・判断理由

- 本文JSONの `invocation_id` を正本とし、Subjectは補助表示に限定する既存設計を採用する。
- `AI_INVOCATION_ID` と旧 `INVOCATION_ID` の厳格な解決、および互換モード用MANUAL ID生成は再利用可能な製品関数へ集約する。
- 結果ファイルとInvocation状態ファイルの読込失敗は握り潰さず、送信前に明示的な非0終了とする。

### 変更ファイル

- director: `.gitignore`, `real04_mitigation_design.md`, `director/agent_reply.py`, `director/ids.py`, `director/tests/helper.py`, `director/tests/test_agent_reply.py`, `director/tests/test_one_round_trip.py`, 本記録。
- orchestrator: `orchestrator/invocation.py`, `dispatch.py`, `launcher.py`, `mail_adapter.py`, `README.md`, `tests/test_launcher.py`, `tests/test_mail_adapter.py`。
- `aiagent-mail`は変更不要。本文JSONを既存bodyへ格納できるため、Phase 1/3のためのDBスキーマ拡張は行わない。

### テスト結果

- director: 37件成功。
- orchestrator: 143件成功。
- mail: 56件成功（変更なしの基準確認）。
- 3リポジトリの`compileall`成功。
- director/orchestratorの`git diff --check`および`git diff --cached --check`成功。
- 独立レビューで、完全UUID4、launch境界の型・一致検証、構造化status正本化、空Invocation-IDのfail-closed、Windowsテスト手順を追加確認・修正した。

### コミットID

- ai-director: `db4d769` (`fix: complete invocation id propagation`)
- ai-orchestrator: `437d139` (`fix: enforce structured invocation correlation`)

### 残課題

- Phase 1としてなし。第三者転送・遅延/重複返信はPhase 2、SYSTEM_ALERT除外はPhase 3で対応する。

## フェーズ2: 第三者転送の正常終了

### 調査結果・判断理由

- 再起動時点の未コミット実装は第三者宛`DELEGATED`を認識できたが、Directorの
  重複QUESTION、終端後の遅延返信、既読trigger再試行で状態破損または例外終了を
  再現したため、そのままコミットしなかった。
- 本文JSONを正本としつつ、同じ本文から作られる起動環境との自己一致だけではなく、
  Directorが実際に発行した子タスクの親Invocationと送信メールIDを照合する。
- 誤相関はJobを`HUMAN_REQUIRED`へ変更せず、当該Director Invocationだけを
  `FAILED`として隔離する。拒否triggerの再試行でも結果を`FAILED`に固定する。

### 変更ファイル

- director: `director.py`, `state_machine.py`, `ids.py`, `agent_reply.py`,
  `SPEC.md`, `tests/test_agent_reply.py`, `tests/test_minimal_loop.py`,
  `real04_mitigation_design.md`。
- orchestrator: `invocation.py`, `dispatch.py`, `launcher.py`, `mail_adapter.py`,
  `runtime.py`, `logging_utils.py`, `orchestrator.py`, `SPEC.md`, `TESTCASE.md`,
  関連テストとfake。
- mail: `receive_mail(uid, mail_id=...)`の原子的な単一メール受信、仕様、テスト。

### テスト結果

- director: 48件成功。
- orchestrator: 160件成功。
- mail: 61件成功。
- 3リポジトリの`compileall`、`git diff --check`、`git diff --cached --check`成功。
- 正常、第三者DELEGATED、重複結果の最小メールID、遅延返信、誤Invocation、
  forged parent/trigger、タイムアウト境界、既読trigger再試行を確認した。
- 独立レビューで、root parentの`null`契約、空decision_id照合、Subjectを正本に
  しない照合、発行済み子タスクとの相関、拒否triggerの結果反転を追加修正した。

### コミットID

- ai-director: `c3a51a9` (`feat: correlate delegated invocation results`)
- ai-orchestrator: `077209a` (`feat: complete delegated invocation results`)
- aiagent-mail: `13c7e99` (`feat: receive one exact mail atomically`)

### 残課題

- Phase 2としてなし。`SYSTEM_ALERT/task_eligible=false`の起動除外はPhase 3で行う。

## フェーズ3: 制御通知による誤起動防止

### 調査結果・判断理由

- mail DBスキーマは変更せず、通知本文JSONの`message_type=SYSTEM_ALERT`と
  `task_eligible=false`を正本にした。Subjectの`NO_REPLY`等は表示用途だけにした。
- Orchestratorは起動候補を選ぶ前に制御通知をterminal indexへ永続記録し、CLIを
  起動しない。メールは未読のままなので、人間または宛先AIが後から確認できる。
- Directorにも防御的フィルタを置いた。独立レビューで、未知Jobの通知に対して
  Job作成を先に行うと`Decision-ID missing`または不要な`DISCOVERED`作成になる問題を
  再現したため、Job作成前の無状態な無視へ修正した。
- 構造化通知へstdout/stderr末尾を載せるため、JSONエスケープ後のサイズを上限内に
  収め、一般的な`*_TOKEN/*_SECRET*/*_PASSWORD/*_COOKIE/*_API_KEY`代入値も
  StreamCaptureでマスクするよう補強した。

### 変更ファイル

- director: `director/director.py`, `director/tests/test_minimal_loop.py`,
  `director/SPEC.md`, `real04_mitigation_design.md`, 本記録。
- orchestrator: `orchestrator/dispatch.py`, `output_capture.py`, `SPEC.md`,
  `TESTCASE.md`, `tests/test_dispatch.py`, `tests/test_output_capture.py`。
- mail: 変更なし。

### テスト結果

- director: 51件成功。
- orchestrator: 165件成功。
- mail: 61件成功（変更なしの基準確認）。
- SYSTEM_ALERTの未読維持・非起動・再巡回抑止、`task_eligible=false`単独、
  壊れたJSON/メタデータ欠落/trueの通常処理、件名`NO_REPLY`の誤除外防止、
  未知/既存/終端Director Job、JSONサイズ、秘密値マスクを確認した。
- 独立レビューで、Directorの判定順、テストケースID重複、JSONエスケープ後の
  サイズ、shell/JSON形式の資格情報漏えい、1MB出力時の正規表現性能を追加修正した。

### コミットID

- ai-orchestrator: `692fa7e` (`fix: prevent control notifications from launching agents`)
- ai-director: `1139fc3` (`fix: ignore structured control notifications`)

### 残課題

- フェーズ3としてなし。REAL04実AI試験をフェーズ4で実施する。

## フェーズ4: REAL04再実行

### 試行1（REAL04-R1）

- 実行開始: 2026-08-01 14:36 JST（`JOB-20260801T053600Z-REAL04-R1`）。
- 使用AI: Director=`director`、作業AI=`claude_designer`（Claude Code）、
  指揮AI=`codex_reviewer`（Codex CLI）。
- 実フローは、起点mail 1、Director委任mail 3、Claude質問mail 6、Director判断依頼mail 7、
  Codex回答mail 9、Director再開TASK mail 10、Claude完了mail 13、Director最終完了mail 14。
- 成果物`director/tests/artifacts/real04_result.txt`は25 ASCII bytes、BOM/末尾改行なし、
  SHA-256 `0A69635949D802CA985B6B8C89F3834AB973642529AB40C171065FC34C5D92B0`で内容要件を満たした。
- `Q011`は新規質問としてANSWEREDになり、回答はUTF-8（BOMなし）だった。
- ただしmail 9起点のDirector Invocationに対してmail 11の`SYSTEM_ALERT/NO_REPLY`が1件発生したため、
  試行1は成功扱いにしない。全証跡は`director/records/_real04_runtime_20260801_R1`へ保存した。

### 試行1の原因と修正

- 再開処理がJobのDecision-IDを旧値から新値へ更新した後、worker向け再開TASK mail 10を
  Director Invocationの終端結果にも兼用した。Orchestratorは起点mail 9の旧Decision-IDと
  一致する結果を見つけられずNO_REPLYにした。
- worker向け新Decision TASKと、起点旧Decisionの`DELEGATED`終端結果を別メールへ分離した。
- 独立レビューで、子TASK送信後の状態保存失敗、終端結果送信失敗、後続相関へ切替後の遅延重複、
  手動起動時のtrigger UID消失を動的再現した。再開intentを子送信前に保存し、Outbox回収と
  不変の起点系譜によるreplayを実装した。人間通知へ遷移するworker失敗も、起動元へ別途
  `InvocationResult=FAILED`を返すようにした。

### 変更ファイル

- `director/director.py`, `director/state_machine.py`,
  `director/tests/test_minimal_loop.py`, `director/SPEC.md`,
  `real04_mitigation_design.md`, 本記録。

### テスト結果

- Director全58件成功。
- 終端結果送信失敗からの`process_once`再試行、子TASK送信後・状態保存前の中断回復、
  commander/workerの遅延重複、誤系譜、手動trigger UID、worker FAILED終端を製品経路で確認した。

### コミットID

- ai-director: `bd9db8e` (`fix: recover delegated resume invocations`)

### 残課題

- 試行1の再開相関不具合は解消。REAL04-R2で実AI経路を再確認する。

### 試行2（REAL04-R2）

- 実行: 2026-08-01 15:35–15:45 JST
  （`JOB-20260801T063515Z-REAL04-R2`）。使用AIは試行1と同じ。
- mail 1起点、mail 3初回Claude TASK、mail 6新規Q012、mail 7 Codex判断依頼、
  mail 9 Codex回答、mail 10新DecisionのClaude再開TASK、mail 11旧Decisionの
  独立`DELEGATED`結果まで正常に進み、R1のNO_REPLYは再発しなかった。
- Q012はANSWEREDとなり、mail 13時点で成果物は25 ASCII bytes、BOM/末尾改行なし、
  SHA-256 `0A69635949D802CA985B6B8C89F3834AB973642529AB40C171065FC34C5D92B0`だった。
- ただしClaudeがmail 13を製品reporterを使わず、`message_type=RESULT`かつ
  `task_eligible=false`で手組みした。Orchestratorは制御通知契約どおりこれを
  `IGNORED_CONTROL_NOTIFICATION`として除外し、Director最終検証は起動されなかった。
  正しい成果物があっても完全自動ループ未完了のため、試行2も成功扱いにしない。

### 試行2の原因と修正

- 初回/再開TASKはACKと終端結果を要求していたが、`director/agent_reply.py`を使う
  具体的コマンドと、終端`task_eligible=true`契約を本文へ含めていなかった。
- Director製品関数へ必須reply protocolを集約し、初回TASKと再開TASKの双方へ、
  `agent_reply.py ack|wait|complete|fail`の正確なコマンド、直接mail API/手組みJSONの禁止、
  COMPLETEDの`artifacts=[{path, sha256}]`形式を付与した。
- 各Job/Decision専用の具体的result fileパス、placeholderのない実行コマンド、
  complete/wait/failの最小JSON例、reporter終了コード0の完了条件も付与した。
- `task_eligible=false`の除外はPhase 3の安全要件なので弱めない。
- 既知の制限として、現方式はLLMへの必須指示であり、Orchestrator側の信頼済みpostflight
  brokerによる強制ではない。R3で同じ逸脱が再発する場合はprompt追加ではなく、AIには
  result fileだけを書かせてtrusted brokerがreporterを実行する方式を実装する。

### 変更ファイル

- `director/director.py`, `director/tests/test_minimal_loop.py`,
  `director/SPEC.md`, 本記録。

### 残課題

- 独立レビューと全テスト後、クリーンな実行状態でREAL04-R3を実行する。

### 試行3（REAL04-R3）

- 実行: 2026-08-01 15:56–16:04 JST
  （`JOB-20260801T065634Z-REAL04-R3`）。使用AIは試行1と同じ。
- mail 1起点、mail 3初回Claude TASK、mail 6新規Q013、mail 7の製品reporterによる
  `WAITING`、mail 8 Codex判断依頼、mail 10 Codex回答まで進んだ。R2で修正した
  構造化reply protocolは実AIが使用し、制御通知への誤分類は再発しなかった。
- Codex回答mail 10はJob/Decision/Invocation系譜、action=`ANSWER`、
  invocation_result=`COMPLETED`の全てが一致していたが、`confidence: 1.0`を含んだ。
  仕様書の数値例・閾値と異なり製品parserは文字列列挙値だけを受理していたため、
  mail 12を`HUMAN_REQUIRED`、mail 13を起点Invocationの`FAILED`として終了した。
- 完全自動ループ未完了のため試行3も成功扱いにしない。証跡は
  `director/records/_real04_runtime_20260801_R3`へ保存した。

### 試行3の原因と修正

- `director/SPEC.md`はJSON例に数値`0.95`、人間転送条件に`confidence < 0.7`を
  記載していたが、`parse_decision`は`HIGH|MEDIUM|LOW`だけを受理していた。
- 列挙値との後方互換性を維持しつつ、有限の`0.0..1.0`を受理して
  `>=0.85`をHIGH、`>=0.7`をMEDIUM、それ未満をLOWへ正規化した。
  bool、範囲外、NaN、Infinity、非スカラーは明示的に拒否する。
- 判断依頼の`answer_json`を具体的な機械可読契約へ変更し、現在のJob-IDと
  Decision-ID、許容confidence形式、LOW時の`requires_human=true`を明示した。

### 変更ファイル

- `director/decision.py`, `director/director.py`, `director/SPEC.md`,
  `director/tests/test_minimal_loop.py`, 本記録。

### 残課題

- Director全60件、compileall、`git diff --check`に成功した。
- 独立レビューはP0/P1なし、R4-ready判定。指摘された巨大整数の`float()`変換時の
  `OverflowError`漏れも`DecisionError`へ正規化し、数値LOW閾値を依頼payloadへ追記した。
- クリーンな実行状態でREAL04-R4を実行する。

### 試行3修正コミット

- ai-director: `6ad0b39` (`fix: accept documented decision confidence`)

### 試行4（REAL04-R4）

- 実行: 2026-08-01 16:13–16:17 JST
  （`JOB-20260801T071255Z-REAL04-R4`）。
- 使用AI: Director=`director`、作業AI=`claude_designer`（Claude Code）、
  指揮AI=`codex_reviewer`（Codex CLI）。
- mail 1起点、mail 3初回Claude TASK、mail 5 ACK、mail 6新規Q014、
  mail 7製品reporterのWAITING、mail 8 Codex判断依頼まで正常に進んだ。
- Invocation系譜:
  - root/Director: `INV-20260801T071329782Z-001-97DDAFEF-38B2-4E2F-9A43-C59933C15F2C`
    （trigger mail 1、result mail 4、WAITING）
  - Claude: `INV-20260801T071330234Z-001-9C5CEDFE-EF00-44DC-A618-BDC5CFBF4297`
    （parent=root、trigger mail 3、result mail 7、WAITING）
  - question Director: `INV-20260801T071705734Z-001-0CFD5525-1C7B-458A-8483-835FCAEA6F7F`
    （parent=Claude、trigger mail 6、result mail 8、DELEGATED）
  - Codex: `INV-20260801T071706207Z-001-35681D43-1C45-42CB-BB8B-AC7FB5EE768A`
    （parent=question Director、trigger mail 8、回答前にCLI exit 1）
- Codex CLI stderrは`You've hit your usage limit`と、次回利用可能時刻
  `Aug 8th, 2026 12:37 PM`を明示した。製品コードや判断schemaの失敗ではなく、
  外部AI利用量制限である。orchestratorはmail 9を`SYSTEM_ALERT`かつ
  `task_eligible=false`として一度だけ生成し、Directorの新規AIタスクとして起動しなかった。
- 成果物作成・Q014回答・最終COMPLETEDには未到達であり、試行4も成功扱いにしない。
  `stop.request`で新規起動を安全停止し、全証跡を
  `director/records/_real04_runtime_20260801_R4`へ保存した。
- PowerShell表示上でQandA.mdが文字化けして見えたためR4実行原本を先に証跡保存した。
  独立レビューのbyte検証では原本はBOM/U+FFFDのない正しいUTF-8だったため、R4原本を
  byte-exactでliveへ戻し、Knowledge Indexをその正本から再生成した。実ファイル破損はなかった。

### 停止条件

- 指定停止条件7「使用量制限に達した」に該当する。試行回数は4/6だが、同じCodex CLIを
  再実行しても外部制限時刻まで成功不能なため、R5/R6は実行しない。
- 制限解除後は、CLI利用可能性を確認し、R4証跡を保持したまま新しいJob/Decisionで
  クリーンなmail DBから再実行する。製品修正コミットのpushは行っていない。

### 停止時の最終検証

- ai-director: 全60件成功、compileall成功、`git diff --check`成功。
- ai-orchestrator: 全165件成功、compileall成功、`git diff --check`成功。
- aiagent-mail: 全61件成功、compileall成功、`git diff --check`成功。
- REAL04-R4の成果物は未作成、Q014はOPENであり、成功として記録していない。
- 独立事後レビューはP0/P1なし。usage-limit証跡、全Invocation系譜、mail 9の一回限りの
  非起動、未読維持、Jobの`DECISION_PENDING`、成果物なしが相互に整合すると確認した。
- Phase 4停止記録コミット: ai-director `5e2d321`
  (`chore: record REAL04 usage limit stop`)。pushは実施していない。

## Claude Code continuation

- 引継ぎ開始時刻: 2026-08-01 17:35 JST（Codex Sol停止条件7からの継続）
- 方針: R4証跡を保持し、クリーンな新Job/Decision/Invocationで再試験する。pushは行わない。

### 各リポジトリのブランチとHEAD（引継ぎ時点）

| リポジトリ | ブランチ | 開始時HEAD | git status |
|---|---|---|---|
| ai-director | `agent/director-waiting-protocol` | `2650d0d` | clean（未追跡なし） |
| ai-orchestrator | `agent/spec-proofread-qanda` | `692fa7e` | clean |
| aiagent-mail | `main` | `13c7e99` | clean |

### 確認したSolコミット

指定された12件すべてが存在し、各HEADから到達可能であることを`git merge-base --is-ancestor`で確認した。

- ai-director: `db4d769`, `c3a51a9`, `1139fc3`, `bd9db8e`, `9f058be`, `6ad0b39`, `5e2d321`, `2650d0d`
- ai-orchestrator: `437d139`, `077209a`, `692fa7e`
- aiagent-mail: `13c7e99`

3リポジトリとも`git diff --check`、`git diff --cached --check`は成功。未コミット変更・ローカルコミットの破棄は行っていない。

### 調査結果

- 基準テストはSol報告値と完全一致した（director 60、orchestrator 165、mail 61、合計286）。
  orchestratorは`PYTHONPATH=orchestrator`が必要で、未設定だと9件がimportエラーになる。
  これは実行構成の問題でありコードの退行ではない。
- ai-director配下の`orchestrator/`と`mail/`はvendored copyで`.git/info/exclude`により非追跡。
  正式リポジトリと`diff -r`でバイト一致を確認したため、正式リポジトリのテストは
  REAL04が実際に実行するコードをそのまま検証している。
- R4のmail 9は`message_type=SYSTEM_ALERT`かつ`task_eligible=false`であり、
  `IGNORED_CONTROL_NOTIFICATION`として1回だけ記録され、未読のまま、AIを起動していない。
  R4のorchestrator.jsonlでも起動は director/claude/director/codex/director の5回のみで、
  通知メールによる誤起動は発生していない。Solの報告を証跡とコードの両方で確認した。
- Codex CLIは利用可能に復帰していた。最小プローブ（`codex exec`で1プロンプト、
  4,790トークン）が正常応答したため、代替ワーカーではなく本来のCodexでR5を実施した。

### レビュー結果と修正（P1を1件発見）

`director/agent_reply.py`の`mask_secrets`が**直列化後のJSON文字列**に対して行単位の
マスクを行っていた。本文が token/secret/password/authorization/cookie/api_key を
含むと`"key": "value"`行全体が`[REDACTED_SECRET_LINE]`へ置換され、本文が
**JSONとして壊れる**。orchestratorは解析不能な本文を「構造化payloadなし」とみなすため
`_invocation_matches`が不成立となり、正しい返信が**NO_REPLYと誤判定**される。
これはREAL04が解消しようとしている失敗そのものであり、再現実験で確認した。

修正: 直列化前に値側でマスクする方式へ変更した。
- 秘密語を含むキーは値を`[REDACTED_SECRET_VALUE]`に置換
- 文字列値の内部は行単位でマスク（JSON文字列として妥当なまま）
- 相関フィールド（invocation/parent/root/trigger/job/decision、artifactの path・sha256）は
  `_PROTECTED_KEYS`として一切マスクしない

秘匿性は弱めていない（キー名による値の秘匿を追加したため、むしろ強化されている）。

### 追加テスト

`director/tests/test_agent_reply.py`に2件追加。
- `test_masked_body_stays_valid_json_when_text_mentions_secrets`
- `test_masked_body_redacts_secret_keys_and_nested_values`

### 全テスト結果（修正後）

- ai-director: 62件成功（60 + 追加2）
- ai-orchestrator: 165件成功
- aiagent-mail: 61件成功
- 合計288件成功。3リポジトリの`compileall`、`git diff --check`、`git diff --cached --check`成功。
- テストのskip・削除・期待値の弱体化は行っていない。

### REAL04-R5（試行5、Claude Code側の新規試行1回目）

- 実行: 2026-08-01 17:54–18:08 JST
- Job-ID: `JOB-20260801T085449Z-REAL04-R5`
- Decision-ID（初期）: `DEC-20260801T085449Z-01-R405`
- Decision-ID（再開の子）: `DEC-20260801T090555Z-02-BBA2`
- 使用AI: Director=`director`、作業AI=`claude_designer`（Claude Code）、
  指揮AI=`codex_reviewer`（Codex CLI、実AI）
- 事前準備: R4のライブDBは`director/records/_pre_real05_archive_20260801T084917Z/`へ
  moveで退避（削除ではない。R4証跡内のコピーとmd5一致を確認済み）。空DBから
  R4と同じ順序でユーザー登録し、UIDの一致を確認した。

#### Invocation系譜（すべて root = `INV-20260801T085902355Z-001-B53CD244-...`）

| 役割 | Invocation-ID | parent | trigger mail |
|---|---|---|---|
| root/Director | `INV-20260801T085902355Z-001-B53CD244` | null | 1 |
| Claude（初回） | `INV-20260801T085902691Z-001-D99E73E8` | root | 3 |
| question Director | `INV-20260801T090209650Z-001-7CB1E1A1` | Claude初回 | 6 |
| Codex | `INV-20260801T090210155Z-001-3FC309FC` | question Director | 8 |
| 起点結果Director | `INV-20260801T090552591Z-001-400130xx` | Claude初回 | 7 |
| 再開Director | `INV-20260801T090554922Z-001-49F376B0` | Codex | 10 |
| Claude（再開） | `INV-20260801T090555395Z-001-E6BE7F62` | 再開Director | 12 |
| 最終Director | `INV-20260801T090745751Z-001-49F2B5F0` | Claude再開 | 15 |

#### 関連メールUID（全16通）

1 起点依頼、2 Director ACK、3 Claude宛TASK、4 WAITING_FOR_WORKER、
5 Claude ACK、6 QUESTION Q015、7 Claude WAITING（製品reporter）、
8 Codex宛DECISION_REQUEST、9 Codex ACK、10 Codex回答（COMPLETED/ANSWER/confidence=HIGH）、
11 旧DecisionのClaude宛終端結果、12 新DecisionのClaude再開TASK、
13 Codex宛終端結果（DELEGATED）、14 Claude ACK、15 Claude COMPLETED、
16 Director最終COMPLETED（human_controller宛）。

#### 成果物

- `director/tests/artifacts/real04_result.txt`
- 25 ASCII bytes、BOMなし、末尾改行なし、内容`REAL04_AUTONOMOUS_LOOP_OK`
- SHA-256 `0A69635949D802CA985B6B8C89F3834AB973642529AB40C171065FC34C5D92B0`
- Directorが独立にファイル存在とSHA-256を再計算して検証した。

#### 判定: 成功

- orchestrator結果内訳: `SUCCESS` 8件、`IGNORED_CONTROL_NOTIFICATION` 5件。
  **NO_REPLYは0件、SYSTEM_ALERTは0件、FAILEDは0件。**
- 起動は8回で、いずれも起動対象メール1通につき1回のみ（重複起動なし）。
- 制御通知（mail 5, 9, 11, 13, 14 = `task_eligible=false`）はそれぞれ1回だけ
  処理され、AIを起動していない。
- Q015は本Job用に新規作成され`ANSWERED`。Q014はOPENのまま変更していない。
- Job状態は`COMPLETED`、`result_mail_uid=16`。
- 全Invocationのparent/root/trigger_mail_uidが相互に整合する。
- 停止は`stop.request`による安全停止で、orchestratorは終了コード0で正常終了した。

#### 証跡保存場所

- `director/records/_real04_runtime_20260801_R5`（48ファイル）
- R4証跡`_real04_runtime_20260801_R4`は変更・上書きしていない（41ファイル、DBのmd5一致で確認）。

### コミットID

- ai-director: `44556d3` (`fix: keep masked result bodies valid JSON`)
- ai-orchestrator: 変更なし
- aiagent-mail: 変更なし

### 残課題・既知の制限

- R5ではどの段階も失敗しなかったため、SYSTEM_ALERT自体が生成されていない。
  「SYSTEM_ALERTがAIを起動しない」ことの実証はR4のmail 9の証跡と単体テストによる。
  R5が直接示したのは`task_eligible=false`の制御通知5件が一度ずつ処理され
  AIを起動しないことである。
- 構造化reply protocolの遵守は依然としてLLMへの指示であり、orchestrator側の
  信頼済みbrokerによる強制ではない（Solの記載と同じ制限）。
- Q014はR4の失敗証跡としてOPENのまま残している。人間が閉じるか否かを判断すること。
- pushは実施していない。
