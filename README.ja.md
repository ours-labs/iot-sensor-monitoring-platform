# IoT Sensor Monitoring Platform

[English](README.md) | 日本語

Raspberry Piから環境センサーデータを収集し、PostgreSQLへ確実に保存して、FlaskダッシュボードとAndroidアプリで表示する、運用を意識したVer3のポートフォリオプロジェクトです。

このリポジトリには、現在のVer3実装を収録しています。

## 構成

```text
Raspberry Pi sensors
  -> SQLite outbox
  -> versioned JSON over TCP
  -> Python ingestion server
  -> PostgreSQL
       -> Flask dashboard and API
       -> Android client
       -> monitoring and limited device control
```

配信プロトコルは`message_id`、`device_id`、`device_seq`を使い、少なくとも1回の配信、重複排除、順序の確認を行います。PostgreSQLのトランザクションがcommitされた後にだけ、成功のACKを返します。

## リポジトリ構成

- `ver3/`: Raspberry Piクライアント、受信サーバー、PostgreSQLマイグレーション、Flaskアプリ、監視、運用テンプレート、Pythonテスト。
- `SensorDataApp-v3/`: KotlinとJetpack ComposeによるAndroidクライアント。
- `.github/workflows/ver3-ci.yml`: 独立したPostgreSQL環境、Python、セキュリティ、AndroidのCI。

## 主な特徴

- 10種類のセンサー項目と、測定・受信・保存時刻
- Raspberry Pi側の永続的な送信待ち領域と再送
- 複数デバイスの識別とシーケンス追跡
- PostgreSQLを利用するAPIとブラウザダッシュボード
- 現在値、アラート、時間帯平均、手動測定を表示するAndroid画面
- 保守的なセンサー値の妥当性確認と、明示的な`NULL`処理
- APIキー、セッション、外部アクセス連携の境界
- CIでの85台の論理デバイスによる負荷確認

## 安全上の境界と導入の入口

本番用の認証情報、デバイストークン、非公開ホスト、センサーデータセット、生成済みAPKは収録していません。管理者が環境固有の設定をGitの外で用意するまでは、cloneしただけでは動作しない構成です。

[Ver3サーバーのガイド](ver3/README.md)、[Androidのガイド](SensorDataApp-v3/README.md)、[セキュリティ方針](SECURITY.md)を最初に参照してください。

現在のダッシュボードとAndroidの画面は主に日本語です。利用者が選択できる言語切替は計画段階です。プロトコルのフィールド、API識別子、設定キー、保存データは言語に依存しない形を維持します。

## 検証

```bash
cd ver3
python -m unittest discover -s . -p "test_*.py"
python tools/verify_ver3_boundary.py
python -m compileall -q .
python -m pip check
```

GitHub Actionsでは、PostgreSQLの結合・並行処理テスト、依存関係の監査、静的セキュリティ検査、Androidの単体テスト・lint・ビルドも実行します。

## ライセンス

プロジェクトで制作したコードと文書は[MIT License](LICENSE)で利用できます。第三者の依存関係にはそれぞれのライセンスが適用されます。[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)を参照してください。
