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
# Decisions

QandA.mdでANSWEREDになった正式判断の索引。内容の正本は必ずQandA.mdにある。

- [Q001](../../QandA.md#q001): Question: mail および orchestrator のコピー元コミットID（リポジトリのコミットハッシュ）の確定方法について。
- [Q002](../../QandA.md#q002): Question: director 自身の UID 管理について。既存 orchestrator では人間やシステム用の UID が予約・登録されていますが、director 用のデフォルト UID（例: UID000000 または DIRECTOR）の規約を固定すべきか？
- [Q003](../../QandA.md#q003): Question: orchestrator 経由で director が起動された際、director 内部から指揮AI（Codex CLI）を同期的に呼び出す処理のタイムアウト時間やAPIエラー時のリトライ方針。
- [Q004](../../QandA.md#q004): Question: directorの応答義務（orchestratorのreply-check整合性）と終端通知の扱いについて。
- [Q005](../../QandA.md#q005): Question: orchestratorのdirector対応（director用CLIアダプターおよび設定追加）の扱いについて。
- [Q006](../../QandA.md#q006): Question: 成果物ファイルは末尾改行なしの一行で作成してよいですか？
- [Q007](../../QandA.md#q007): Question: 成果物は指定文字列の一行を末尾改行なしで作成してよいですか？
- [Q009](../../QandA.md#q009): Question: director Python CLIをorchestratorの一つのInvocationとして起動した場合、処理済みを示すInvocation-ID付き終端通知をdirectorが送信する契約にするか、orchestratorがdirector専用のstdout成功契約を検証するか？
