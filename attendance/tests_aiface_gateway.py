from datetime import datetime

from django.test import SimpleTestCase

from attendance.integrations.aiface_protocol import (
    build_reg_ack,
    build_sendlog_ack,
    build_senduser_ack,
    stable_record_id,
    translate_sendlog_record,
)


class RegAckTests(SimpleTestCase):
    def test_asks_device_to_skip_user_sync_and_images(self):
        ack = build_reg_ack(datetime(2026, 1, 1, 8, 0, 0))

        self.assertEqual(ack["ret"], "reg")
        self.assertTrue(ack["result"])
        self.assertEqual(ack["cloudtime"], "2026-01-01 08:00:00")
        self.assertTrue(ack["nosenduser"])
        self.assertTrue(ack["nosendimage"])
        self.assertFalse(ack["nosendlog"])


class SendUserAckTests(SimpleTestCase):
    def test_always_acknowledges_success(self):
        ack = build_senduser_ack(datetime(2026, 1, 1, 8, 0, 0))

        self.assertEqual(ack, {"ret": "senduser", "result": True, "cloudtime": "2026-01-01 08:00:00"})


class SendLogAckTests(SimpleTestCase):
    def test_includes_count_and_logindex_when_device_expects_confirmation(self):
        ack = build_sendlog_ack(datetime(2026, 1, 1, 8, 0, 0), result=True, count=3, logindex=0)

        self.assertEqual(ack["count"], 3)
        self.assertEqual(ack["logindex"], 0)

    def test_omits_count_and_logindex_when_device_sent_none(self):
        ack = build_sendlog_ack(datetime(2026, 1, 1, 8, 0, 0), result=True, count=None, logindex=None)

        self.assertNotIn("count", ack)
        self.assertNotIn("logindex", ack)

    def test_can_report_failure_so_device_retries(self):
        ack = build_sendlog_ack(datetime(2026, 1, 1, 8, 0, 0), result=False, count=1, logindex=0)

        self.assertFalse(ack["result"])


class StableRecordIdTests(SimpleTestCase):
    def test_same_punch_always_hashes_to_the_same_id(self):
        record = {"enrollid": 123, "time": "2026-01-01 08:00:00", "mode": 1, "inout": 0, "event": 0}

        first = stable_record_id("LF00000001", record)
        second = stable_record_id("LF00000001", record)

        self.assertEqual(first, second)
        self.assertGreater(first, 0)

    def test_same_punch_on_different_devices_hashes_differently(self):
        record = {"enrollid": 123, "time": "2026-01-01 08:00:00", "mode": 1, "inout": 0, "event": 0}

        self.assertNotEqual(
            stable_record_id("LF00000001", record),
            stable_record_id("LF00000002", record),
        )

    def test_distinct_punches_hash_differently(self):
        base = {"enrollid": 123, "time": "2026-01-01 08:00:00", "mode": 1, "inout": 0, "event": 0}
        different_time = {**base, "time": "2026-01-01 08:00:01"}

        self.assertNotEqual(
            stable_record_id("LF00000001", base),
            stable_record_id("LF00000001", different_time),
        )


class TranslateSendlogRecordTests(SimpleTestCase):
    def test_maps_aiface_fields_to_bridge_shape(self):
        record = {
            "enrollid": 123,
            "time": "2026-01-01 08:00:00",
            "mode": 1,
            "inout": 0,
            "event": 0,
        }

        translated = translate_sendlog_record("LF00000001", record)

        self.assertEqual(translated["enroll_id"], 123)
        self.assertEqual(translated["device_serial_number"], "LF00000001")
        self.assertEqual(translated["timestamp"], "2026-01-01 08:00:00")
        self.assertEqual(translated["mode"], 1)
        self.assertEqual(translated["inout"], 0)
        self.assertEqual(translated["event"], 0)
        self.assertIsNone(translated["temperature"])
        self.assertIsInstance(translated["gateway_record_id"], int)
        self.assertGreater(translated["gateway_record_id"], 0)

    def test_converts_device_temperature_units_to_celsius(self):
        record = {"enrollid": 1, "time": "2026-01-01 08:00:00", "mode": 1, "inout": 0, "event": 0, "temp": 365}

        translated = translate_sendlog_record("LF00000001", record)

        self.assertEqual(translated["temperature"], 36.5)

    def test_never_forwards_raw_biometric_fields(self):
        record = {
            "enrollid": 1,
            "time": "2026-01-01 08:00:00",
            "mode": 1,
            "inout": 0,
            "event": 0,
            "image": "base64-face-photo-data",
        }

        translated = translate_sendlog_record("LF00000001", record)

        self.assertNotIn("image", translated)
