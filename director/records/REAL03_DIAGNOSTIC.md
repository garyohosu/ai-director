# REAL03起動経路診断記録

- 判定: FAILED（実AI再試行なし）
- Job-ID: `JOB-20260731T063000Z-REAL03`
- Decision-ID: `DEC-20260731T063000Z-01-7F3A`
- 使用コンポーネント: mail `f24af8b`、orchestrator `a180e07`、director `fb83954`

## 確認結果

- director Invocation: `INV-20260731T063028688Z-001-63FF3FAF`
- Claude Invocation: `INV-20260731T063054061Z-001-4C9C7250`
- directorはACKと委任メールを送信したが、Invocation-ID付きの起動単位終端通知を送信しなかった。
- orchestratorはdirectorをNO_REPLYとして通知し、その後Claudeを1回起動した。
- Claudeは約61秒後、試験停止処理で停止された。Claude側の終了コード・自然終了は確認できない。
- Q008、WAITING_FOR_DECISION、Codex起動、成果物、COMPLETEDは発生していない。

## REAL01との比較

REAL01にはdirector成功ログとClaudeのACK/Q006メールが残るが、Claudeの最終終了証跡は残っていない。REAL01のClaude ACKは起動から約153秒後であり、REAL03のClaude停止までの約61秒より長い。したがってREAL03のstdin不達・環境変数欠落を証拠から断定できない。

## Fake CLI

Fake CLIでstdin EOF、stdin 732 bytes、必須環境変数7項目、作業ディレクトリ、Invocation-IDを検証し、ACK・Q008・WAITING_FOR_DECISIONを送信して終了コード0となることを確認した。stdin SHA-256とargv SHA-256は実行ログに記録済みで、本文は正式記録へ転載しない。

## 未解決事項

director Python CLIのInvocation終端通知をdirectorが送信するか、orchestratorがdirector専用のstdout成功契約を検証するかはQ009 OPENとして記録した。契約確定までコード変更しない。
