---
source_commit: "5e2d321535f15db232aa414c13fb708071da8953"
generated_at: "2026-08-01T07:24:49Z"
source_files:
  - director/SPEC.md
  - QandA.md
source_sha256:
  director/SPEC.md: 351c20d791b2fa1631e6e42492ab991138f657f4dde3425a73d2505e44f235d6
  QandA.md: f2e28837661474bbfa28e94d44c2c2ad3c46963a8b82e933a00f0cbb1f48201d
---
# Protocols

- ACKは受信確認であり、完了ではない。
- WAITING_FOR_DECISIONはCLI起動単位の正常終了で、Job全体は非終端。
- WAITING_FOR_WORKERはdirector Invocationの正常終了で、Job全体は非終端。委任先workerは別Invocationとして追跡する。
- COMPLETEDは終端通知であり、成果物の相対パスとSHA-256を検証する。
- Job-IDとDecision-IDは件名・本文・状態JSONで一致させる。Invocation-IDも同様に一致させ、過去Invocationの応答を現在Invocationへ流用しない。
- 同一CLIプロセスのACK、WAITING_FOR_DECISION、COMPLETED、FAILEDは同じInvocation-IDを使う。Claudeを再起動する回答後の再開では新しいInvocation-IDを使う。
- 質問後は同一CLIで回答を待たず、wait通知後に終了し、新規コンテキストで再開する。

NO_REPLYは、今回実行でCLIを起動し、PID・プロセス開始時刻・起動前最大メールIDを記録し、同一Job/Decision/Invocationの有効な応答がなく、CLIが終了またはタイムアウトし、ACK以外の起動単位終端通知もない場合だけ送信する。CLI未起動、WAITING_FOR_DECISION、WAITING_FOR_WORKER、COMPLETEDではNO_REPLYを送信しない。

正式な送信・検証規則は `director/SPEC.md` と `QandA.md` を優先する。
