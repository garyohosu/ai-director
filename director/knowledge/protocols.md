---
source_commit: "1fa84bb518bd20d99c24ae3a328cce8cedea7e08"
generated_at: "2026-07-31T07:31:34Z"
source_files:
  - director/SPEC.md
  - QandA.md
source_sha256:
  director/SPEC.md: bf35d3071169ece1868f34ee69c171105e2175c913a5ae48795534ff4f391d82
  QandA.md: 99adb3d32680f23a4f8c80835795c9f3654977c0fba2d79350ad35ba54198b4c
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
