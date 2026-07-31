---
source_commit: "c48df2e420041e999dd190351de42296cb5c4a4e"
generated_at: "2026-07-31T06:02:39Z"
source_files:
  - director/SPEC.md
  - QandA.md
source_sha256:
  director/SPEC.md: 7ec7537078927f4a41eef1ac3ab85124381395f422b43680cb6e455a314504c6
  QandA.md: d2f5f7388082002cc6f4724ef6a4bf87e955dc083a323e8affcee6e4058eaaf2
---
# Protocols

- ACKは受信確認であり、完了ではない。
- WAITING_FOR_DECISIONはCLI起動単位の正常終了で、Job全体は非終端。
- COMPLETEDは終端通知であり、成果物の相対パスとSHA-256を検証する。
- Job-IDとDecision-IDは件名・本文・状態JSONで一致させる。Invocation-IDも同様に一致させ、過去Invocationの応答を現在Invocationへ流用しない。
- 同一CLIプロセスのACK、WAITING_FOR_DECISION、COMPLETED、FAILEDは同じInvocation-IDを使う。Claudeを再起動する回答後の再開では新しいInvocation-IDを使う。
- 質問後は同一CLIで回答を待たず、wait通知後に終了し、新規コンテキストで再開する。

NO_REPLYは、今回実行でCLIを起動し、PID・プロセス開始時刻・起動前最大メールIDを記録し、同一Job/Decision/Invocationの有効な応答がなく、CLIが終了またはタイムアウトし、ACK以外の起動単位終端通知もない場合だけ送信する。CLI未起動、WAITING_FOR_DECISION、COMPLETEDではNO_REPLYを送信しない。

正式な送信・検証規則は `director/SPEC.md` と `QandA.md` を優先する。
