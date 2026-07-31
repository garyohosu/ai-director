---
source_commit: "3b30c5e342e9fd4a7f5128bc92c58bc78e96a69f"
generated_at: "2026-07-31T05:29:11Z"
source_files:
  - director/SPEC.md
  - QandA.md
source_sha256:
  director/SPEC.md: 7ec7537078927f4a41eef1ac3ab85124381395f422b43680cb6e455a314504c6
  QandA.md: b7a9977bdc9b0b4f8478fe418adbf0d1d32cf21bc9ca26b56c24d52f69d67197
---
# Protocols

- ACKは受信確認であり、完了ではない。
- WAITING_FOR_DECISIONはCLI起動単位の正常終了で、Job全体は非終端。
- COMPLETEDは終端通知であり、成果物の相対パスとSHA-256を検証する。
- Job-IDとDecision-IDは件名・本文・状態JSONで一致させる。
- 質問後は同一CLIで回答を待たず、wait通知後に終了し、新規コンテキストで再開する。

正式な送信・検証規則は `director/SPEC.md` と `QandA.md` を優先する。
