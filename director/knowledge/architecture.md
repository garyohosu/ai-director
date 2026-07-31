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
# Architecture

mailは配送、orchestratorはCLI起動・監視、directorは判断の進行管理を担う。

基本遷移は `DISCOVERED → ACK_SENT → DELEGATION_PENDING → WORKER_RUNNING`。Blocking質問は `WORKER_WAITING_QUESTION → WAITING_FOR_DECISION → DECISION_PENDING`、回答後は `ANSWER_PENDING → WORKER_RESUMED → VERIFYING` と進む。成果物と終端通知を検証して `OUTBOX_PENDING → COMPLETED` とする。判断不能・タイムアウト・解析不能は `HUMAN_REQUIRED`。

Job-IDは仕事、Decision-IDは判断、Invocation-IDは一つのCLIプロセスを識別する。ACK、WAITING_FOR_DECISION、WAITING_FOR_WORKER、COMPLETED、FAILEDは同じInvocation-IDを保持し、再開時には新しいInvocation-IDを発行する。directorがworkerへ委任して終了する場合はWAITING_FOR_WORKERを送信する。

Knowledge Indexは短い参照情報であり、正式仕様を変更しない。詳細は `director/SPEC.md` を参照する。
