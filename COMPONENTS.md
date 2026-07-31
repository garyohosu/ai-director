# COMPONENTS.md

本リポジトリで参照・連携する外部・関連コンポーネントのバージョンおよびリポジトリ対応記録です。

## mail

- Repository: https://github.com/garyohosu/aiagent-mail
- Commit: `f24af8b` ("fix: finalize safe find_mails query API")
- Local path: `C:\PROJECT\aiagent-mail`
- 状態: 今回コード変更なし（独立リポジトリ `aiagent-mail` にて保守）

## orchestrator

- Repository: https://github.com/garyohosu/ai-orchestrator
- Commit: `9714828` ("feat: add env_vars propagation, decision_id support, and NOJOB warning logging")
- Local path: `C:\PROJECT\ai-orchestrator`
- 状態: `agent_reply` サポート環境変数注入、Decision-ID保持および NOJOB 警告ログ出力を本リポジトリおよび `ai-orchestrator` 側へ反映済み
- テスト結果: 123/123件成功