# SensorDataApp Ver3

This Kotlin and Jetpack Compose Android client is designed for the Ver3 server. It provides current readings, alert views, minute-based time-window averages, manual readings, and limited device administration.

Production hosts, user mappings, and API keys are intentionally absent. Configure the server host in the user's `~/.gradle/gradle.properties` file:

```properties
SENSOR_SERVER_HOST=host.example.invalid
```

If the setting is missing, the application reports `CFG-A001`. API keys are obtained through the deployment's protected pairing flow and are not embedded in source code or build settings.

Manual-reading requests remain queued on the device until the server confirms the associated `message_id`. The server derives the operator identity from the paired credential instead of accepting a user-selected identity from the client.

## Build and test

```bash
./gradlew testDebugUnitTest lintDebug assembleDebug --no-daemon
```
