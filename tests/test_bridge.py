import json
import tempfile
import unittest
from pathlib import Path

from host.mf17_bridge import (
    Bridge,
    MSG_CLEAR_PIXELS,
    MSG_SET_ALL,
    MSG_SET_PIXEL,
    SessionSlots,
    latest_transcript_state,
)


class SessionSlotTests(unittest.TestCase):
    def test_allocates_and_updates_slots(self):
        slots = SessionSlots()
        self.assertEqual(slots.update("thr_a", "idle"), 0)
        self.assertEqual(slots.update("thr_b", "thinking"), 1)
        self.assertEqual(slots.update("thr_a", "complete"), 0)
        self.assertEqual(slots.session_for_slot(0), "thr_a")
        self.assertEqual(slots.active_session, "thr_a")

    def test_remove_turns_slot_off(self):
        slots = SessionSlots()
        slots.update("thr_a", "idle")
        self.assertEqual(slots.update("thr_a", "off", remove=True), 0)
        self.assertIsNone(slots.session_for_slot(0))

    def test_recent_tasks_fill_empty_slots(self):
        slots = SessionSlots()
        self.assertTrue(slots.populate_recent(["thr_new", "thr_old"]))
        self.assertEqual(slots.session_for_slot(0), "thr_new")
        self.assertEqual(slots.session_for_slot(1), "thr_old")
        self.assertFalse(slots.populate_recent(["thr_new", "thr_old"]))

    def test_terminal_reconciliation_only_changes_stale_thinking(self):
        slots = SessionSlots()
        slots.update("thr_working", "thinking")
        slots.update("thr_idle", "idle")
        self.assertEqual(slots.reconcile_terminal("thr_working", "complete"), 0)
        self.assertEqual(slots.slots[0]["status"], "complete")
        self.assertIsNone(slots.reconcile_terminal("thr_idle", "complete"))
        self.assertEqual(slots.slots[1]["status"], "idle")

    def test_latest_transcript_state_handles_complete_abort_and_active(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout.jsonl"
            path.write_text(
                "\n".join(
                    json.dumps({"type": "event_msg", "payload": {"type": marker}})
                    for marker in ("task_started", "task_complete")
                ),
                encoding="utf-8",
            )
            self.assertEqual(latest_transcript_state(path), "complete")

            with path.open("a", encoding="utf-8") as stream:
                stream.write("\n" + json.dumps({"type": "event_msg", "payload": {"type": "turn_aborted"}}))
            self.assertEqual(latest_transcript_state(path), "idle")

            with path.open("a", encoding="utf-8") as stream:
                stream.write("\n" + json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}))
            self.assertIsNone(latest_transcript_state(path))


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


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.bridge = Bridge(Path("host/actions.json"), dry_run=True)
        self.shortcuts = []
        self.slashes = []
        self.palettes = []
        self.forks = []
        self.bridge.controller.shortcut = lambda keys, focus=True: self.shortcuts.append((keys, focus))
        self.bridge.controller.slash = lambda command, focus=False: self.slashes.append((command, focus))
        self.bridge.controller.palette = self.palettes.append
        self.bridge.controller.fork_new_chat = lambda: self.forks.append(True)

    def test_send_does_not_refocus_the_thread(self):
        self.bridge.perform_action(17)
        self.assertEqual(self.shortcuts, [(["ENTER"], False)])

    def test_plan_and_fast_use_supported_controls(self):
        self.bridge.perform_action(7)
        self.bridge.perform_action(12)
        self.assertEqual(self.shortcuts, [])
        self.assertEqual(self.slashes, [("/plan", True), ("/fast", True)])
        self.assertEqual(self.palettes, [])

    def test_continue_uses_fork_slash_command(self):
        self.bridge.perform_action(15)
        self.assertEqual(self.forks, [True])
        self.assertEqual(self.slashes, [])
        self.assertEqual(self.palettes, [])

    def test_permission_buttons_use_native_codex_shortcuts(self):
        self.bridge.perform_action(13)
        self.bridge.perform_action(14)
        self.assertEqual(self.shortcuts, [(["ENTER"], False), (["ESC"], False)])


if __name__ == "__main__":
    unittest.main()
