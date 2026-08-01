---
source_commit: "6ad0b3923cde2bde4cd1de75e2d4ad55690edb21"
generated_at: "2026-08-01T07:20:15Z"
source_files:
  - director/SPEC.md
  - QandA.md
source_sha256:
  director/SPEC.md: 351c20d791b2fa1631e6e42492ab991138f657f4dde3425a73d2505e44f235d6
  QandA.md: 183d47f5e790db7fe57a6475d5858aece8edd8f8ad26f38ff589c6276aa6ed39
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
- [Q011](../../QandA.md#q011): Question: 成果物 director/tests/artifacts/real04_result.txt の文字コードはUTF-8(BOMなし)とUTF-8(BOMあり)のどちらにしますか?
- [Q012](../../QandA.md#q012): Question: 本R2受入試験の成果物 director/tests/artifacts/real04_result.txt について、(a)文字コードをUTF-8(BOMなし)とUTF-8(BOMあり)のどちらにするか、(b)末尾改行を付けないことを確定してよいか、(c)結果として全体バイト長を厳密に25バイトとしてよいか、の3点の確定回答をください。
