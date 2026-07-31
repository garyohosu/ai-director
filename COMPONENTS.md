# COMPONENTS.md

本リポジトリで参照・連携する外部・関連コンポーネントのバージョンおよびリポジトリ対応記録です。

## mail

- Repository: https://github.com/garyohosu/aiagent-mail
- Commit: `f24af8b` ("fix: finalize safe find_mails query API")
- Local path: `C:\PROJECT\aiagent-mail`
- 状態: 今回コード変更なし（独立リポジトリ `aiagent-mail` にて保守）

## orchestrator

- Repository: https://github.com/garyohosu/ai-orchestrator
- Commit: `d39034a` ("fix: stop CLI on terminal mail notifications")
- Local path: `C:\PROJECT\ai-orchestrator`
- 状態: `agent_reply` サポート環境変数注入、Decision-ID保持、directorアダプター、WAITING_FOR_DECISIONおよび構造化タイムアウト通知を反映済み
- テスト結果: 128/128件成功。WAITING/終端通知の起動中検知、猶予後安全停止、外側タイムアウト順序を含む

## director

- Repository: https://github.com/garyohosu/ai-director
- Commit: `3b30c5e` ("feat: add lightweight knowledge index")
- 状態: WAITING_FOR_DECISION、構造化判断、Q&A再利用、Knowledge Index、Context Packet、Outbox復旧を反映済み
