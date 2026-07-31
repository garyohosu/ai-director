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
# Architecture

mailは配送、orchestratorはCLI起動・監視、directorは判断の進行管理を担う。

基本遷移は `DISCOVERED → ACK_SENT → DELEGATION_PENDING → WORKER_RUNNING`。Blocking質問は `WORKER_WAITING_QUESTION → WAITING_FOR_DECISION → DECISION_PENDING`、回答後は `ANSWER_PENDING → WORKER_RESUMED → VERIFYING` と進む。成果物と終端通知を検証して `OUTBOX_PENDING → COMPLETED` とする。判断不能・タイムアウト・解析不能は `HUMAN_REQUIRED`。

Job-IDは仕事、Decision-IDは判断、Invocation-IDは一つのCLIプロセスを識別する。ACK、WAITING_FOR_DECISION、COMPLETED、FAILEDは同じInvocation-IDを保持し、再開時には新しいInvocation-IDを発行する。

Knowledge Indexは短い参照情報であり、正式仕様を変更しない。詳細は `director/SPEC.md` を参照する。
