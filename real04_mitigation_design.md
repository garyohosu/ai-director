# REAL04 課題対策設計案 (real04_mitigation_design.md) - 第一段階：Invocation-ID のエンドツーエンド伝達（補強版）

本ドキュメントは、実 AI ループ試験（REAL04）で発生した「Invocation-ID 未伝達による返信確認の不一致」を解消するため、環境変数を用いた機械的な伝達、および安全な自動検証・エラー設計の補強仕様を定義します。

---

## 1. 伝達と検証の基本方針

1. **プロンプトだけに依存しない**: プロセス起動時に環境変数を通じて ID を機械的に渡し、エージェント返信時（`agent_reply.py`）に自動取得・自動付与します。
2. **環境変数名の厳格な解決規則**:
   - 正式名: `AI_INVOCATION_ID`
   - 旧名エイリアス: `INVOCATION_ID` (移行用)
   - 取得と検証の優先順位は [2. 環境変数の解決規則] に従います。
3. **固定値の禁止とエラー処理**:
   - 返信の誤関連付けや重複判定を防ぐため、固定値 `"INV-NOT-SET"` の利用は廃止します。
   - 通常モードにおいて Invocation-ID が設定されていない場合、処理を行わず必ずエラー（Exit Code != 0）とします。
4. **明示的な互換モード**:
   - 旧方式や手動実行との互換性を有効にするには、環境変数 `AI_ALLOW_MISSING_INVOCATION_ID=1` の設定を必須とします。
   - 互換モード時の Invocation-ID は、以下のいずれかで処理します。
     - `MANUAL-<完全なUUID4>`（例: `MANUAL-C91A39F0-9E8F-4F7A-8BF2-E2CBF962C21A`）を実行ごとに製品関数で生成し、本文メタデータとSubjectへ同一値を適用する。
5. **本文メタデータが正本**:
   - メールの本文構造化メタデータに格納された Invocation-ID を「正本」とします。
   - Subject の ID は人間向けの補助表示であり、照合の決定材料にはしません。Subject だけが一致し、本文と不一致であるメールは正常な返信として扱いません。
6. **find_terminal_reply の厳密一致**:
   - `find_terminal_reply` における ID 一致条件は緩和せず、厳密に照合します。

---

## 2. 環境変数の解決規則

`agent_reply.py` および実行プロセスは、起動された際に以下のルールで `Invocation-ID` を決定します。

| AI_INVOCATION_ID キーの状態 | INVOCATION_ID キーの状態 | 判定・採用される値 |
|---|---|---|
| 存在し、値あり | 存在しない | `AI_INVOCATION_ID` の値を使用。 |
| 存在しない | 存在し、値あり | `INVOCATION_ID` の値を互換値として使用。 |
| 存在し、値あり | 存在し、値あり (同値) | その値を使用。 |
| 存在し、値あり | 存在し、値あり (異値) | **送信せずエラー (exit 1)** |
| 存在（空文字 `""`）| （任意のキー状態） | **送信せずエラー (exit 1)** |
| （任意のキー状態） | 存在（空文字 `""`） | **送信せずエラー (exit 1)** |
| どちらも存在しない | どちらも存在しない | ・通常モード: **送信せずエラー (exit 1)**<br>・互換モード (`AI_ALLOW_MISSING_INVOCATION_ID=1`): `MANUAL-<完全なUUID4>` を生成して使用。 |

---

## 3. インターフェースとデータ構造

### A. メール本文の構造化メタデータ (正本)
返信結果 JSON (本文) 内の `invocation_id` フィールドが正本となります。
```json
{
  "status": "COMPLETED",
  "job_id": "JOB-20260731T072152Z-REAL04",
  "decision_id": "DEC-20260731T120000Z-01-A1F9",
  "invocation_id": "INV-20260731T072527546Z-001-4646C590",
  "artifacts": []
}
```

### B. orchestrator 側の変更
* **`orchestrator/dispatch.py`**:
  - `_launch_agent` における `env_vars` に、既存の `"INVOCATION_ID"` と同時に `"AI_INVOCATION_ID"` を同じ値で設定。
* **`orchestrator/launcher.py`**:
  - `FIXED_INSTRUCTION_TEMPLATE` に可視性補助として `Invocation-ID: {invocation_id}` を記載。
* **`orchestrator/mail_adapter.py`**:
  - `find_terminal_reply` 内の `_invocation_matches` が、Subject の一致だけではなく、**本文に含まれる Invocation-ID** を厳密に検証するようにする。

### C. director 側の変更
* **`director/agent_reply.py`**:
  - 上記 [2. 環境変数の解決規則] に従った環境変数解析・バリデーション。
  - 互換モード時における `"INV-NOT-SET"` の廃止、製品関数による `MANUAL-<完全なUUID4>` の生成とメタデータ付与。
  - メール送信時の本文および Subject への同一 Invocation-ID の自動付与。
