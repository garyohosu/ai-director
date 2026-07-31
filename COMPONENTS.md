# COMPONENTS.md

本リポジトリで参照・連携する外部・関連コンポーネントのバージョンおよびリポジトリ対応記録です。

## mail

- Repository: https://github.com/garyohosu/aiagent-mail
- Commit: `f24af8b` ("fix: finalize safe find_mails query API")
- Local path: `C:\PROJECT\aiagent-mail`
- 状態: 今回コード変更なし（独立リポジトリ `aiagent-mail` にて保守）

## orchestrator

- Repository: https://github.com/garyohosu/ai-orchestrator
- Commit: `a180e07b8118a9fa4ebf7065ddbb5c50b432248c` ("fix: allow adapter tests to select mail database")
- Local path: `C:\PROJECT\ai-orchestrator`
- 状態: `agent_reply` サポート環境変数注入、Decision-ID・Invocation-ID保持、directorアダプター、WAITING_FOR_DECISIONおよび構造化タイムアウト通知を反映済み
- テスト結果: 131/131件成功。起動前最大メールID、Invocation-ID一致、ACKのみの未完了判定、WAITING/終端通知の起動中検知、NO_REPLY抑止、猶予後安全停止を含む

## director

- Repository: https://github.com/garyohosu/ai-director
- Commit: `142469af6b79f5b7d607d9625f7508dc3ccdffd0` (Invocation-ID・Knowledge Index provenance更新)
- 状態: WAITING_FOR_DECISION、構造化判断、Q&A再利用、Knowledge Index、Context Packet、Outbox復旧を反映済み
