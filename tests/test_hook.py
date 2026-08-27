import unittest

from host.codex_status_hook import event_to_status


class HookStatusTests(unittest.TestCase):
    def test_lifecycle_mapping(self):
        self.assertEqual(event_to_status({"hook_event_name": "SessionStart"}), ("idle", False))
        self.assertEqual(event_to_status({"hook_event_name": "UserPromptSubmit"}), ("thinking", False))
        self.assertEqual(event_to_status({"hook_event_name": "PermissionRequest"}), ("waiting", False))
        self.assertEqual(event_to_status({"hook_event_name": "Stop"}), ("complete", False))
        self.assertEqual(event_to_status({"hook_event_name": "SessionEnd"}), ("off", True))

    def test_failed_tool_is_error(self):
        event = {"hook_event_name": "PostToolUse", "tool_response": {"exit_code": 1}}
        self.assertEqual(event_to_status(event), ("error", False))

    def test_request_user_input_is_waiting(self):
        event = {"hook_event_name": "PreToolUse", "tool_name": "request_user_input"}
        self.assertEqual(event_to_status(event), ("waiting", False))

        namespaced = {"hook_event_name": "PreToolUse", "tool_name": "functions__request_user_input"}
        self.assertEqual(event_to_status(namespaced), ("waiting", False))

if __name__ == "__main__":
    unittest.main()
