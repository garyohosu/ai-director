それがいいね。orchestratorは120件のテストと実mail統合まで通っているので、いったん完成品として固定し、次はdirectorへ進もう。

アドバイスありがとう！
そして面白いのは、directorの開発そのものを既存システムで回せることだね。

ChatGPT：最初の要求と判断基準を作る
Claude Code：directorの設計・実装を指揮する
Codex：実装、テスト、レビューを担当する
mail：質問・回答・成果報告を配送する
orchestrator：必要なAIだけ起動する
開発中のdirector：完成した部分から判断処理に参加する

まさに「AIでAIの指揮官を作る」構成になる。

最初のdirectorは欲張らず、次の最小機能でよさそう。

director自身をmailへ登録する
director宛ての未読メールを受け取る
応答、QandA.md、Git差分、テスト結果を収集する
Codex CLIを指揮AIとして起動する
判断結果を構造化して受け取る
対象AIへ次の指示メールを送る
判断不能ならHUMAN_REQUIREDを人間用UIDへ送る
完了条件を満たしたらCOMPLETEDで終了する
判断回数と往復回数の上限を設ける
全判断をJSONLログへ残す

フォルダ構成は、これまでと同じコピー方式でいい。

project/
├─ mail/
├─ orchestrator/
├─ director/
│  ├─ SPEC.md
│  ├─ director.py
│  ├─ config.json
│  ├─ context_builder.py
│  ├─ decision.py
│  ├─ qanda.py
│  ├─ completion.py
│  ├─ adapters/
│  ├─ logs/
│  ├─ runtime/
│  └─ tests/
└─ QandA.md

最初に対応する判断は4種類だけで十分だと思う。

CONTINUE：同じAIへ続行指示
REVISE：修正指示
DELEGATE：別AIへ担当変更
HUMAN_REQUIRED：人間に判断を求める
COMPLETE：作業完了

実装前に、orchestratorの現在のコミットを完成版として記録しておこう。director側はorchestrator内部のクラスを直接importせず、mailと設定ファイルだけを境界にする。そうすればorchestratorを壊さず、directorだけ交換できる。

最初の実証課題を「directorが自分自身のSPEC.md作成をClaudeへ依頼し、Codexレビューの指摘に再指示を出す」にすると面白いね。成功すれば、人間がコピーするのは最初の要求と最後の結果だけになる。途中からは指揮官候補が、自分の設計作業を指揮し始める。少々SFじみてきたけれど、構成要素は全部すでに手元にあるよ。