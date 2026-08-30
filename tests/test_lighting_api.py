import unittest

from host.lighting_api import (
    KEY_COUNT,
    KEY_NAMES,
    LEGACY_LED_BY_KEY,
    LightingReportEncoder,
    MSG_CLEAR_KEY_RGB,
    MSG_SET_KEY_RGB,
    PROTOCOL_VERSION,
    resolve_key,
)


class LogicalKeyTests(unittest.TestCase):
    def test_all_physical_keys_have_stable_ids_and_legacy_fallbacks(self):
        self.assertEqual(len(KEY_NAMES), KEY_COUNT)
        self.assertEqual(len(LEGACY_LED_BY_KEY), KEY_COUNT)
        self.assertEqual(len(set(LEGACY_LED_BY_KEY)), KEY_COUNT)

    def test_action_and_physical_aliases_resolve_to_the_same_key(self):
        self.assertEqual(resolve_key("approve"), resolve_key("num_lock"))
        self.assertEqual(resolve_key("agent_1"), resolve_key("1"))
        self.assertEqual(resolve_key("new chat"), resolve_key("plus"))
        self.assertEqual(resolve_key("send"), resolve_key("enter"))

    def test_v2_request_ids_advance(self):
        encoder = LightingReportEncoder(PROTOCOL_VERSION)
        first = encoder.encode({"key": "1", "rgb": [1, 2, 3]})[0]
        second = encoder.encode({"clear_key": "1"})[0]
        self.assertEqual(first[:2], [MSG_SET_KEY_RGB, 1])
        self.assertEqual(second[:2], [MSG_CLEAR_KEY_RGB, 2])


if __name__ == "__main__":
    unittest.main()
