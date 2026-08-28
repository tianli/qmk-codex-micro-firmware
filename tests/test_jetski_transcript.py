import json
from pathlib import Path
import tempfile
import unittest

from host.mf17_bridge import latest_transcript_state


class JetskiTranscriptTests(unittest.TestCase):
    def test_planner_response_done_is_complete(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            p = Path(tmp_dir) / "transcript.jsonl"
            lines = [
                {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE"},
                {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE", "tool_calls": []},
            ]
            p.write_text("\n".join(json.dumps(l) for l in lines), encoding="utf-8")
            self.assertEqual(latest_transcript_state(p), "complete")

    def test_user_input_is_active(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            p = Path(tmp_dir) / "transcript.jsonl"
            lines = [
                {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE"},
            ]
            p.write_text("\n".join(json.dumps(l) for l in lines), encoding="utf-8")
            self.assertIsNone(latest_transcript_state(p))

    def test_error_status_is_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            p = Path(tmp_dir) / "transcript.jsonl"
            lines = [
                {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE"},
                {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "ERROR"},
            ]
            p.write_text("\n".join(json.dumps(l) for l in lines), encoding="utf-8")
            self.assertEqual(latest_transcript_state(p), "error")

    def test_waiting_tool_call_is_waiting(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            p = Path(tmp_dir) / "transcript.jsonl"
            lines = [
                {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE"},
                {
                    "step_index": 1,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "tool_calls": [{"name": "ask_question", "args": {}}],
                },
            ]
            p.write_text("\n".join(json.dumps(l) for l in lines), encoding="utf-8")
            self.assertEqual(latest_transcript_state(p), "waiting")


if __name__ == "__main__":
    unittest.main()
