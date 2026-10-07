# Sensor Monitoring Ver3

[English](README.md) | 日本語

このディレクトリには、Ver3のRaspberry Piクライアント、TCP受信サーバー、PostgreSQLによる永続化層、Flaskダッシュボード、監視処理、配備テンプレートを収録しています。

Ver3のシステムバージョンは`3.0.0`、プロトコルバージョンは`3`、データベーススキーマバージョンは`3`です。不一致がある場合は処理を拒否します。対応するハードウェア経路はPi4gpioバックエンドです。直接アクセスのバックエンドは、明示的なフォールバックとして残しています。

## 基本動作

- サーバー側のデータ正本はPostgreSQLです。
- `message_id`とDB commit後のACKにより、重複排除を伴う少なくとも1回の配信を行います。
- 各デバイスは、不変の`device_id`、変更可能な表示名、申告した機能、永続的な`device_seq`を持ちます。
- Raspberry Piは未送信メッセージをSQLiteの送信待ち領域へ保存し、安全に再送します。
- 測定・受信・保存の時刻を別々に記録します。
- ブラウザとAndroidクライアントから、測定値と時間帯平均を取得できます。
- 手動測定は操作担当者の帰属を保持し、物理的な値の範囲を検証し、欠測値を`NULL`として保存します。
- 不自然な値、欠測、通信の途切れ、シーケンスの異常を監視します。

## データの流れ

```text
sensor_client_tiered.py + SQLite outbox
  -> protocol_version=3 JSON
sensor_server.py
  -> PostgreSQL commit
  <- inserted / duplicate / rejected / retry ACK
Flask application and Android client
monitor.py
  -> per-device quality and availability checks
```

## 設定

実際のアドレス、ドメイン、利用者識別情報、APIキー、トークン、ローカルのホームパスは収録していません。新しくcloneした状態では、設定が用意されるまで起動を拒否する構成です。

1. `env/*.env.example`と`web-app/.env.example`をテンプレートとして使用します。
2. 実際の値はGitの外へ保存します。配備先ごとの環境設定ファイルを推奨します。
3. PostgreSQLのマイグレーションを適用し、管理ツールでデバイスと操作担当者を登録します。
4. 汎用サービステンプレートを配備環境に合わせて調整します。
5. Androidの接続先ホストは利用者のGradleプロパティへ設定し、commitするソースには記載しません。

安定した設定・実行時エラーコードは[ERROR_CODES.md](ERROR_CODES.md)、共有インターフェースは[PUBLIC_CONTRACT.md](PUBLIC_CONTRACT.md)、データベースの手順は[POSTGRES_OPERATIONS.md](POSTGRES_OPERATIONS.md)を参照してください。

## 限定的な管理機能

共有の管理用パスワードは、外部の環境設定ファイルへハッシュとしてのみ保存します。権限を昇格したセッションには、無操作による有効期限と絶対的な存続時間があります。再起動とシャットダウンには、再認証と明示的な確認が必要です。

デバイス制御エージェントは、要求IDとイベントをSQLiteへ永続化します。`CONTROL_DRY_RUN=true`が安全側の既定値です。実際の電源操作には、OS側の明示的な許可リストと配備の承認が必要です。

## Pi4gpioバックエンド

`RPI_SENSOR_BACKEND=direct`または`RPI_SENSOR_BACKEND=pi4gpio`を設定します。Pi4gpioデーモンはGPIO、I2C、SPI、UARTのリソースを所有し、クライアントへUnixソケットを提供します。バックエンドの変更を受け入れる前に、キューからの配信、ACK、ソケットの利用可能性、プロセスの所有権、センサー値の妥当性を確認してください。

## テスト

プロジェクト専用の仮想環境を作成・使用し、次を実行します。

```bash
python -m unittest discover -s . -p "test_*.py"
python tools/verify_ver3_boundary.py
python -m compileall -q .
python -m pip check
```

PostgreSQLの結合テストには`TEST_DATABASE_URL`が必要です。CIでは、一時的なPostgreSQLサービスを自動的に用意します。
