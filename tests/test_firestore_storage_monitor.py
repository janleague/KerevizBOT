import unittest
from datetime import datetime, timezone

from services.firestore_storage_monitor import (
    DEFAULT_LIMIT_BYTES,
    classify_threshold,
    calculate_percent,
    google_error_message,
    is_billing_required_message,
    normalize_alert_state,
    parse_monitoring_timeseries,
    parse_thresholds,
    should_reset_alert,
    should_send_permission_alert,
    should_send_threshold_alert,
)


class FirestoreStorageMonitorTests(unittest.TestCase):
    def test_classifies_thresholds(self):
        self.assertIsNone(classify_threshold(69.9, (70, 85, 95)))
        self.assertEqual(classify_threshold(70, (70, 85, 95)), 70)
        self.assertEqual(classify_threshold(85.2, (70, 85, 95)), 85)
        self.assertEqual(classify_threshold(99, (70, 85, 95)), 95)

    def test_resets_below_recovery_threshold(self):
        self.assertTrue(should_reset_alert(64.9))
        self.assertFalse(should_reset_alert(65.0))

    def test_suppresses_duplicate_threshold_alerts(self):
        self.assertTrue(should_send_threshold_alert(70, None))
        self.assertFalse(should_send_threshold_alert(70, 70))
        self.assertTrue(should_send_threshold_alert(85, 70))
        self.assertFalse(should_send_threshold_alert(None, 85))

    def test_parses_threshold_env_values(self):
        self.assertEqual(parse_thresholds("95,70,85"), (70, 85, 95))
        self.assertEqual(parse_thresholds("bad, 0, 101"), (70, 85, 95))

    def test_calculates_percent(self):
        self.assertEqual(calculate_percent(DEFAULT_LIMIT_BYTES // 2, DEFAULT_LIMIT_BYTES), 50.0)
        self.assertEqual(calculate_percent(123, 0), 0.0)

    def test_parses_monitoring_response_with_multiple_databases(self):
        payload = {
            "timeSeries": [
                {
                    "points": [
                        {
                            "interval": {"endTime": "2026-07-01T00:00:00Z"},
                            "value": {"int64Value": "100"},
                        }
                    ]
                },
                {
                    "points": [
                        {
                            "interval": {"endTime": "2026-07-01T00:05:00Z"},
                            "value": {"doubleValue": 200.5},
                        }
                    ]
                },
            ]
        }

        used_bytes, measured_at, database_count = parse_monitoring_timeseries(payload)

        self.assertEqual(used_bytes, 300)
        self.assertEqual(measured_at, datetime(2026, 7, 1, 0, 5, tzinfo=timezone.utc))
        self.assertEqual(database_count, 2)

    def test_ignores_empty_monitoring_points(self):
        used_bytes, measured_at, database_count = parse_monitoring_timeseries({"timeSeries": [{"points": []}]})
        self.assertEqual(used_bytes, 0)
        self.assertIsNone(measured_at)
        self.assertEqual(database_count, 0)

    def test_normalizes_alert_state(self):
        state = normalize_alert_state({"last_alerted_level": "85", "last_permission_alert_at": "10", "last_percent": "85.5"})
        self.assertEqual(state["last_alerted_level"], 85)
        self.assertEqual(state["last_permission_alert_at"], 10)
        self.assertEqual(state["last_percent"], 85.5)

    def test_permission_alert_cooldown(self):
        self.assertTrue(should_send_permission_alert(None, current_ts=100))
        self.assertFalse(should_send_permission_alert(100, current_ts=100 + 3600))
        self.assertTrue(should_send_permission_alert(100, current_ts=100 + 24 * 60 * 60))

    def test_detects_billing_required_google_error(self):
        body = '{"error":{"message":"This API method requires billing to be enabled."}}'
        message = google_error_message(body)
        self.assertEqual(message, "This API method requires billing to be enabled.")
        self.assertTrue(is_billing_required_message(message))
        self.assertFalse(is_billing_required_message("Permission monitoring.timeSeries.list denied"))


if __name__ == "__main__":
    unittest.main()
