---
source_commit: "2628b151bb22c36971f280bd55cb5ef51b6168d5"
generated_at: "2026-07-31T05:28:58Z"
source_files:
  - director/SPEC.md
  - QandA.md
source_sha256:
  director/SPEC.md: 7ec7537078927f4a41eef1ac3ab85124381395f422b43680cb6e455a314504c6
  QandA.md: b7a9977bdc9b0b4f8478fe418adbf0d1d32cf21bc9ca26b56c24d52f69d67197
---
# Decisions

QandA.mdでANSWEREDになった正式判断の索引。内容の正本は必ずQandA.mdにある。

- [Q001](../../QandA.md#q001): Question: mail および orchestrator のコピー元コミットID（リポジトリのコミットハッシュ）の確定方法について。
- [Q002](../../QandA.md#q002): Question: director 自身の UID 管理について。既存 orchestrator では人間やシステム用の UID が予約・登録されていますが、director 用のデフォルト UID（例: UID000000 または DIRECTOR）の規約を固定すべきか？
- [Q003](../../QandA.md#q003): Question: orchestrator 経由で director が起動された際、director 内部から指揮AI（Codex CLI）を同期的に呼び出す処理のタイムアウト時間やAPIエラー時のリトライ方針。
- [Q004](../../QandA.md#q004): Question: directorの応答義務（orchestratorのreply-check整合性）と終端通知の扱いについて。
- [Q005](../../QandA.md#q005): Question: orchestratorのdirector対応（director用CLIアダプターおよび設定追加）の扱いについて。
- [Q006](../../QandA.md#q006): Question: 成果物ファイルは末尾改行なしの一行で作成してよいですか？
