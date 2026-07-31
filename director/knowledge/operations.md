---
source_commit: "fb83954a69306050c18eede211600e83e688ae0c"
generated_at: "2026-07-31T06:53:40Z"
source_files:
  - director/SPEC.md
  - QandA.md
source_sha256:
  director/SPEC.md: 7ec7537078927f4a41eef1ac3ab85124381395f422b43680cb6e455a314504c6
  QandA.md: f81bca83ccc334b5af3ff5a62fe7f073208dd1cf14cc99ff020f90ae56b91714
---
# Operations

CLI内部タイムアウト < orchestrator監視期限 < テストハーネス外側タイムアウトとする。orchestratorはmailの公開 `find_mails()` だけでWAITING/終端通知を非破壊検索し、検知後に短い猶予を与える。自然終了しなければ既存の安全停止処理を使い、WAITING検知をTIMEOUTに分類しない。

通常のTIMEOUT、RATE_LIMITED、クラッシュ、通知失敗は構造化記録を残し、必要なら `HUMAN_REQUIRED` とする。通知にはJob-ID、Decision-ID、Invocation-ID、対象AI、CLI起動時刻、終了理由を含め、Invocationを依頼した送信元UIDへ返す。directorはAI CLIを直接起動しない。

director再起動時は状態JSONとcheckpointからInvocation-IDを復元する。HUMAN_REQUIREDのJobを状態ファイルの直接編集で再開してはならず、人間の新しい指示と新しいJobを要求する。
