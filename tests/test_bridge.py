import json
import ctypes
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from host.mf17_bridge import (
    Bridge,
    active_codex_sessions,
    archived_codex_session_ids,
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
    POINT,
    RECT,
    root_codex_session_id,
    SessionSlots,
    STATUS_IDS,
    conversation_uri,
    latest_transcript_state,
    visible_codex_session_ids,
)
from host.lighting_api import LEGACY_LED_BY_KEY, resolve_key


class SessionSlotTests(unittest.TestCase):
    def test_active_codex_sessions_exclude_archived_and_internal_tasks(self):
        active_id = "11111111-1111-1111-1111-111111111111"
        archived_id = "22222222-2222-2222-2222-222222222222"
        internal_id = "33333333-3333-3333-3333-333333333333"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active_rollout = root / f"rollout-{active_id}.jsonl"
            archived_rollout = root / f"rollout-{archived_id}.jsonl"
            internal_rollout = root / f"rollout-{internal_id}.jsonl"
            for path in (active_rollout, archived_rollout, internal_rollout):
                path.write_text("{}\n", encoding="utf-8")

            state = root / "state.sqlite"
            connection = sqlite3.connect(state)
            try:
                connection.execute(
                    "CREATE TABLE threads ("
                    "id TEXT, rollout_path TEXT, archived INTEGER, source TEXT, "
                    "thread_source TEXT, recency_at_ms INTEGER)"
                )
                connection.executemany(
                    "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (archived_id, str(archived_rollout), 1, "vscode", "user", 300),
                        (internal_id, str(internal_rollout), 0, "vscode", "subagent", 200),
                        (active_id, str(active_rollout), 0, "vscode", "user", 100),
                    ],
                )
                connection.commit()
            finally:
                connection.close()

            self.assertEqual(active_codex_sessions(state), [(active_id, active_rollout)])
            self.assertEqual(archived_codex_session_ids(state, root / "missing"), {archived_id})

    def test_visible_index_subtracts_legacy_archive_files(self):
        visible_id = "11111111-1111-1111-1111-111111111111"
        archived_id = "22222222-2222-2222-2222-222222222222"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = root / "session_index.jsonl"
            index.write_text(
                json.dumps({"id": visible_id}) + "\n" + json.dumps({"id": archived_id}) + "\n",
                encoding="utf-8",
            )
            archive = root / "archived_sessions"
            archive.mkdir()
            (archive / f"rollout-2026-01-01T00-00-00-{archived_id}.jsonl").write_text(
                "{}\n", encoding="utf-8"
            )
            self.assertEqual(
                visible_codex_session_ids(index, root / "missing.sqlite", archive),
                {visible_id},
            )

    def test_desktop_index_and_metadata_exclude_internal_or_broken_tasks(self):
        visible_id = "11111111-1111-1111-1111-111111111111"
        child_id = "22222222-2222-2222-2222-222222222222"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = root / "session_index.jsonl"
            index.write_text(json.dumps({"id": visible_id}) + "\n", encoding="utf-8")
            visible = visible_codex_session_ids(
                index, root / "missing.sqlite", root / "missing_archive"
            )

            continuation = root / f"rollout_parent_{child_id}.jsonl"
            continuation.write_text(json.dumps({
                "type": "session_meta",
                "payload": {
                    "id": visible_id,
                    "session_id": visible_id,
                    "source": "vscode",
                    "thread_source": "user",
                },
            }) + "\n", encoding="utf-8")
            self.assertEqual(root_codex_session_id(continuation, visible), visible_id)

            internal = root / "reviewer.jsonl"
            internal.write_text(json.dumps({
                "type": "session_meta",
                "payload": {
                    "id": child_id,
                    "source": {"subagent": {"other": "guardian"}},
                    "thread_source": "subagent",
                },
            }) + "\n", encoding="utf-8")
            self.assertIsNone(root_codex_session_id(internal, visible))

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
        self.assertEqual(slots.session_for_slot(9), "session_1")  # Key 0: most recent
        self.assertEqual(slots.session_for_slot(0), "session_2")  # Key 1: next most recent
        self.assertFalse(slots.populate_recent(["session_1", "session_2"]))

    def test_recent_refresh_reorders_and_removes_stale_tasks(self):
        slots = SessionSlots()
        slots.populate_recent(["old_recent", "old_second", "archived"])
        self.assertTrue(slots.populate_recent(["new_recent", "old_recent"]))
        self.assertEqual(slots.session_for_slot(9), "new_recent")
        self.assertEqual(slots.session_for_slot(0), "old_recent")
        self.assertNotIn(
            "archived",
            [item["session_id"] for item in slots.slots if item],
        )

    def test_active_only_mode_keeps_only_live_hook_sessions(self):
        slots = SessionSlots()
        # Active only mode on empty slots does not load disk history
        self.assertFalse(slots.populate_recent(["past_session_1", "past_session_2"], active_only=True))
        self.assertIsNone(slots.session_for_slot(0))
        self.assertIsNone(slots.session_for_slot(1))

        # Dynamic live hook assigns slot 0
        slots.update("live_session", "thinking", from_hook=True)
        self.assertEqual(slots.session_for_slot(0), "live_session")

        # populate_recent in active_only preserves live session
        self.assertFalse(slots.populate_recent([], active_only=True))
        self.assertEqual(slots.session_for_slot(0), "live_session")

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
            [MSG_CAPABILITIES, 0, PROTOCOL_VERSION, KEY_COUNT, CAP_LOGICAL_KEYS, 0, 7, 2]
        )
        self.assertEqual(self.bridge.lighting.protocol_version, PROTOCOL_VERSION)
        self.assertEqual(self.bridge.action_protocol_version, PROTOCOL_VERSION)

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

    def test_zero_opens_most_recent_then_one_opens_next(self):
        self.bridge.sessions.populate_recent(
            ["most-recent", "next-recent"],
            providers={"most-recent": "codex", "next-recent": "codex"},
        )
        opened = []
        self.bridge.controller.open_thread = lambda session_id, slot=0, provider="": opened.append(
            (session_id, slot, provider)
        )
        self.bridge.perform_action(10)  # Physical key 0
        self.bridge.perform_action(1)   # Physical key 1
        self.assertEqual(
            opened,
            [
                ("most-recent", 9, "codex"),
                ("next-recent", 0, "codex"),
            ],
        )

    def test_send_action(self):
        self.bridge.perform_action(17)
        self.assertEqual(self.shortcuts, [(["ENTER"], False)])

    def test_legacy_firmware_uses_six_agent_joystick_action_table(self):
        self.bridge.action_protocol_version = 1
        self.bridge.perform_action(7)
        self.bridge.perform_action(9)
        self.bridge.perform_action(13)
        self.bridge.perform_action(15)
        self.assertEqual(self.scrolls, ["up", "down"])
        self.assertEqual(self.shortcuts, [(["ENTER"], False)])
        self.assertEqual(self.new_chats, 1)

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
            [(["CTRL_OR_CMD", "SHIFT", "D"], None)],
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
        wheel = []
        self.controller.is_win = True
        self.controller._scroll_windows_conversation = lambda direction: not wheel.append(direction)
        self.controller.context_scroll("up")
        self.controller.context_scroll("down")
        self.assertEqual(wheel, ["up", "down"])
        self.assertEqual(self.sent, [])


class WindowsPageKeyInjectionTests(unittest.TestCase):
    class FakeUser32:
        def __init__(self):
            self.events = []

        def MapVirtualKeyW(self, code, _map_type):
            return code

        def keybd_event(self, code, scan, flags, extra_info):
            self.events.append((code, scan, flags, extra_info))

    def setUp(self):
        self.controller = Bridge(Path("host/actions.json"), dry_run=True).controller
        self.user32 = self.FakeUser32()
        self.controller.is_win = True
        self.controller.is_mac = False
        self.controller.is_linux = False
        self.controller.user32 = self.user32

    def test_desktop_page_keys_are_extended_windows_input(self):
        self.controller._foreground_context = lambda: "Codex"
        self.controller._scroll_windows_conversation = lambda direction: False
        self.controller.context_scroll("up")
        self.controller.context_scroll("down")
        self.assertEqual(
            self.user32.events,
            [
                (0x21, 0x21, 0x0001, 0),
                (0x21, 0x21, 0x0003, 0),
                (0x22, 0x22, 0x0001, 0),
                (0x22, 0x22, 0x0003, 0),
            ],
        )

    def test_terminal_page_keys_keep_shift_and_extended_page_key(self):
        self.controller._foreground_context = lambda: "WindowsTerminal.exe"
        self.controller.context_scroll("up")
        self.assertEqual(
            self.user32.events,
            [
                (0x10, 0x10, 0, 0),
                (0x21, 0x21, 0x0001, 0),
                (0x21, 0x21, 0x0003, 0),
                (0x10, 0x10, 0x0002, 0),
            ],
        )

    def test_desktop_wheel_targets_content_pane_and_restores_cursor(self):
        class FakeWheelUser32:
            def __init__(self):
                self.cursor_positions = []
                self.wheel_events = []

            def GetForegroundWindow(self):
                return 123

            def GetWindowRect(self, _hwnd, pointer):
                bounds = ctypes.cast(pointer, ctypes.POINTER(RECT)).contents
                bounds.left, bounds.top, bounds.right, bounds.bottom = 0, 0, 1000, 1000
                return True

            def GetCursorPos(self, pointer):
                point = ctypes.cast(pointer, ctypes.POINTER(POINT)).contents
                point.x, point.y = 10, 20
                return True

            def SetCursorPos(self, x, y):
                self.cursor_positions.append((x, y))
                return True

            def mouse_event(self, flags, dx, dy, data, extra_info):
                self.wheel_events.append((flags, dx, dy, data, extra_info))

        fake = FakeWheelUser32()
        self.controller.user32 = fake
        self.assertTrue(self.controller._scroll_windows_conversation("down"))
        self.assertEqual(fake.cursor_positions, [(700, 450), (10, 20)])
        self.assertEqual(
            fake.wheel_events,
            [(self.controller.MOUSEEVENTF_WHEEL, 0, 0, (-840) & 0xFFFFFFFF, 0)],
        )

    def test_process_status_message_updates_slots(self):
        bridge = Bridge(Path("host/actions.json"), dry_run=True)
        session_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        payload = {
            "kind": "jetski_status",
            "provider": "antigravity",
            "session_id": session_id,
            "status": "thinking",
            "remove": False,
        }
        bridge.process_status(payload)
        self.assertEqual(bridge.sessions.active_session, session_id)
        slot = next(idx for idx, item in enumerate(bridge.sessions.slots) if item and item["session_id"] == session_id)
        self.assertEqual(bridge.sessions.slots[slot]["status"], "thinking")
        self.assertEqual(bridge.sessions.slots[slot]["provider"], "antigravity")


if __name__ == "__main__":
    unittest.main()

