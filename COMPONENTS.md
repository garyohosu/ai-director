# COMPONENTS.md

本リポジトリで参照・連携する外部・関連コンポーネントのバージョンおよびリポジトリ対応記録です。

## mail

- Repository: https://github.com/garyohosu/aiagent-mail
- Commit: `f24af8b` ("fix: finalize safe find_mails query API")
- Local path: `C:\PROJECT\aiagent-mail`
- 状態: 今回コード変更なし（独立リポジトリ `aiagent-mail` にて保守）

## orchestrator

- Repository: https://github.com/garyohosu/ai-orchestrator
- Commit: `d2986fe106ef72f19e5d8d540f19148ab7caf7fc` ("feat: support director waiting and timeout handoff")
- Local path: `C:\PROJECT\ai-orchestrator`
- 状態: `agent_reply` サポート環境変数注入、Decision-ID保持、directorアダプター、WAITING_FOR_DECISIONおよび構造化タイムアウト通知を反映済み
- テスト結果: 125/125件成功

## director

- Repository: https://github.com/garyohosu/ai-director
- Commit: `8b17efae5a58881e3107e9da86fd1fe94cc17094` ("fix: include resume memory in context packets")
- 状態: WAITING_FOR_DECISION、構造化判断、Q&A再利用、Context Packet、Outbox復旧を反映済み
