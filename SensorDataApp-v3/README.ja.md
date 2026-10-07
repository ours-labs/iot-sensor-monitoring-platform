# SensorDataApp Ver3

[English](README.md) | 日本語

Ver3サーバー向けの、KotlinとJetpack ComposeによるAndroidクライアントです。現在値、アラート、分単位の時間帯平均、手動測定、限定的なデバイス管理を提供します。

本番用のホスト、利用者の対応付け、APIキーは収録していません。利用者の`~/.gradle/gradle.properties`にサーバーのホストを設定してください。

```properties
SENSOR_SERVER_HOST=host.example.invalid
```

設定がない場合は、アプリが`CFG-A001`を表示します。APIキーは配備環境の保護されたペアリング手順で取得し、ソースコードやビルド設定へ埋め込みません。

手動測定の要求は、サーバーが対応する`message_id`を確認するまで端末のキューに残ります。サーバーは、クライアント側で利用者が選んだ識別情報を受け入れるのではなく、ペアリング済み認証情報から操作担当者を特定します。

## ビルドとテスト

```bash
./gradlew testDebugUnitTest lintDebug assembleDebug --no-daemon
```
