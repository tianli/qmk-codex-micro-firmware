import json
from pathlib import Path
import tempfile
import time
import unittest

from host.mf17_bridge import (
    Bridge,
    MSG_CLEAR_PIXELS,
    MSG_SET_ALL,
    MSG_SET_PIXEL,
    MSG_SET_STATUS,
    SessionSlots,
    STATUS_IDS,
    latest_transcript_state,
)


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

    def test_process_jetski_status_packet(self):
        self.bridge.process_status({
            "kind": "jetski_status",
            "session_id": "test-session-123",
            "status": "thinking",
        })
        self.assertEqual(self.reports[-1], [MSG_SET_STATUS, 0, STATUS_IDS["thinking"]])


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.bridge = Bridge(Path("host/actions.json"), dry_run=True)
        self.shortcuts = []
        self.slashes = []
        self.palettes = []
        self.bridge.controller.shortcut = lambda keys, focus=False: self.shortcuts.append((keys, focus))
        self.bridge.controller.slash = lambda command, focus=False: self.slashes.append((command, focus))
        self.bridge.controller.palette = self.palettes.append

    def test_send_action(self):
        self.bridge.perform_action(17)
        self.assertEqual(self.shortcuts, [(["ENTER"], False)])

    def test_top_row_controls(self):
        self.bridge.perform_action(11)  # Approve
        self.bridge.perform_action(12)  # Reject
        self.bridge.perform_action(13)  # Up
        self.bridge.perform_action(14)  # Down
        self.assertEqual(
            self.shortcuts,
            [(["ENTER"], False), (["ESC"], False), (["UP"], False), (["DOWN"], False)],
        )

    def test_new_chat_and_dictation(self):
        self.bridge.perform_action(15)  # New Chat (+)
        self.bridge.perform_action(16)  # Dictation (.)
        self.assertEqual(
            self.shortcuts,
            [(["CTRL_OR_CMD", "SHIFT", "L"], False), (["CTRL_OR_CMD", "SHIFT", "D"], False)],
        )


if __name__ == "__main__":
    unittest.main()
