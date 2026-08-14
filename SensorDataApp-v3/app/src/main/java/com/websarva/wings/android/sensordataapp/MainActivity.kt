package com.websarva.wings.android.sensordataapp

import android.content.Context
import android.os.Build
import android.os.Bundle
import android.os.VibrationEffect
import android.os.Vibrator
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import com.websarva.wings.android.sensordataapp.ui.theme.SensorDataAppTheme
import kotlinx.coroutines.*
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

data class SensorRecord(
    val timestamp: String,
    val temp: String,
    val co2: String,
    val hum: String,
    val pressure: String,
    val light: String,
    val sound: String,
    val joyX: String,
    val joyY: String,
    val doorOpen: Int = 0,
    val trigger: String = "",
    val deviceId: String = "",
    val deviceName: String = "",
    val sourceType: String = "sensor",
)

// サーバー(app.py)が算出した不快指数(THI)・危険判定をブラウザ版と共通のロジックで受け取るための結果クラス
data class SensorApiResult(
    val records: List<SensorRecord>,
    val latestThi: Double?,
    val isDangerous: Boolean,
    val dangerReasons: List<String>
)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            SensorDataAppTheme(dynamicColor = false) {
                Surface(modifier = Modifier.fillMaxSize(), color = Color(0xFFF0F2F5)) {
                    SensorDataScreen()
                }
            }
        }
    }
}

@Composable
fun SensorDataScreen() {
    val context = LocalContext.current
    // SharedPreferencesのインスタンスを取得
    val sharedPref = context.getSharedPreferences("AppPrefs", Context.MODE_PRIVATE)

    // 実ホストはGitへ含めず、GradleプロパティSENSOR_SERVER_HOSTからビルド時に注入する。
    var serverIp by remember {
        mutableStateOf(sharedPref.getString("server_ip", BuildConfig.SENSOR_SERVER_HOST)
            ?: BuildConfig.SENSOR_SERVER_HOST)
    }

    if (serverIp.isBlank()) {
        ConfigurationErrorScreen(
            code = "CFG-A001",
            message = "SENSOR_SERVER_HOSTが設定されていません。管理者用ビルド設定を確認してください。",
        )
        return
    }

    // ペアリングで受け取った個別APIキー（未ペアリングならnull）。
    // 全員で1本の鍵を共有するBuildConfig.API_KEYはもう使わず、各自がペアリングで受け取った鍵を使う。
    var apiKey by remember { mutableStateOf(sharedPref.getString("api_key", null)) }

    // 未ペアリングの間は、通信を始めずペアリング画面だけを表示する
    if (apiKey.isNullOrBlank()) {
        PairingScreen(pairingUrl = "https://$serverIp/pair", onPaired = { pairedName, pairedKey ->
            sharedPref.edit()
                .putString("api_key", pairedKey)
                .putString("paired_name", pairedName)
                .apply()
            apiKey = pairedKey
        })
        return
    }
    // ここに到達する時点でペアリング済み（未ペアリングなら上のreturnで弾かれている）
    val pairedApiKey = apiKey!!
    var activeSection by remember { mutableStateOf(AppSection.LIVE) }
    if (activeSection == AppSection.AVERAGE) {
        AverageScreen(
            serverIp = serverIp,
            apiKey = pairedApiKey,
            activeSection = activeSection,
            onSectionSelected = { activeSection = it },
        )
        return
    }
    if (activeSection == AppSection.MANUAL) {
        ManualInputScreen(
            serverIp = serverIp,
            apiKey = pairedApiKey,
            activeSection = activeSection,
            onSectionSelected = { activeSection = it },
        )
        return
    }

    var statusText by remember { mutableStateOf("システム起動中...") }
    var sensorList by remember { mutableStateOf(listOf<SensorRecord>()) }

    // サーバー(app.py)側で算出した不快指数(THI)・危険判定。ブラウザ版と同じロジックを共有する。
    var latestThi by remember { mutableStateOf<Double?>(null) }
    var isDangerous by remember { mutableStateOf(false) }
    var dangerReasons by remember { mutableStateOf(listOf<String>()) }

    // 【機能2】スクロール位置を保持するためのステート
    val listState = rememberLazyListState()

    // 【機能1】バイブレーション用のコンテキストと重複防止用
    var lastAlertTimestamp by remember { mutableStateOf("") }

    // 5秒ごとの定期自動更新
    LaunchedEffect(Unit) {
        while (isActive) {
            try {
                val result = fetchSensorData(serverIp, pairedApiKey)
                sensorList = result.records
                latestThi = result.latestThi
                isDangerous = result.isDangerous
                dangerReasons = result.dangerReasons
                statusText = "リアルタイム同期中（全 ${result.records.size} 件表示）"
            } catch (e: Exception) {
                statusText = "同期エラー: ${e.message}"
            }
            delay(5000)
        }
    }

    // 【機能1】危険検知時の「スマホバイブレーション」ロジック（サーバー側の危険判定を使用）
    LaunchedEffect(isDangerous, sensorList) {
        val latestRecord = sensorList.firstOrNull()
        if (isDangerous && latestRecord != null && latestRecord.timestamp != lastAlertTimestamp) {
            lastAlertTimestamp = latestRecord.timestamp

            val vibrator = context.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
            if (vibrator != null && vibrator.hasVibrator()) {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    vibrator.vibrate(VibrationEffect.createOneShot(500, VibrationEffect.DEFAULT_AMPLITUDE))
                } else {
                    @Suppress("DEPRECATION")
                    vibrator.vibrate(500)
                }
            }
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(top = 50.dp, start = 12.dp, end = 12.dp, bottom = 12.dp)) {
        Text(
            text = "センサー環境モニター Ver.${BuildConfig.VERSION_NAME}",
            fontSize = 24.sp,
            fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.onSurface,
        )
        Text(
            text = "現在値と環境の変化をリアルタイムに確認",
            fontSize = 13.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 2.dp, bottom = 12.dp),
        )
        // 【追加】IPアドレス入力フィールド
        OutlinedTextField(
            value = serverIp,
            onValueChange = { newValue ->
                serverIp = newValue
                // 入力されるたびにSharedPreferencesに保存
                sharedPref.edit().putString("server_ip", newValue).apply()
            },
            label = { Text("サーバーホスト（管理者指定）") },
            modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
            singleLine = true
        )
        FeatureNavigation(
            activeSection = activeSection,
            onSectionSelected = { activeSection = it },
        )
        Spacer(modifier = Modifier.height(8.dp))
        Card(
            modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer)
        ) {
            Text(
                text = "● $statusText",
                fontSize = 15.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(16.dp),
                color = MaterialTheme.colorScheme.onPrimaryContainer
            )
        }

        // 【追加】現在の状況カード（不快指数・危険判定。ブラウザ版と同じサーバー算出値を表示）
        if (latestThi != null) {
            Card(
                modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
                colors = CardDefaults.cardColors(
                    containerColor = if (isDangerous) Color(0xFFB71C1C) else Color.White
                )
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(
                        text = "現在の状況",
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold,
                        color = if (isDangerous) Color.White else Color.Black
                    )
                    Text(
                        text = "不快指数（THI）: $latestThi",
                        fontSize = 14.sp,
                        color = if (isDangerous) Color.White else Color.Black,
                        modifier = Modifier.padding(top = 4.dp)
                    )
                    if (isDangerous) {
                        Text(
                            text = "⚠️ 危険区域：${dangerReasons.joinToString("、")}が異常範囲です。",
                            fontSize = 13.sp,
                            color = Color(0xFFFFCDD2),
                            modifier = Modifier.padding(top = 4.dp)
                        )
                    }
                }
            }
        }

        // --- リスト表示エリア ---
        // 【機能2】state = listState を指定してガタつきを防止
        LazyColumn(
            state = listState,
            modifier = Modifier.fillMaxSize()
        ) {
            items(sensorList) { record ->
                ExpandableSensorCard(record)
            }
        }
    }
}

@Composable
private fun ConfigurationErrorScreen(code: String, message: String) {
    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text("設定エラー [$code]", color = Color.Red, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(12.dp))
        Text(message)
    }
}

@Composable
fun ExpandableSensorCard(record: SensorRecord) {
    var expanded by remember { mutableStateOf(false) }

    // 【変更点】カードの色を変える判定にも「または0度以下（it <= 0.0f）」を追加
    val isTempDangerous = record.temp.toFloatOrNull()?.let { it >= 30.0f || it <= 0.0f } ?: false
    val isCo2Dangerous = record.co2.toIntOrNull()?.let { it >= 1200 } ?: false
    val isDangerous = isTempDangerous || isCo2Dangerous

    val cardBgColor = if (isDangerous) Color(0xFFB71C1C) else Color.White
    val mainTextColor = if (isDangerous) Color.White else Color.Black
    val subTextColor = if (isDangerous) Color(0xFFEEEEEE) else Color.DarkGray
    val tempColor = if (isDangerous) Color(0xFFFFCDD2) else Color(0xFFE53935)
    val co2Color = if (isDangerous) Color(0xFFFFCDD2) else Color(0xFF43A047)

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp)
            .clickable { expanded = !expanded },
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
        colors = CardDefaults.cardColors(containerColor = cardBgColor)
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text(
                text = record.deviceName.ifBlank { "名称未設定Pi" } +
                    if (record.sourceType == "manual") " · 手入力" else " · 実測",
                fontSize = 11.sp,
                color = subTextColor,
                modifier = Modifier.padding(bottom = 4.dp),
            )
            // ドア開放中の表示
            if (record.doorOpen == 1) {
                Text(text = "⚠️ ドア開放中", color = Color.Red, fontWeight = FontWeight.Bold, fontSize = 16.sp)
                Spacer(modifier = Modifier.height(4.dp))
            }

            // --- 常時表示エリア ---
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                val shortTime = record.timestamp.replace("2026-", "")
                Text(text = shortTime, fontSize = 14.sp, fontWeight = FontWeight.Bold, color = mainTextColor)
                Text(text = "${record.temp}℃", fontSize = 16.sp, color = tempColor, fontWeight = FontWeight.Bold)
                Text(text = "CO2: ${record.co2}", fontSize = 14.sp, color = co2Color, fontWeight = FontWeight.Bold)

                Icon(
                    imageVector = if (expanded) Icons.Default.KeyboardArrowUp else Icons.Default.KeyboardArrowDown,
                    contentDescription = "Expand",
                    tint = if (isDangerous) Color.White else Color.Gray
                )
            }

            // 取得トリガーの表示：このデータがボタン押下によるものか一目で分かる
            val triggerLabel = when (record.trigger) {
                "button" -> "🔘 ボタン取得"
                "timer", "" -> "⏱ 自動(タイマー)"
                else -> "🏷 ${record.trigger}"
            }
            val triggerColor = if (record.trigger == "button") {
                if (isDangerous) Color(0xFFFFCDD2) else Color(0xFF1565C0)
            } else subTextColor
            Text(
                text = triggerLabel,
                fontSize = 11.sp,
                fontWeight = if (record.trigger == "button") FontWeight.Bold else FontWeight.Normal,
                color = triggerColor,
                modifier = Modifier.padding(top = 2.dp)
            )

            if (isDangerous && !expanded) {
                Text("⚠️ 危険区域：環境値を確認してください", color = Color(0xFFFFCDD2), fontSize = 11.sp, modifier = Modifier.padding(top = 4.dp))
            }

            // --- 詳細エリア ---
            AnimatedVisibility(visible = expanded) {
                Column(modifier = Modifier.fillMaxWidth().padding(top = 12.dp)) {
                    HorizontalDivider(color = if (isDangerous) Color(0xFFD32F2F) else Color(0xFFEEEEEE), thickness = 1.dp)
                    Spacer(modifier = Modifier.height(8.dp))

                    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        Text(text = "湿度: ${record.hum} %", fontSize = 14.sp, color = subTextColor)
                        Text(text = "気圧: ${record.pressure} hPa", fontSize = 14.sp, color = subTextColor)
                    }
                    Spacer(modifier = Modifier.height(4.dp))
                    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        Text(text = "明るさ: ${record.light}", fontSize = 14.sp, color = subTextColor)
                        Text(text = "音量: ${record.sound}", fontSize = 14.sp, color = subTextColor)
                    }
                    Spacer(modifier = Modifier.height(4.dp))
                    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        Text(text = "ジョイスティック X: ${record.joyX}", fontSize = 14.sp, color = subTextColor)
                        Text(text = "Y: ${record.joyY}", fontSize = 14.sp, color = subTextColor)
                    }
                }
            }
        }
    }
}

suspend fun fetchSensorData(serverIp: String, apiKey: String): SensorApiResult {
    return withContext(Dispatchers.IO) {
        val urlString = "https://$serverIp/api/v3/readings?format=json"
        val url = URL(urlString)
        val connection = url.openConnection() as HttpURLConnection
        connection.requestMethod = "GET"
        connection.setRequestProperty("X-API-Key", apiKey)
        connection.connectTimeout = 4000
        connection.readTimeout = 4000

        if (connection.responseCode == HttpURLConnection.HTTP_OK) {
            val jsonString = connection.inputStream.bufferedReader().use { it.readText() }
            val jsonObject = parseApiJsonObject(jsonString)
            val sensorData = jsonObject.getJSONObject("sensor_data")

            val timestamps = sensorData.getJSONArray("timestamp")
            val temps = sensorData.getJSONArray("temp")
            val co2s = sensorData.getJSONArray("co2")
            val hums = sensorData.getJSONArray("hum")
            val pressures = sensorData.getJSONArray("pressure")
            val lights = sensorData.getJSONArray("light_raw")
            val sounds = sensorData.getJSONArray("sound_raw")
            val joyXs = sensorData.optJSONArray("joystick_x")
            val joyYs = sensorData.optJSONArray("joystick_y")
            val doorOpens = sensorData.optJSONArray("door_open")
            val deviceIds = sensorData.optJSONArray("device_id")
            val deviceNames = sensorData.optJSONArray("display_name")
            val sourceTypes = sensorData.optJSONArray("source_type")
            // trigger は sensor_data ではなく JSON 直下の配列（button/timer/固有番号）
            val triggers = jsonObject.optJSONArray("trigger")

            val list = mutableListOf<SensorRecord>()
            val totalCount = timestamps.length()

            // メモリ負荷と描画処理の最適化のため、最新の100件に絞り込みます
            val startIdx = maxOf(0, totalCount - 100)

            for (i in startIdx until totalCount) {
                list.add(
                    SensorRecord(
                        timestamp = timestamps.optString(i, "N/A"),
                        temp = temps.optString(i, "N/A"),
                        co2 = co2s.optString(i, "N/A"),
                        hum = hums.optString(i, "N/A"),
                        pressure = pressures.optString(i, "N/A"),
                        light = lights.optString(i, "N/A"),
                        sound = sounds.optString(i, "N/A"),
                        joyX = joyXs?.optString(i, "N/A") ?: "N/A",
                        joyY = joyYs?.optString(i, "N/A") ?: "N/A",
                        doorOpen = doorOpens?.optInt(i, 0) ?: 0,
                        trigger = triggers?.optString(i, "") ?: "",
                        deviceId = deviceIds?.optString(i, "") ?: "",
                        deviceName = deviceNames?.optString(i, "") ?: "",
                        sourceType = sourceTypes?.optString(i, "sensor") ?: "sensor",
                    )
                )
            }

            val latestThi = if (jsonObject.has("latest_thi") && !jsonObject.isNull("latest_thi")) {
                jsonObject.getDouble("latest_thi")
            } else null
            val isDangerous = jsonObject.optBoolean("is_dangerous", false)
            val dangerReasonsArray = jsonObject.optJSONArray("danger_reasons")
            val dangerReasons = mutableListOf<String>()
            if (dangerReasonsArray != null) {
                for (i in 0 until dangerReasonsArray.length()) {
                    dangerReasons.add(dangerReasonsArray.optString(i))
                }
            }

            return@withContext SensorApiResult(
                records = list.reversed(),
                latestThi = latestThi,
                isDangerous = isDangerous,
                dangerReasons = dangerReasons
            )
        } else {
            throw Exception("HTTP ${connection.responseCode}")
        }
    }
}

// 初回起動時のペアリング画面。
// Cloudflare Access(One-time PIN)で本物のメールアドレス所有者であることを確認した上で、
// 個別のAPIキーを1回だけ受け取る。全員で1本の鍵を共有せず、ビルドも1つで済むようにする仕組み。
@Composable
fun PairingScreen(pairingUrl: String, onPaired: (name: String, apiKey: String) -> Unit) {
    var showWebView by remember { mutableStateOf(false) }
    var errorText by remember { mutableStateOf<String?>(null) }

    if (!showWebView) {
        Column(
            modifier = Modifier.fillMaxSize().padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Spacer(modifier = Modifier.height(80.dp))
            Text("初回ペアリングが必要です", fontSize = 20.sp, fontWeight = FontWeight.Bold)
            Spacer(modifier = Modifier.height(12.dp))
            Text(
                "次の画面でご自身のメールアドレスを入力し、届いたコードで認証すると、" +
                    "あなた専用のAPIキーが自動的に設定されます。",
                fontSize = 14.sp,
                color = Color.DarkGray
            )
            if (errorText != null) {
                Spacer(modifier = Modifier.height(12.dp))
                Text("エラー: $errorText", color = Color.Red, fontSize = 13.sp)
            }
            Spacer(modifier = Modifier.height(24.dp))
            Button(onClick = {
                errorText = null
                showWebView = true
            }) {
                Text("ペアリングを開始")
            }
        }
        return
    }

    AndroidView(
        modifier = Modifier.fillMaxSize(),
        factory = { context ->
            WebView(context).apply {
                settings.javaScriptEnabled = true
                // CloudflareのログインページはlocalStorage等のDOM Storageに依存しているため、
                // 既定でOffになっているこれを明示的に有効化する（Offのままだと画面が
                // 読み込み途中で止まって見える不具合の原因になっていた）
                settings.domStorageEnabled = true
                webViewClient = object : WebViewClient() {
                    override fun onPageFinished(view: WebView, url: String?) {
                        super.onPageFinished(view, url)
                        // ペアリングURL(/pair)に到達した時だけ、本文をJSONとして解析を試みる。
                        // Cloudflare Accessのログイン画面表示中はJSONでないため、解析失敗は無視して待つ。
                        if (url == null || !url.startsWith(pairingUrl)) return

                        view.evaluateJavascript("document.body.innerText") { rawResult ->
                            try {
                                // evaluateJavascriptの戻り値はJSON文字列としてエスケープされているため、
                                // 前後のダブルクォートを外し、エスケープを1回分だけ戻す。
                                val bodyText = JSONObject.wrap(rawResult)?.toString()
                                    ?.let { org.json.JSONTokener(it).nextValue() as? String }
                                    ?: rawResult.trim('"').replace("\\\"", "\"").replace("\\n", "\n")
                                val json = JSONObject(bodyText)
                                if (json.optString("status") == "success") {
                                    val name = json.getString("name")
                                    val apiKey = json.getString("api_key")
                                    onPaired(name, apiKey)
                                } else {
                                    errorText = json.optString("message", "ペアリングに失敗しました")
                                    showWebView = false
                                }
                            } catch (_: Exception) {
                                // ログイン途中のページ（Cloudflare Accessの画面等）。まだ何もしない。
                            }
                        }
                    }
                }
                loadUrl(pairingUrl)
            }
        }
    )
}
