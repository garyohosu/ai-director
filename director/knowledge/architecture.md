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
# Architecture

mailは配送、orchestratorはCLI起動・監視、directorは判断の進行管理を担う。

基本遷移は `DISCOVERED → ACK_SENT → DELEGATION_PENDING → WORKER_RUNNING`。Blocking質問は `WORKER_WAITING_QUESTION → WAITING_FOR_DECISION → DECISION_PENDING`、回答後は `ANSWER_PENDING → WORKER_RESUMED → VERIFYING` と進む。成果物と終端通知を検証して `OUTBOX_PENDING → COMPLETED` とする。判断不能・タイムアウト・解析不能は `HUMAN_REQUIRED`。

Knowledge Indexは短い参照情報であり、正式仕様を変更しない。詳細は `director/SPEC.md` を参照する。
