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
# Operations

CLI内部タイムアウト < orchestrator監視期限 < テストハーネス外側タイムアウトとする。orchestratorはmailの公開 `find_mails()` だけでWAITING/終端通知を非破壊検索し、検知後に短い猶予を与える。自然終了しなければ既存の安全停止処理を使い、WAITING検知をTIMEOUTに分類しない。

通常のTIMEOUT、RATE_LIMITED、クラッシュ、通知失敗は構造化記録を残し、必要なら `HUMAN_REQUIRED` とする。directorはAI CLIを直接起動しない。
