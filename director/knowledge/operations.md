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
# Operations

reply-check猶予 < CLI内部タイムアウト < orchestrator監視期限 < テストハーネス外側タイムアウトとする。orchestratorはmailの公開 `find_mails()` だけでWAITING/終端通知を非破壊検索し、検知後に短い猶予を与える。自然終了しなければ既存の安全停止処理を使い、WAITING検知をTIMEOUTに分類しない。REAL01の約153秒のACK観測を下回る外側期限を使用しない。

通常のTIMEOUT、RATE_LIMITED、クラッシュ、通知失敗は構造化記録を残し、必要なら `HUMAN_REQUIRED` とする。通知にはJob-ID、Decision-ID、Invocation-ID、対象AI、CLI起動時刻、終了理由を含め、Invocationを依頼した送信元UIDへ返す。directorはAI CLIを直接起動しない。

director再起動時は状態JSONとcheckpointからInvocation-IDを復元する。HUMAN_REQUIREDのJobを状態ファイルの直接編集で再開してはならず、人間の新しい指示と新しいJobを要求する。
