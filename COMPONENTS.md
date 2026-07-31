# COMPONENTS.md

本リポジトリで参照・連携する外部・関連コンポーネントのバージョンおよびリポジトリ対応記録です。

## mail

- Repository: https://github.com/garyohosu/aiagent-mail
- Commit: `f24af8b` ("fix: finalize safe find_mails query API")
- Local path: `C:\PROJECT\aiagent-mail`
- 状態: 今回コード変更なし（独立リポジトリ `aiagent-mail` にて保守）

## orchestrator

- Repository: https://github.com/garyohosu/ai-orchestrator
- Commit: `d21218c13787e869254f42050ec256216a26e013` ("feat: support waiting-for-worker invocation completion")
- Local path: `C:\PROJECT\ai-orchestrator`
- 状態: `agent_reply` サポート環境変数注入、Decision-ID・Invocation-ID保持、directorアダプター、WAITING_FOR_DECISIONおよび構造化タイムアウト通知を反映済み
- テスト結果: 133/133件成功。起動前最大メールID、Invocation-ID一致、ACKのみの未完了判定、WAITING_FOR_DECISION/WAITING_FOR_WORKER/終端通知の起動中検知、NO_REPLY抑止、猶予後安全停止を含む

## director

- Repository: https://github.com/garyohosu/ai-director
- Commit: `3186c62e257b1c24d6f40d174f1f0a6a24061e89` (Q009・WAITING_FOR_WORKER実装)
- 状態: WAITING_FOR_DECISION、構造化判断、Q&A再利用、Knowledge Index、Context Packet、Outbox復旧を反映済み
