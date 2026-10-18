# ブラウザー回帰テスト

Python 3、Python版Playwright、Chromiumを使用します。検証環境ではPython 3.12 / Playwright 1.62 / Chromium 154で実行しました。

```sh
python3 -m venv /tmp/ana-test-venv
/tmp/ana-test-venv/bin/pip install playwright==1.62.0
/tmp/ana-test-venv/bin/python -m playwright install chromium
/tmp/ana-test-venv/bin/python -m unittest discover -s tests -v
```

既に導入済みなら `python3 -m unittest discover -s tests -v` で実行できます。システムの `chromium` / `google-chrome` を優先し、なければPlaywrightのブラウザーを使います。`CHROMIUM_PATH` で実行ファイルを指定できます。

ブラウザー時刻を2026年10月18日に固定し、日付に依存する試算も再現可能にしています。サーバーは空いているループバックポートで自動起動し、テストごとに使い捨てのブラウザーコンテキストを作成します。既存ブラウザーのデータ、本番環境、実際のバックアップフォルダは操作しません。

対象は正常な記録・JSON出力、不正な取り込み、全件ロールバック、新規環境への復元と再取り込み、v2互換、複数タブ更新、試算の月境界、バックアップ通知、入力検証、グラフの目盛り数、キャッシュ分離とオフライン起動です。

Safari/iPhoneの共有シート、Macの実フォルダ保存・世代削除、OSによる強制終了、実際のストレージ容量不足、本番配信ヘッダーは別途実機で確認してください。
