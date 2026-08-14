"""DHT22とBME280の送信前整合性検査テスト。"""

import unittest

from sensor_plausibility import dht22_matches_bme280


class SensorPlausibilityTests(unittest.TestCase):
    def test_normal_sensor_difference_is_accepted(self):
        self.assertTrue(dht22_matches_bme280(28.4, 60.2, 28.0, 58.0))

    def test_doubled_temperature_and_humidity_are_rejected(self):
        self.assertFalse(dht22_matches_bme280(56.0, 93.8, 28.1, 48.0))

    def test_large_difference_in_either_field_is_rejected(self):
        self.assertFalse(dht22_matches_bme280(45.0, 60.0, 28.0, 58.0))
        self.assertFalse(dht22_matches_bme280(28.0, 95.0, 28.0, 55.0))


if __name__ == "__main__":
    unittest.main()
