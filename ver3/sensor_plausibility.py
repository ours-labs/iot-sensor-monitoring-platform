"""複数センサーを使った、送信前の保守的な妥当性検査。"""

from __future__ import annotations

DHT22_BME280_MAX_TEMPERATURE_DELTA = 10.0
DHT22_BME280_MAX_HUMIDITY_DELTA = 30.0


def dht22_matches_bme280(
    dht_temperature: float,
    dht_humidity: float,
    bme_temperature: float,
    bme_humidity: float,
) -> bool:
    """同じ基板上のDHT22とBME280が大きく矛盾しない場合だけTrueを返す。

    閾値は通常のセンサー差より十分広くし、約2倍化のようなサイレント破損だけを
    fail-closedでNULL化する。値を補正・推測して置き換えることはしない。
    """
    return (
        abs(float(dht_temperature) - float(bme_temperature))
        <= DHT22_BME280_MAX_TEMPERATURE_DELTA
        and abs(float(dht_humidity) - float(bme_humidity))
        <= DHT22_BME280_MAX_HUMIDITY_DELTA
    )
