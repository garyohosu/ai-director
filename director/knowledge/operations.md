---
source_commit: "80448fafddfe0eb136d13c1157a466e1690f51ed"
generated_at: "2026-07-31T07:04:56Z"
source_files:
  - director/SPEC.md
  - QandA.md
source_sha256:
  director/SPEC.md: bf35d3071169ece1868f34ee69c171105e2175c913a5ae48795534ff4f391d82
  QandA.md: a47a6b1d345f01b6393a37ada49a6b70b5bcae199ba6ec7b7982b7d84eea20a9
---
# Operations

reply-check猶予 < CLI内部タイムアウト < orchestrator監視期限 < テストハーネス外側タイムアウトとする。orchestratorはmailの公開 `find_mails()` だけでWAITING/終端通知を非破壊検索し、検知後に短い猶予を与える。自然終了しなければ既存の安全停止処理を使い、WAITING検知をTIMEOUTに分類しない。REAL01の約153秒のACK観測を下回る外側期限を使用しない。

通常のTIMEOUT、RATE_LIMITED、クラッシュ、通知失敗は構造化記録を残し、必要なら `HUMAN_REQUIRED` とする。通知にはJob-ID、Decision-ID、Invocation-ID、対象AI、CLI起動時刻、終了理由を含め、Invocationを依頼した送信元UIDへ返す。directorはAI CLIを直接起動しない。

director再起動時は状態JSONとcheckpointからInvocation-IDを復元する。HUMAN_REQUIREDのJobを状態ファイルの直接編集で再開してはならず、人間の新しい指示と新しいJobを要求する。
