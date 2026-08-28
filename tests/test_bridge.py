import json
from pathlib import Path
import tempfile
import time
import unittest

from host.mf17_bridge import (
    Bridge,
    CAP_LOGICAL_KEYS,
    KEY_COUNT,
    MSG_CAPABILITIES,
    MSG_CLEAR_ALL_KEY_RGB,
    MSG_CLEAR_PIXELS,
    MSG_SET_ALL,
    MSG_SET_ALL_KEYS_RGB,
    MSG_SET_KEY_RGB,
    MSG_SET_KEYS_RGB,
    MSG_SET_PIXEL,
    MSG_SET_STATUS,
    PROVIDER_CODEX,
    PROVIDER_ANTIGRAVITY,
    PROTOCOL_VERSION,
    SessionSlots,
    STATUS_IDS,
    conversation_uri,
    latest_transcript_state,
)
from host.lighting_api import LEGACY_LED_BY_KEY, resolve_key


class SessionSlotTests(unittest.TestCase):
    def test_allocates_and_updates_ten_slots(self):
        slots = SessionSlots()
        for i in range(10):
            self.assertEqual(slots.update(f"session_{i}", "idle"), i)
        self.assertEqual(slots.session_for_slot(9), "session_9")
        self.assertEqual(slots.active_session, "session_9")

    def test_remove_turns_slot_off(self):
        slots = SessionSlots()
        slots.update("92e737fb-e2d9-4120-a89a-f1d7fccbbb25", "idle")
        self.assertEqual(slots.update("92e737fb-e2d9-4120-a89a-f1d7fccbbb25", "off", remove=True), 0)
        self.assertIsNone(slots.session_for_slot(0))

    def test_recent_tasks_fill_empty_slots(self):
        slots = SessionSlots()
        self.assertTrue(slots.populate_recent(["session_1", "session_2"]))
        self.assertEqual(slots.session_for_slot(0), "session_1")
        self.assertEqual(slots.session_for_slot(1), "session_2")
        self.assertFalse(slots.populate_recent(["session_1", "session_2"]))

    def test_terminal_reconciliation_respects_recent_hook(self):
        slots = SessionSlots()
        slots.update("session_working", "thinking", from_hook=True)
        # Immediately trying to reconcile thinking should be ignored (live hook protection)
        self.assertIsNone(slots.reconcile_terminal("session_working", "complete"))
        self.assertEqual(slots.slots[0]["status"], "thinking")

        # Simulate stale thinking state (> 45s)
        slots.slots[0]["last_hook_time"] = time.time() - 50.0
        self.assertEqual(slots.reconcile_terminal("session_working", "complete"), 0)
        self.assertEqual(slots.slots[0]["status"], "complete")


class LightingProtocolTests(unittest.TestCase):
    def setUp(self):
        self.bridge = Bridge(Path("host/actions.json"), dry_run=True)
        self.reports = []
        self.bridge.send_report = self.reports.append

    def test_all_pixel_and_clear_commands(self):
        self.bridge.process_lighting({"all": [1, 2, 300]})
        self.bridge.process_lighting({"pixel": 8, "rgb": [255, 4, 3]})
        self.bridge.process_lighting({"clear": True})
        self.assertEqual(self.reports[0], [MSG_SET_ALL, 1, 2, 255])
        self.assertEqual(self.reports[1], [MSG_SET_PIXEL, 8, 255, 4, 3])
        self.assertEqual(self.reports[2], [MSG_CLEAR_PIXELS])

    def test_v2_addresses_keys_by_logical_name_and_overrides_all_layers(self):
        self.bridge.lighting.protocol_version = PROTOCOL_VERSION
        self.bridge.process_lighting({"key": "agent_1", "rgb": [9, 8, 7]})
        self.bridge.process_lighting({"all": [1, 2, 3]})
        self.bridge.process_lighting({"clear": True})

        self.assertEqual(self.reports[0], [MSG_SET_KEY_RGB, 1, resolve_key("1"), 9, 8, 7])
        self.assertEqual(self.reports[1], [MSG_SET_ALL_KEYS_RGB, 2, 1, 2, 3])
        self.assertEqual(self.reports[2], [MSG_CLEAR_ALL_KEY_RGB, 3])

    def test_v2_batches_more_keys_than_fit_in_one_report(self):
        self.bridge.lighting.protocol_version = PROTOCOL_VERSION
        colors = {str(digit): [digit, 0, 0] for digit in range(8)}
        self.bridge.process_lighting({"keys": colors})

        self.assertEqual(len(self.reports), 2)
        self.assertEqual(self.reports[0][:3], [MSG_SET_KEYS_RGB, 1, 7])
        self.assertEqual(self.reports[1][:3], [MSG_SET_KEYS_RGB, 2, 1])

    def test_logical_keys_fall_back_to_legacy_led_indices(self):
        self.bridge.process_lighting({"key": "approve", "rgb": [4, 5, 6]})
        self.assertEqual(
            self.reports,
            [[MSG_SET_PIXEL, LEGACY_LED_BY_KEY[resolve_key("approve")], 4, 5, 6]],
        )

    def test_capability_report_enables_v2(self):
        self.bridge.process_hid_report(
            [MSG_CAPABILITIES, 0, PROTOCOL_VERSION, KEY_COUNT, CAP_LOGICAL_KEYS, 0, 7]
        )
        self.assertEqual(self.bridge.lighting.protocol_version, PROTOCOL_VERSION)

    def test_process_jetski_status_packet(self):
        self.bridge.process_status({
            "kind": "jetski_status",
            "session_id": "test-session-123",
            "status": "thinking",
        })
        self.assertEqual(self.reports[-1], [MSG_SET_STATUS, 0, STATUS_IDS["thinking"]])

    def test_process_codex_status_packet_tracks_provider(self):
        self.bridge.process_status({
            "kind": "codex_status",
            "provider": "codex",
            "session_id": "codex-session-123",
            "status": "thinking",
        })
        self.assertEqual(self.bridge.sessions.provider_for_slot(0), PROVIDER_CODEX)
        self.assertEqual(self.reports[-1], [MSG_SET_STATUS, 0, STATUS_IDS["thinking"]])

    def test_conversation_uris_are_provider_specific(self):
        session_id = "a session/with spaces"
        self.assertEqual(
            conversation_uri(session_id, PROVIDER_CODEX),
            "codex://threads/a%20session%2Fwith%20spaces",
        )
        self.assertEqual(
            conversation_uri(session_id, PROVIDER_ANTIGRAVITY),
            "jetski-ide://conversation/a%20session%2Fwith%20spaces",
        )


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.bridge = Bridge(Path("host/actions.json"), dry_run=True)
        self.shortcuts = []
        self.slashes = []
        self.palettes = []
        self.scrolls = []
        self.new_chats = 0
        self.microphones = []
        self.bridge.controller.shortcut = lambda keys, focus=False: self.shortcuts.append((keys, focus))
        self.bridge.controller.slash = lambda command, focus=False: self.slashes.append((command, focus))
        self.bridge.controller.palette = self.palettes.append
        self.bridge.controller.context_scroll = lambda direction: self.scrolls.append(direction)
        self.bridge.controller.new_chat = lambda: setattr(self, "new_chats", self.new_chats + 1)
        self.bridge.controller.microphone = lambda keys=None, terminal_keys=None: self.microphones.append(
            (keys, terminal_keys)
        )

    def test_agent_action_routes_codex_provider(self):
        self.bridge.process_status({
            "kind": "codex_status",
            "provider": "codex",
            "session_id": "codex-session-123",
            "status": "idle",
        })
        opened = []
        self.bridge.controller.open_thread = lambda session_id, slot=0, provider="": opened.append(
            (session_id, slot, provider)
        )
        self.bridge.perform_action(1)
        self.assertEqual(opened, [("codex-session-123", 0, "codex")])

    def test_send_action(self):
        self.bridge.perform_action(17)
        self.assertEqual(self.shortcuts, [(["ENTER"], False)])

    def test_top_row_controls(self):
        self.bridge.perform_action(11)  # Approve
        self.bridge.perform_action(12)  # Reject
        self.bridge.perform_action(13)  # Scroll up
        self.bridge.perform_action(14)  # Scroll down
        self.assertEqual(self.shortcuts, [(["ENTER"], False), (["ESC"], False)])
        self.assertEqual(self.scrolls, ["up", "down"])

    def test_new_chat_and_dictation(self):
        self.bridge.perform_action(15)  # New Chat (+)
        self.bridge.perform_action(16)  # Dictation (.)
        self.assertEqual(self.new_chats, 1)
        self.assertEqual(
            self.microphones,
            [(["CTRL_OR_CMD", "SHIFT", "D"], ["M"])],
        )


class ContextDetectionTests(unittest.TestCase):
    def setUp(self):
        self.controller = Bridge(Path("host/actions.json"), dry_run=True).controller
        self.sent = []
        self.controller.shortcut = lambda keys, focus=False: self.sent.append((keys, focus))

    def test_terminal_context_uses_shifted_page_keys(self):
        self.controller._foreground_context = lambda: "WindowsTerminal.exe codex"
        self.controller.context_scroll("up")
        self.controller.context_scroll("down")
        self.assertEqual(
            self.sent,
            [(["SHIFT", "PAGEUP"], False), (["SHIFT", "PAGEDOWN"], False)],
        )

    def test_desktop_context_uses_plain_page_keys(self):
        self.controller._foreground_context = lambda: "Codex"
        self.controller.context_scroll("up")
        self.controller.context_scroll("down")
        self.assertEqual(
            self.sent,
            [(["PAGEUP"], False), (["PAGEDOWN"], False)],
        )


if __name__ == "__main__":
    unittest.main()
