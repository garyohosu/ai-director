---
source_commit: "0655873b4277d5b2579be0b3c7d271ca0462df26"
generated_at: "2026-08-01T09:34:07Z"
source_files:
  - director/SPEC.md
  - QandA.md
source_sha256:
  director/SPEC.md: 351c20d791b2fa1631e6e42492ab991138f657f4dde3425a73d2505e44f235d6
  QandA.md: 94faf3041ba615337a4fa98fb97a8b4ea0a1f07127a4b13c5ae46bd4f9f609d7
---
# Architecture

mailは配送、orchestratorはCLI起動・監視、directorは判断の進行管理を担う。

基本遷移は `DISCOVERED → ACK_SENT → DELEGATION_PENDING → WORKER_RUNNING`。Blocking質問は `WORKER_WAITING_QUESTION → WAITING_FOR_DECISION → DECISION_PENDING`、回答後は `ANSWER_PENDING → WORKER_RESUMED → VERIFYING` と進む。成果物と終端通知を検証して `OUTBOX_PENDING → COMPLETED` とする。判断不能・タイムアウト・解析不能は `HUMAN_REQUIRED`。

Job-IDは仕事、Decision-IDは判断、Invocation-IDは一つのCLIプロセスを識別する。ACK、WAITING_FOR_DECISION、WAITING_FOR_WORKER、COMPLETED、FAILEDは同じInvocation-IDを保持し、再開時には新しいInvocation-IDを発行する。directorがworkerへ委任して終了する場合はWAITING_FOR_WORKERを送信する。

Knowledge Indexは短い参照情報であり、正式仕様を変更しない。詳細は `director/SPEC.md` を参照する。
