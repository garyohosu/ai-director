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
- ai-director: この記録を含むフェーズ3コミットとして作成する。

### 残課題

- フェーズ3としてなし。REAL04実AI試験をフェーズ4で実施する。

## フェーズ4: REAL04再実行

- 未着手。
