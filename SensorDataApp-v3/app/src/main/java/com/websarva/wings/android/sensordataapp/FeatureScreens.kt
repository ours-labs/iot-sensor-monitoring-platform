package com.websarva.wings.android.sensordataapp

import android.app.DatePickerDialog
import android.app.TimePickerDialog
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale
import java.util.UUID

enum class AppSection(val label: String) {
    LIVE("現在"), AVERAGE("平均"), MANUAL("手入力")
}

data class SensorDefinition(
    val key: String,
    val label: String,
    val unit: String,
    val min: Double,
    val max: Double,
)

data class AverageReading(
    val key: String,
    val label: String,
    val average: Double?,
    val count: Int,
)

data class DeviceChoice(val deviceId: String, val displayName: String)

internal fun deviceChoiceLabel(device: DeviceChoice, devices: List<DeviceChoice>): String {
    val hasDuplicateName = devices.count { it.displayName == device.displayName } > 1
    return if (hasDuplicateName) {
        "${device.displayName}（${device.deviceId.take(8)}…）"
    } else {
        device.displayName
    }
}

data class ManualSaveResult(val status: String, val messageId: String, val errorCode: String?)

private val sensorDefinitions = listOf(
    SensorDefinition("light_raw", "照度", "raw", 0.0, 4095.0),
    SensorDefinition("light_voltage", "照度電圧", "V", 0.0, 3.3),
    SensorDefinition("sound_raw", "音", "raw", 0.0, 4095.0),
    SensorDefinition("joystick_x", "ジョイスティック X", "", -1.0, 1.0),
    SensorDefinition("joystick_y", "ジョイスティック Y", "", -1.0, 1.0),
    SensorDefinition("potentiometer_percent", "可変抵抗", "%", 0.0, 100.0),
    SensorDefinition("temp", "温度", "℃", -40.0, 80.0),
    SensorDefinition("hum", "湿度", "%", 0.0, 100.0),
    SensorDefinition("pressure", "気圧", "hPa", 300.0, 1100.0),
    SensorDefinition("co2", "CO₂", "ppm", 400.0, 5000.0),
)

@Composable
fun FeatureNavigation(activeSection: AppSection, onSectionSelected: (AppSection) -> Unit) {
    TabRow(
        selectedTabIndex = activeSection.ordinal,
        containerColor = Color.White,
        contentColor = MaterialTheme.colorScheme.primary,
    ) {
        AppSection.entries.forEach { section ->
            Tab(
                selected = section == activeSection,
                onClick = { onSectionSelected(section) },
                text = { Text(section.label, fontWeight = FontWeight.Bold) },
            )
        }
    }
}

@Composable
private fun FeatureHeader(title: String, subtitle: String) {
    Text(title, fontSize = 24.sp, fontWeight = FontWeight.Bold)
    Text(
        subtitle,
        fontSize = 13.sp,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = 2.dp, bottom = 12.dp),
    )
}

private const val DATE_TIME_PATTERN = "yyyy-MM-dd'T'HH:mm"

private fun formatDateTime(calendar: Calendar): String =
    SimpleDateFormat(DATE_TIME_PATTERN, Locale.US).format(calendar.time)

private fun parseDateTime(value: String): Calendar? {
    if (value.isBlank()) return null
    val formatter = SimpleDateFormat(DATE_TIME_PATTERN, Locale.US).apply { isLenient = false }
    val parsed = runCatching { formatter.parse(value) }.getOrNull() ?: return null
    return Calendar.getInstance().apply { time = parsed }
}

@Composable
private fun DateTimeSelector(
    label: String,
    value: String,
    onValueChange: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    Column(modifier) {
        Text(label, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        OutlinedButton(
            onClick = {
                val initial = parseDateTime(value) ?: Calendar.getInstance()
                DatePickerDialog(
                    context,
                    { _, year, month, day ->
                        TimePickerDialog(
                            context,
                            { _, hour, minute ->
                                val selected = Calendar.getInstance().apply {
                                    set(year, month, day, hour, minute, 0)
                                    set(Calendar.MILLISECOND, 0)
                                }
                                onValueChange(formatDateTime(selected))
                            },
                            initial.get(Calendar.HOUR_OF_DAY),
                            initial.get(Calendar.MINUTE),
                            true,
                        ).show()
                    },
                    initial.get(Calendar.YEAR),
                    initial.get(Calendar.MONTH),
                    initial.get(Calendar.DAY_OF_MONTH),
                ).show()
            },
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(if (value.isBlank()) "未指定" else value.replace('T', ' '))
        }
    }
}

@Composable
fun AverageScreen(
    serverIp: String,
    apiKey: String,
    activeSection: AppSection,
    onSectionSelected: (AppSection) -> Unit,
) {
    val scope = rememberCoroutineScope()
    var startDate by remember { mutableStateOf("") }
    var endDate by remember { mutableStateOf("") }
    var includeManual by remember { mutableStateOf(false) }
    var devices by remember { mutableStateOf<List<DeviceChoice>>(emptyList()) }
    var selectedDeviceId by remember { mutableStateOf("") }
    var deviceMenuExpanded by remember { mutableStateOf(false) }
    val selected = remember { mutableStateMapOf<String, Boolean>() }
    sensorDefinitions.forEach { sensor ->
        if (sensor.key !in selected) selected[sensor.key] = true
    }
    var results by remember { mutableStateOf<List<AverageReading>>(emptyList()) }
    var message by remember { mutableStateOf("条件を選び、「平均値を計算」を押してください。") }
    var loading by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        runCatching { fetchDevices(serverIp, apiKey) }
            .onSuccess { devices = it }
            .onFailure { message = "Pi一覧の取得に失敗しました: ${it.message}" }
    }

    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(top = 50.dp, start = 12.dp, end = 12.dp, bottom = 12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        item {
            FeatureHeader("期間平均", "実測データを基本に、必要なときだけ手入力を含めます")
            FeatureNavigation(activeSection, onSectionSelected)
            Spacer(Modifier.height(8.dp))
        }
        item {
            Card(colors = CardDefaults.cardColors(containerColor = Color.White)) {
                Column(Modifier.padding(16.dp)) {
                    Text("集計条件", fontWeight = FontWeight.Bold)
                    Text("対象Pi", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Box(Modifier.fillMaxWidth()) {
                        OutlinedButton(
                            onClick = { deviceMenuExpanded = true },
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Text(devices.firstOrNull { it.deviceId == selectedDeviceId }?.displayName ?: "全Pi")
                        }
                        DropdownMenu(
                            expanded = deviceMenuExpanded,
                            onDismissRequest = { deviceMenuExpanded = false },
                        ) {
                            DropdownMenuItem(
                                text = { Text("全Pi") },
                                onClick = { selectedDeviceId = ""; deviceMenuExpanded = false },
                            )
                            devices.forEach { device ->
                                DropdownMenuItem(
                                    text = { Text(deviceChoiceLabel(device, devices)) },
                                    onClick = { selectedDeviceId = device.deviceId; deviceMenuExpanded = false },
                                )
                            }
                        }
                    }
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        DateTimeSelector(
                            label = "開始日時",
                            value = startDate,
                            onValueChange = { startDate = it },
                            modifier = Modifier.weight(1f),
                        )
                        DateTimeSelector(
                            label = "終了日時",
                            value = endDate,
                            onValueChange = { endDate = it },
                            modifier = Modifier.weight(1f),
                        )
                    }
                    Text(
                        "終了日時は選択した分の末尾まで含みます",
                        fontSize = 12.sp,
                        color = Color.DarkGray,
                        modifier = Modifier.padding(top = 6.dp),
                    )
                    Row(
                        Modifier.fillMaxWidth().padding(top = 8.dp),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        OutlinedButton(
                            onClick = {
                                val end = Calendar.getInstance().apply { set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0) }
                                val start = end.clone() as Calendar
                                start.add(Calendar.HOUR_OF_DAY, -1)
                                startDate = formatDateTime(start)
                                endDate = formatDateTime(end)
                            },
                            modifier = Modifier.weight(1f),
                        ) { Text("直近1時間", fontSize = 12.sp) }
                        OutlinedButton(
                            onClick = {
                                val end = Calendar.getInstance().apply { set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0) }
                                val start = end.clone() as Calendar
                                start.set(Calendar.HOUR_OF_DAY, 0)
                                start.set(Calendar.MINUTE, 0)
                                startDate = formatDateTime(start)
                                endDate = formatDateTime(end)
                            },
                            modifier = Modifier.weight(1f),
                        ) { Text("今日", fontSize = 12.sp) }
                        OutlinedButton(
                            onClick = { startDate = ""; endDate = "" },
                            modifier = Modifier.weight(1f),
                        ) { Text("全期間", fontSize = 12.sp) }
                    }
                    Row(
                        Modifier.fillMaxWidth().padding(top = 8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text("手入力データを含める", fontWeight = FontWeight.SemiBold)
                            Text("OFFならtimer/buttonのみ", fontSize = 12.sp, color = Color.DarkGray)
                        }
                        Switch(checked = includeManual, onCheckedChange = { includeManual = it })
                    }
                }
            }
        }
        item {
            Card(colors = CardDefaults.cardColors(containerColor = Color.White)) {
                Column(Modifier.padding(16.dp)) {
                    Text("センサー", fontWeight = FontWeight.Bold)
                    sensorDefinitions.chunked(2).forEach { pair ->
                        Row(Modifier.fillMaxWidth()) {
                            pair.forEach { sensor ->
                                Row(
                                    modifier = Modifier.weight(1f),
                                    verticalAlignment = Alignment.CenterVertically,
                                ) {
                                    Checkbox(
                                        checked = selected[sensor.key] == true,
                                        onCheckedChange = { selected[sensor.key] = it },
                                    )
                                    Text(sensor.label, fontSize = 13.sp)
                                }
                            }
                            if (pair.size == 1) Spacer(Modifier.weight(1f))
                        }
                    }
                    Button(
                        onClick = {
                            val sensors = sensorDefinitions.filter { selected[it.key] == true }.map { it.key }
                            if (sensors.isEmpty()) {
                                message = "センサーを1つ以上選択してください。"
                            } else if ((startDate.isNotBlank() && parseDateTime(startDate) == null) ||
                                (endDate.isNotBlank() && parseDateTime(endDate) == null)) {
                                message = "日時をもう一度選択してください。"
                            } else if (startDate.isNotBlank() && endDate.isNotBlank() && startDate > endDate) {
                                message = "開始日時は終了日時以前にしてください。"
                            } else {
                                loading = true
                                message = "集計中..."
                                scope.launch {
                                    try {
                                        results = fetchAverageData(
                                            serverIp, apiKey, startDate, endDate, sensors, includeManual,
                                            selectedDeviceId)
                                        message = if (results.any { it.count > 0 }) {
                                            if (includeManual) "実測＋手入力の平均です。" else "実測データのみの平均です。"
                                        } else "平均を計算できるデータがありません。"
                                    } catch (e: Exception) {
                                        message = "集計エラー: ${e.message}"
                                    } finally {
                                        loading = false
                                    }
                                }
                            }
                        },
                        enabled = !loading,
                        modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
                    ) {
                        if (loading) CircularProgressIndicator(Modifier.height(18.dp), strokeWidth = 2.dp)
                        else Text("平均値を計算")
                    }
                }
            }
        }
        item {
            Text(message, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        items(results) { result ->
            Card(colors = CardDefaults.cardColors(containerColor = Color.White)) {
                Row(
                    Modifier.fillMaxWidth().padding(16.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column {
                        Text(result.label, fontWeight = FontWeight.Bold)
                        Text("有効データ ${result.count} 件", fontSize = 12.sp, color = Color.DarkGray)
                    }
                    Text(
                        result.average?.toString() ?: "Null",
                        fontSize = 22.sp,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.primary,
                    )
                }
            }
        }
    }
}

@Composable
fun ManualInputScreen(
    serverIp: String,
    apiKey: String,
    activeSection: AppSection,
    onSectionSelected: (AppSection) -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val pendingStore = remember { context.getSharedPreferences("Ver3ManualQueue", android.content.Context.MODE_PRIVATE) }
    val values = remember { mutableStateMapOf<String, String>() }
    sensorDefinitions.forEach { sensor -> if (sensor.key !in values) values[sensor.key] = "" }
    var devices by remember { mutableStateOf<List<DeviceChoice>>(emptyList()) }
    var selectedDeviceId by remember { mutableStateOf("") }
    var menuExpanded by remember { mutableStateOf(false) }
    var displayNameDraft by remember { mutableStateOf("") }
    var renaming by remember { mutableStateOf(false) }
    var measuredAt by remember { mutableStateOf("") }
    var saving by remember { mutableStateOf(false) }
    var resultMessage by remember { mutableStateOf<String?>(null) }
    var resultIsDanger by remember { mutableStateOf(false) }
    var dangerConfirmed by remember { mutableStateOf(false) }
    var currentMessageId by remember { mutableStateOf(UUID.randomUUID().toString()) }
    val dangerPreview = manualDangerReasons(values)

    LaunchedEffect(Unit) {
        try {
            devices = fetchDevices(serverIp, apiKey)
            if (selectedDeviceId.isBlank() && devices.isNotEmpty()) {
                selectedDeviceId = devices.first().deviceId
                displayNameDraft = devices.first().displayName
            }
        } catch (e: Exception) {
            resultMessage = "Pi一覧の取得に失敗しました: ${e.message}"
        }
        if (pendingStore.contains("payload")) {
            resultMessage = "前回の保存結果を確認できていません。同じmessage_idで再確認できます。"
        }
    }

    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(top = 50.dp, start = 12.dp, end = 12.dp, bottom = 12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        item {
            FeatureHeader("手入力データ Ver3", "対象Piを指定し、PostgreSQLのACKまで確認します")
            FeatureNavigation(activeSection, onSectionSelected)
            Spacer(Modifier.height(8.dp))
        }
        item {
            Card(colors = CardDefaults.cardColors(containerColor = Color(0xFFEADDFF))) {
                Text(
                    "入力者はAPIキーに紐付いた学籍番号をサーバーが決定します。空欄はNullです。",
                    modifier = Modifier.padding(16.dp),
                    color = Color(0xFF21005D),
                    fontSize = 13.sp,
                )
            }
        }
        item {
            Card(colors = CardDefaults.cardColors(containerColor = Color.White)) {
                Column(Modifier.padding(16.dp)) {
                    Text("所属Pi", fontWeight = FontWeight.Bold)
                    Box(Modifier.fillMaxWidth().padding(top = 8.dp)) {
                        OutlinedButton(
                            onClick = { menuExpanded = true },
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Text(devices.firstOrNull { it.deviceId == selectedDeviceId }?.displayName ?: "選択してください")
                        }
                        DropdownMenu(
                            expanded = menuExpanded,
                            onDismissRequest = { menuExpanded = false },
                        ) {
                            devices.forEach { device ->
                                DropdownMenuItem(
                                    text = { Text(deviceChoiceLabel(device, devices)) },
                                    onClick = {
                                        selectedDeviceId = device.deviceId
                                        displayNameDraft = device.displayName
                                        menuExpanded = false
                                    },
                                )
                            }
                        }
                    }
                }
            }
        }
        item {
            Card(colors = CardDefaults.cardColors(containerColor = Color.White)) {
                Column(Modifier.padding(16.dp)) {
                    Text("Piの表示名", fontWeight = FontWeight.Bold)
                    Text(
                        "人向けの名前だけを変更します。device_idは変わりません。",
                        fontSize = 12.sp,
                        color = Color.DarkGray,
                    )
                    OutlinedTextField(
                        value = displayNameDraft,
                        onValueChange = { if (it.length <= 80) displayNameDraft = it },
                        label = { Text("表示名") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
                    )
                    OutlinedButton(
                        onClick = {
                            if (selectedDeviceId.isBlank() || displayNameDraft.isBlank()) {
                                resultMessage = "対象Piと1〜80文字の表示名を指定してください。"
                            } else {
                                renaming = true
                                scope.launch {
                                    try {
                                        renameDevice(
                                            serverIp, apiKey, selectedDeviceId,
                                            displayNameDraft.trim(),
                                        )
                                        devices = fetchDevices(serverIp, apiKey)
                                        resultMessage = "Piの表示名を更新しました。"
                                    } catch (e: Exception) {
                                        resultMessage = "表示名を更新できません: ${e.message}"
                                    } finally {
                                        renaming = false
                                    }
                                }
                            }
                        },
                        enabled = !renaming && !saving,
                        modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
                    ) {
                        if (renaming) {
                            CircularProgressIndicator(Modifier.height(18.dp), strokeWidth = 2.dp)
                        } else {
                            Text("表示名を変更")
                        }
                    }
                }
            }
        }
        item {
            Card(colors = CardDefaults.cardColors(containerColor = Color.White)) {
                DateTimeSelector(
                    label = "計測時刻（未指定ならサーバー受信時刻）",
                    value = measuredAt,
                    onValueChange = { measuredAt = it },
                    modifier = Modifier.fillMaxWidth().padding(16.dp),
                )
            }
        }
        items(sensorDefinitions) { sensor ->
            Card(colors = CardDefaults.cardColors(containerColor = Color.White)) {
                OutlinedTextField(
                    value = values[sensor.key].orEmpty(),
                    onValueChange = { values[sensor.key] = it },
                    label = { Text("${sensor.label}${if (sensor.unit.isBlank()) "" else "（${sensor.unit}）"}") },
                    supportingText = { Text("入力可能範囲 ${sensor.min.toPlain()}〜${sensor.max.toPlain()} / 空欄はNull") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth().padding(12.dp),
                )
            }
        }
        if (dangerPreview.isNotEmpty()) {
            item {
                Card(colors = CardDefaults.cardColors(containerColor = Color(0xFFFFDAD6))) {
                    Column(Modifier.padding(16.dp)) {
                        Text(
                            "⚠️ 危険値: ${dangerPreview.joinToString("、")}。確認した場合だけ保存できます。",
                            color = Color(0xFF93000A), fontWeight = FontWeight.Bold,
                        )
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Checkbox(checked = dangerConfirmed, onCheckedChange = { dangerConfirmed = it })
                            Text("危険値であることを確認して保存する")
                        }
                    }
                }
            }
        }
        if (resultMessage != null) {
            item {
                Card(
                    colors = CardDefaults.cardColors(
                        containerColor = if (resultIsDanger) Color(0xFFFFDAD6) else Color(0xFFC4EED0))
                ) {
                    Text(
                        resultMessage!!,
                        modifier = Modifier.padding(16.dp),
                        color = if (resultIsDanger) Color(0xFF93000A) else Color(0xFF123F23),
                        fontWeight = FontWeight.Bold,
                    )
                }
            }
        }
        item {
            Button(
                onClick = {
                    val validation = validateManualValues(values)
                    when {
                        selectedDeviceId.isBlank() -> resultMessage = "所属Piを選択してください。"
                        validation != null -> resultMessage = validation
                        dangerPreview.isNotEmpty() && !dangerConfirmed -> resultMessage = "危険値の確認欄を選択してください。"
                        else -> {
                            saving = true
                            resultMessage = "PostgreSQLへの保存確認中…"
                            resultIsDanger = false
                            scope.launch {
                                try {
                                    val existing = pendingStore.getString("payload", null)
                                    val payload = existing?.let(::JSONObject) ?: buildManualPayload(
                                        currentMessageId, selectedDeviceId, measuredAt,
                                        values, dangerConfirmed,
                                    ).also { pendingStore.edit().putString("payload", it.toString()).apply() }
                                    val saved = postManualReading(serverIp, apiKey, payload)
                                    if (saved.status == "inserted" || saved.status == "duplicate") {
                                        pendingStore.edit().remove("payload").apply()
                                        resultIsDanger = dangerPreview.isNotEmpty()
                                        resultMessage = "DB保存確認: ${saved.status} / message_id=${saved.messageId.take(8)}…"
                                        values.keys.forEach { values[it] = "" }
                                        measuredAt = ""
                                        dangerConfirmed = false
                                        currentMessageId = UUID.randomUUID().toString()
                                    } else if (saved.status == "rejected") {
                                        pendingStore.edit().remove("payload").apply()
                                        resultMessage = "保存拒否: ${saved.errorCode ?: "理由不明"}"
                                    } else {
                                        resultMessage = "保存結果を確認できません。同じmessage_idで再確認します。"
                                    }
                                } catch (e: Exception) {
                                    resultMessage = "保存結果を確認できません: ${e.message}"
                                } finally {
                                    saving = false
                                }
                            }
                        }
                    }
                },
                enabled = !saving,
                modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp),
            ) {
                if (saving) CircularProgressIndicator(Modifier.height(18.dp), strokeWidth = 2.dp)
                else Text(if (pendingStore.contains("payload")) "未確認データを再確認" else "PostgreSQLへ保存")
            }
        }
    }
}

private fun validateManualValues(values: Map<String, String>): String? {
    var hasValue = false
    sensorDefinitions.forEach { sensor ->
        val raw = values[sensor.key].orEmpty().trim()
        if (raw.isBlank()) return@forEach
        hasValue = true
        val value = raw.toDoubleOrNull()
            ?: return "${sensor.label}は数値で入力してください。"
        if (!value.isFinite() || value < sensor.min || value > sensor.max) {
            return "${sensor.label}は物理範囲 ${sensor.min.toPlain()}〜${sensor.max.toPlain()} で入力してください。"
        }
    }
    return if (hasValue) null else "少なくとも1つのセンサー値を入力してください。"
}

private fun manualDangerReasons(values: Map<String, String>): List<String> {
    val result = mutableListOf<String>()
    values["temp"]?.toDoubleOrNull()?.let { if (it >= 30 || it <= 0) result += "温度" }
    values["co2"]?.toDoubleOrNull()?.let { if (it >= 1200) result += "CO₂" }
    values["pressure"]?.toDoubleOrNull()?.let { if (it > 0 && it < 990) result += "気圧" }
    values["sound_raw"]?.toDoubleOrNull()?.let { if (it >= 3000) result += "音" }
    values["light_raw"]?.toDoubleOrNull()?.let { if (it < 100 || it > 3900) result += "照度" }
    return result
}

private fun Double.toPlain(): String = if (this % 1.0 == 0.0) toInt().toString() else toString()

internal fun isJsonObjectBody(body: String): Boolean = body.trimStart().startsWith("{")

internal fun parseApiJsonObject(body: String): JSONObject {
    if (!isJsonObjectBody(body)) {
        throw IllegalStateException(
            "認証経路エラー [AUTH-A001]: APIがJSONではなくログイン画面を返しました。"
        )
    }
    return JSONObject(body)
}

suspend fun fetchAverageData(
    serverIp: String,
    apiKey: String,
    startDate: String,
    endDate: String,
    sensors: List<String>,
    includeManual: Boolean,
    deviceId: String,
): List<AverageReading> = withContext(Dispatchers.IO) {
    val query = mutableListOf(
        "format=json",
        "display_mode=average",
        "average_source=${if (includeManual) "all" else "sensor"}",
    )
    if (startDate.isNotBlank()) query += "start_date=${URLEncoder.encode(startDate, "UTF-8")}"
    if (endDate.isNotBlank()) query += "end_date=${URLEncoder.encode(endDate, "UTF-8")}"
    if (deviceId.isNotBlank()) query += "device_id=${URLEncoder.encode(deviceId, "UTF-8")}"
    sensors.forEach { query += "sensors=${URLEncoder.encode(it, "UTF-8")}" }
    val connection = URL("https://$serverIp/api/v3/readings?${query.joinToString("&")}").openConnection() as HttpURLConnection
    try {
        connection.requestMethod = "GET"
        connection.setRequestProperty("X-API-Key", apiKey)
        connection.connectTimeout = 4000
        connection.readTimeout = 8000
        val body = (if (connection.responseCode == HttpURLConnection.HTTP_OK) connection.inputStream else connection.errorStream)
            .bufferedReader().use { it.readText() }
        if (connection.responseCode != HttpURLConnection.HTTP_OK) throw Exception("HTTP ${connection.responseCode}")
        val array = parseApiJsonObject(body).getJSONArray("average_results")
        List(array.length()) { index ->
            val item = array.getJSONObject(index)
            AverageReading(
                key = item.getString("sensor"),
                label = item.getString("label"),
                average = if (item.isNull("average")) null else item.getDouble("average"),
                count = item.getInt("count"),
            )
        }
    } finally {
        connection.disconnect()
    }
}

suspend fun fetchDevices(serverIp: String, apiKey: String): List<DeviceChoice> = withContext(Dispatchers.IO) {
    val connection = URL("https://$serverIp/api/v3/devices").openConnection() as HttpURLConnection
    try {
        connection.requestMethod = "GET"
        connection.setRequestProperty("X-API-Key", apiKey)
        connection.connectTimeout = 4000
        connection.readTimeout = 8000
        val body = (if (connection.responseCode == 200) connection.inputStream else connection.errorStream)
            .bufferedReader().use { it.readText() }
        if (connection.responseCode != 200) throw Exception("HTTP ${connection.responseCode}")
        val array = parseApiJsonObject(body).getJSONArray("devices")
        List(array.length()) { index ->
            val item = array.getJSONObject(index)
            DeviceChoice(item.getString("device_id"), item.getString("display_name"))
        }
    } finally {
        connection.disconnect()
    }
}

suspend fun renameDevice(
    serverIp: String,
    apiKey: String,
    deviceId: String,
    displayName: String,
) = withContext(Dispatchers.IO) {
    val connection = URL(
        "https://$serverIp/api/v3/devices/${URLEncoder.encode(deviceId, "UTF-8")}/display-name"
    ).openConnection() as HttpURLConnection
    try {
        connection.requestMethod = "POST"
        connection.doOutput = true
        connection.setRequestProperty("X-API-Key", apiKey)
        connection.setRequestProperty("Content-Type", "application/json; charset=UTF-8")
        connection.connectTimeout = 4000
        connection.readTimeout = 8000
        val payload = JSONObject().put("display_name", displayName).toString()
        connection.outputStream.use { it.write(payload.toByteArray(Charsets.UTF_8)) }
        val code = connection.responseCode
        val body = (if (code in 200..299) connection.inputStream else connection.errorStream)
            .bufferedReader().use { it.readText() }
        if (code !in 200..299 || parseApiJsonObject(body).optString("status") != "success") {
            throw Exception("HTTP $code")
        }
    } finally {
        connection.disconnect()
    }
}

private fun buildManualPayload(
    messageId: String,
    targetDeviceId: String,
    measuredAt: String,
    values: Map<String, String>,
    warningConfirmed: Boolean,
): JSONObject {
    val sensorJson = JSONObject()
    sensorDefinitions.forEach { sensor ->
        val raw = values[sensor.key].orEmpty().trim()
        sensorJson.put(sensor.key, if (raw.isBlank()) JSONObject.NULL else raw.toDouble())
    }
    return JSONObject()
        .put("message_id", messageId)
        .put("target_device_id", targetDeviceId)
        .put("measured_at", measuredAt)
        .put("warning_confirmed", warningConfirmed)
        .put("sensors", sensorJson)
}

suspend fun postManualReading(
    serverIp: String,
    apiKey: String,
    requestJson: JSONObject,
): ManualSaveResult = withContext(Dispatchers.IO) {
    val connection = URL("https://$serverIp/api/v3/manual-readings").openConnection() as HttpURLConnection
    try {
        connection.requestMethod = "POST"
        connection.doOutput = true
        connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
        connection.setRequestProperty("Accept", "application/json")
        connection.setRequestProperty("X-API-Key", apiKey)
        connection.connectTimeout = 4000
        connection.readTimeout = 8000
        connection.outputStream.bufferedWriter(Charsets.UTF_8).use { it.write(requestJson.toString()) }
        val status = connection.responseCode
        val stream = if (status in 200..299) connection.inputStream else connection.errorStream
        val body = stream.bufferedReader().use { it.readText() }
        val json = parseApiJsonObject(body)
        val ackStatus = json.optString("status", "retry")
        if (status == 409 && ackStatus == "confirmation_required") {
            throw Exception("危険値の確認が必要です")
        }
        ManualSaveResult(
            status = ackStatus,
            messageId = json.optString("message_id", requestJson.optString("message_id")),
            errorCode = json.optString("error_code").ifBlank { null },
        )
    } finally {
        connection.disconnect()
    }
}
