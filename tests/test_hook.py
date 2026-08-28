import json
import unittest

from host.jetski_status_hook import event_to_status
from host.codex_status_hook import status_message


class HookStatusTests(unittest.TestCase):
    def test_jetski_lifecycle_mapping(self):
        # PreInvocation -> thinking (Blue LED)
        self.assertEqual(event_to_status({}, explicit_event="PreInvocation"), ("thinking", False))
        self.assertEqual(event_to_status({"hook_event_name": "PreInvocation"}), ("thinking", False))

        # PreToolUse with read tool -> thinking (Blue LED)
        self.assertEqual(
            event_to_status({"toolCall": {"name": "view_file"}}, explicit_event="PreToolUse"),
            ("thinking", False),
        )
        self.assertEqual(
            event_to_status({"toolCall": {"name": "list_dir"}}, explicit_event="PreToolUse"),
            ("thinking", False),
        )

        # PreToolUse with permission/action tools -> waiting (Amber LED)
        self.assertEqual(
            event_to_status({"toolCall": {"name": "run_command"}}, explicit_event="PreToolUse"),
            ("waiting", False),
        )
        self.assertEqual(
            event_to_status({"toolCall": {"name": "write_to_file"}}, explicit_event="PreToolUse"),
            ("waiting", False),
        )
        self.assertEqual(
            event_to_status({"toolCall": {"name": "replace_file_content"}}, explicit_event="PreToolUse"),
            ("waiting", False),
        )
        self.assertEqual(
            event_to_status({"toolCall": {"name": "ask_question"}}, explicit_event="PreToolUse"),
            ("waiting", False),
        )
        self.assertEqual(
            event_to_status({"toolCall": {"name": "ask_custom_permission"}}, explicit_event="PreToolUse"),
            ("waiting", False),
        )
        self.assertEqual(
            event_to_status({"toolCall": {"name": "request_user_input"}}, explicit_event="PreToolUse"),
            ("waiting", False),
        )

        # PostToolUse without error -> thinking (Blue LED)
        self.assertEqual(
            event_to_status({"stepIdx": 5}, explicit_event="PostToolUse"),
            ("thinking", False),
        )

        # PostToolUse with error -> error (Red LED)
        self.assertEqual(
            event_to_status({"stepIdx": 5, "error": "exit status 1"}, explicit_event="PostToolUse"),
            ("error", False),
        )

        # Stop without error -> complete (Green LED)
        self.assertEqual(
            event_to_status({"terminationReason": "model_stop"}, explicit_event="Stop"),
            ("complete", False),
        )

        # Stop with error -> error (Red LED)
        self.assertEqual(
            event_to_status({"terminationReason": "error", "error": "fatal"}, explicit_event="Stop"),
            ("error", False),
        )

        # SessionEnd -> off
        self.assertEqual(
            event_to_status({}, explicit_event="SessionEnd"),
            ("off", True),
        )

    def test_codex_backwards_compatibility(self):
        self.assertEqual(event_to_status({"hook_event_name": "SessionStart"}), ("idle", False))
        self.assertEqual(event_to_status({"hook_event_name": "UserPromptSubmit"}), ("thinking", False))
        self.assertEqual(event_to_status({"hook_event_name": "PermissionRequest"}), ("waiting", False))
        self.assertEqual(event_to_status({"hook_event_name": "Stop"}), ("complete", False))
        self.assertEqual(event_to_status({"hook_event_name": "SessionEnd"}), ("off", True))
        self.assertEqual(event_to_status({"hook_event_name": "PostToolUse", "tool_response": {"exit_code": 1}}), ("error", False))
        self.assertEqual(event_to_status({"hook_event_name": "PreToolUse", "tool_name": "functions__request_user_input"}), ("waiting", False))

    def test_codex_status_message_is_provider_tagged(self):
        message = status_message({
            "hook_event_name": "UserPromptSubmit",
            "session_id": "codex-session-123",
            "cwd": "C:/work",
            "transcript_path": "C:/work/transcript.jsonl",
        })
        self.assertEqual(message["kind"], "codex_status")
        self.assertEqual(message["provider"], "codex")
        self.assertEqual(message["session_id"], "codex-session-123")
        self.assertEqual(message["status"], "thinking")
        self.assertFalse(message["remove"])


if __name__ == "__main__":
    unittest.main()
