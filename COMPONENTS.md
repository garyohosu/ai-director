# COMPONENTS.md

本リポジトリに同梱されている外部・先行コンポーネントのバージョン管理記録です。

## mail

- Repository: https://github.com/garyohosu/aiagent-mail
- Commit: 不明（要確認）
- Imported: 2026-07-31
- Local modifications: なし
- 状態確認:
  - 過去データ / DB: なし（data/ は空または初期状態）
  - ログ・実行情報: なし
  - テスト結果: 56/56件成功 (py -3 -m unittest discover -s mail/tests)

## orchestrator

- Repository: (ローカル先行開発版よりコピー)
- Commit: 不明（要確認）
- Imported: 2026-07-31
- Local modifications: なし
- 状態確認:
  - 過去データ / DB: なし
  - stop.request: なし
  - logs / checkpoints / runtime: .gitkeep のみ
  - テスト結果: 120/120件成功 (py -3 -m unittest discover -s orchestrator/tests -t orchestrator)