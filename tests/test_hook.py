import json
from pathlib import Path
import tempfile
import unittest

from host.jetski_status_hook import event_to_status
from host.codex_status_hook import status_message


class HookStatusTests(unittest.TestCase):
    def test_jetski_lifecycle_mapping(self):
        # PreInvocation -> thinking (Blue LED)
        self.assertEqual(event_to_status({}, explicit_event="PreInvocation"), ("thinking", False))
        self.assertEqual(event_to_status({"hook_event_name": "PreInvocation"}), ("thinking", False))

        # PreToolUse with tools that DO NOT need permission (read/search tools) -> executing (Purple LED)
        self.assertEqual(
            event_to_status({"toolCall": {"name": "view_file"}}, explicit_event="PreToolUse"),
            ("executing", False),
        )
        self.assertEqual(
            event_to_status({"toolCall": {"name": "list_dir"}}, explicit_event="PreToolUse"),
            ("executing", False),
        )

        # PreToolUse with command and edit tools that require permission -> waiting (Amber LED)
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

        # PreToolUse with interactive question / permission tools -> waiting (Amber LED)
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

        # Stop without error and fully idle -> complete (Green LED)
        self.assertEqual(
            event_to_status({"terminationReason": "model_stop", "fullyIdle": True}, explicit_event="Stop"),
            ("complete", False),
        )
        self.assertEqual(
            event_to_status({"terminationReason": "model_stop"}, explicit_event="Stop"),
            ("complete", False),
        )

        # Stop while background tasks/subagents are running (fullyIdle is False) -> executing (Purple LED)
        self.assertEqual(
            event_to_status({"terminationReason": "model_stop", "fullyIdle": False}, explicit_event="Stop"),
            ("executing", False),
        )
        self.assertEqual(
            event_to_status({"terminationReason": "model_stop", "fully_idle": False}, explicit_event="Stop"),
            ("executing", False),
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

    def test_interactive_tool_name_variants_are_waiting(self):
        for tool_name in (
            "request_user_input",
            "functions.request_user_input",
            "functions__request_user_input",
            "mcp/request_user_input",
        ):
            with self.subTest(tool_name=tool_name):
                self.assertEqual(
                    event_to_status({"tool_name": tool_name}, explicit_event="PreToolUse"),
                    ("waiting", False),
                )

    def test_stop_awaiting_user_input_is_waiting_not_complete(self):
        self.assertEqual(
            event_to_status(
                {"termination_reason": "awaiting_user_input"},
                explicit_event="Stop",
            ),
            ("waiting", False),
        )
        self.assertEqual(
            event_to_status(
                {"waiting_for_user": True, "terminationReason": "model_stop"},
                explicit_event="Stop",
            ),
            ("waiting", False),
        )

    def test_stop_final_assistant_question_is_waiting(self):
        self.assertEqual(
            event_to_status(
                {"last_assistant_message": "Which option should I use?"},
                explicit_event="Stop",
            ),
            ("waiting", False),
        )
        self.assertEqual(
            event_to_status(
                {"last_assistant_message": "Please provide the target directory."},
                explicit_event="Stop",
            ),
            ("waiting", False),
        )

    def test_subagent_lifecycle_mapping(self):
        # Subagent starting / running should report thinking
        self.assertEqual(
            event_to_status({"isSubagent": True}, explicit_event="SubagentStart"),
            ("thinking", False),
        )
        self.assertEqual(
            event_to_status({"subagentName": "research"}, explicit_event="PreInvocation"),
            ("thinking", False),
        )
        # Subagent stop without error
        self.assertEqual(
            event_to_status({"isSubagent": True}, explicit_event="SubagentStop"),
            ("complete", False),
        )

    def test_stop_with_running_background_task_in_transcript(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            transcript = Path(tmp_dir) / "transcript.jsonl"
            lines = [
                {"step_index": 0, "type": "USER_INPUT", "status": "DONE"},
                {
                    "step_index": 1,
                    "type": "GENERIC",
                    "status": "RUNNING",
                    "content": "Tool is running as a background task with task id: sess/task-1",
                },
                {
                    "step_index": 2,
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "content": "I launched task-1 in background.",
                },
            ]
            transcript.write_text("\n".join(json.dumps(l) for l in lines), encoding="utf-8")

            # With task-1 running, Stop, PreInvocation, PostToolUse, PostInvocation should report executing
            self.assertEqual(
                event_to_status(
                    {"terminationReason": "model_stop", "transcriptPath": str(transcript)},
                    explicit_event="Stop",
                ),
                ("executing", False),
            )
            self.assertEqual(
                event_to_status(
                    {"transcriptPath": str(transcript)},
                    explicit_event="PreInvocation",
                ),
                ("executing", False),
            )
            self.assertEqual(
                event_to_status(
                    {"transcriptPath": str(transcript), "tool_name": "run_command"},
                    explicit_event="PostToolUse",
                ),
                ("executing", False),
            )
            self.assertEqual(
                event_to_status(
                    {"transcriptPath": str(transcript)},
                    explicit_event="PostInvocation",
                ),
                ("executing", False),
            )

            # Once task-1 is finished, Stop should report complete
            lines.append({
                "step_index": 3,
                "type": "SYSTEM_MESSAGE",
                "status": "DONE",
                "content": "<notifications>\nTask sess/task-1 finished with the following output: success",
            })
            lines.append({
                "step_index": 4,
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "content": "Task finished successfully.",
            })
            transcript.write_text("\n".join(json.dumps(l) for l in lines), encoding="utf-8")

            self.assertEqual(
                event_to_status(
                    {"terminationReason": "model_stop", "transcriptPath": str(transcript)},
                    explicit_event="Stop",
                ),
                ("complete", False),
            )

    def test_background_task_canceled_reports_complete(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            transcript = Path(tmp_dir) / "transcript.jsonl"
            lines = [
                {"step_index": 0, "type": "USER_INPUT", "status": "DONE"},
                {
                    "step_index": 1,
                    "type": "GENERIC",
                    "status": "RUNNING",
                    "content": "Tool is running as a background task with task id: sess/task-2",
                },
                {
                    "step_index": 2,
                    "type": "GENERIC",
                    "status": "DONE",
                    "content": 'Task "sess/task-2" cancelled.',
                },
                {
                    "step_index": 3,
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "content": "Task was canceled.",
                },
            ]
            transcript.write_text("\n".join(json.dumps(l) for l in lines), encoding="utf-8")
            self.assertEqual(
                event_to_status(
                    {"terminationReason": "model_stop", "transcriptPath": str(transcript)},
                    explicit_event="Stop",
                ),
                ("complete", False),
            )

    def test_message_with_code_block_containing_question_mark_is_complete(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            transcript = Path(tmp_dir) / "transcript.jsonl"
            lines = [
                {"step_index": 0, "type": "USER_INPUT", "status": "DONE"},
                {
                    "step_index": 1,
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "content": "Here is regex:\n```python\npattern = r'Task(?:\\s+id)?'\n```\nAll done.",
                },
            ]
            transcript.write_text("\n".join(json.dumps(l) for l in lines), encoding="utf-8")
            self.assertEqual(
                event_to_status(
                    {"terminationReason": "model_stop", "transcriptPath": str(transcript)},
                    explicit_event="Stop",
                ),
                ("complete", False),
            )


if __name__ == "__main__":
    unittest.main()

