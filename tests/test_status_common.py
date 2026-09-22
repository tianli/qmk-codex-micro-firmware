import json
import unittest

from host.status_common import (
    PROVIDER_ANTIGRAVITY,
    PROVIDER_CODEX,
    base_tool_name,
    conversation_uri,
    is_interactive_tool,
    is_permission_tool,
    looks_like_user_prompt,
    normalize_provider,
    normalize_task_id,
    scan_transcript_background_tasks,
)


class StatusCommonTests(unittest.TestCase):
    def test_normalize_provider(self):
        self.assertEqual(normalize_provider("codex"), PROVIDER_CODEX)
        self.assertEqual(normalize_provider("OpenAI_Codex"), PROVIDER_CODEX)
        self.assertEqual(normalize_provider("antigravity"), PROVIDER_ANTIGRAVITY)
        self.assertEqual(normalize_provider("jetski"), PROVIDER_ANTIGRAVITY)
        self.assertEqual(normalize_provider("gemini"), PROVIDER_ANTIGRAVITY)
        self.assertEqual(normalize_provider("unknown_prov", default="custom"), "custom")

    def test_conversation_uri(self):
        self.assertEqual(
            conversation_uri("1234-5678", PROVIDER_CODEX),
            "codex://threads/1234-5678",
        )
        self.assertEqual(
            conversation_uri("abcd-ef01", PROVIDER_ANTIGRAVITY),
            "jetski-ide://conversation/abcd-ef01",
        )

    def test_normalize_task_id(self):
        self.assertEqual(normalize_task_id('"task/abc-123"'), "abc-123")
        self.assertEqual(normalize_task_id("'xyz-999'"), "xyz-999")
        self.assertEqual(normalize_task_id("task-1"), "task-1")

    def test_looks_like_user_prompt(self):
        # Explicit trailing question
        self.assertTrue(looks_like_user_prompt("Would you like me to proceed with this plan?"))
        self.assertTrue(looks_like_user_prompt("Which option do you prefer?"))

        # Non-prompt statements
        self.assertFalse(looks_like_user_prompt("I have finished all tasks successfully."))
        self.assertFalse(looks_like_user_prompt(""))
        self.assertFalse(looks_like_user_prompt(123))

        # Question marks inside code blocks must not trigger
        code_msg = "Here is the code:\n```python\nx = foo() if bar else baz ?\n```\nDone."
        self.assertFalse(looks_like_user_prompt(code_msg))

        # Explicit regex pattern in tail
        self.assertTrue(looks_like_user_prompt("Please let me know if you would like any changes."))

    def test_tool_helpers(self):
        self.assertEqual(base_tool_name("functions__request_user_input"), "request_user_input")
        self.assertEqual(base_tool_name("mcp/ask_question"), "ask_question")
        self.assertEqual(base_tool_name("run_command"), "run_command")

        self.assertTrue(is_interactive_tool("ask_question"))
        self.assertTrue(is_interactive_tool("functions__request_user_input"))
        self.assertFalse(is_interactive_tool("view_file"))

        self.assertTrue(is_permission_tool("run_command"))
        self.assertTrue(is_permission_tool("write_to_file"))
        self.assertTrue(is_permission_tool("ask_question"))
        self.assertFalse(is_permission_tool("view_file"))
        self.assertFalse(is_permission_tool("list_dir"))

    def test_scan_transcript_background_tasks(self):
        lines = [
            json.dumps({"type": "TOOL_USE", "status": "RUNNING", "content": "task id: task-alpha"}),
            json.dumps({"type": "GENERIC", "content": "Task 'task-alpha' was completed."}),
            json.dumps({"type": "TOOL_USE", "status": "RUNNING", "content": "task id: task-beta"}),
        ]
        completed, running = scan_transcript_background_tasks(lines)
        self.assertIn("task-alpha", completed)
        self.assertNotIn("task-alpha", running)
        self.assertIn("task-beta", running)


if __name__ == "__main__":
    unittest.main()
