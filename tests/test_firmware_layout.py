import re
import unittest
from pathlib import Path


class FirmwareLayoutTests(unittest.TestCase):
    def test_kisnt_oversized_keys_use_physical_matrix_cells(self):
        source = Path("firmware/keymaps/codex_micro/keymap.c").read_text(encoding="utf-8")
        match = re.search(r"\[0\]\s*=\s*\{(.*?)\}\s*,\s*\};", source, re.DOTALL)
        self.assertIsNotNone(match)
        rows = re.findall(r"\{([^{}]+)\}", match.group(1))
        matrix = [re.findall(r"(?:CX_[A-Z0-9_]+|KC_NO)", row) for row in rows]
        self.assertEqual(
            matrix,
            [
                ["CX_APPROVE", "CX_REJECT", "CX_UP", "CX_DOWN"],
                ["CX_AGENT_7", "CX_AGENT_8", "CX_AGENT_9", "KC_NO"],
                ["CX_AGENT_4", "CX_AGENT_5", "CX_AGENT_6", "CX_NEW_CHAT"],
                ["CX_AGENT_1", "CX_AGENT_2", "CX_AGENT_3", "KC_NO"],
                ["KC_NO", "CX_AGENT_0", "CX_DICTATE", "CX_SEND"],
            ],
        )

    def test_agent_leds_match_the_ten_agent_key_positions(self):
        source = Path("firmware/keymaps/codex_micro/keymap.c").read_text(encoding="utf-8")
        match = re.search(r"agent_leds\[AGENT_COUNT\]\s*=\s*\{([^}]+)\}", source)
        self.assertIsNotNone(match)
        self.assertEqual([int(value) for value in re.findall(r"\d+", match.group(1))], [4, 3, 2, 8, 7, 6, 10, 11, 12, 0])

    def test_status_colors_match_codex_micro(self):
        source = Path("firmware/keymaps/codex_micro/keymap.c").read_text(encoding="utf-8")
        expected = {
            "STATUS_IDLE": (40, 40, 40),
            "STATUS_THINKING": (48, 79, 254),
            "STATUS_COMPLETE": (0, 255, 76),
            "STATUS_WAITING": (255, 109, 0),
            "STATUS_ERROR": (255, 0, 51),
        }
        for status, rgb in expected.items():
            channels = r"\s*,\s*".join(str(channel) for channel in rgb)
            self.assertRegex(source, rf"case\s+{status}:\s+return\s+\(rgb_t\)\{{{channels}\}}")

    def test_rgb_rendering_bypasses_qmk_effect_state(self):
        source = Path("firmware/keymaps/codex_micro/keymap.c").read_text(encoding="utf-8")
        match = re.search(r"void\s+keyboard_post_init_user\s*\(void\)\s*\{(.*?)\}", source, re.DOTALL)
        self.assertIsNotNone(match)
        body = match.group(1)
        self.assertIn("rgb_matrix_disable_noeeprom();", body)
        self.assertNotIn("rgb_matrix_enable_noeeprom();", body)
        self.assertRegex(source, r"void\s+housekeeping_task_user\s*\(void\)")
        self.assertRegex(source, r"static\s+void\s+render_lighting\s*\(void\)")
        self.assertIn("rgb_matrix_update_pwm_buffers();", source)
        self.assertNotIn("rgb_matrix_indicators_advanced_user", source)

    def test_non_agent_keys_trigger_white_fade_on_their_physical_led(self):
        source = Path("firmware/keymaps/codex_micro/keymap.c").read_text(encoding="utf-8")
        config = Path("firmware/keymaps/codex_micro/config.h").read_text(encoding="utf-8")

        self.assertRegex(config, r"#define\s+KEY_FEEDBACK_FADE_MS\s+900")
        self.assertIn("keycode >= CX_APPROVE && keycode <= CX_SEND", source)
        self.assertIn(
            "g_led_config.matrix_co[record->event.key.row][record->event.key.col]",
            source,
        )
        self.assertIn("key_feedback_mask |= ((uint32_t)1 << led);", source)
        self.assertIn("timer_elapsed32(key_feedback_timers[led])", source)
        self.assertIn("rgb_matrix_set_color(led, brightness, brightness, brightness);", source)
        self.assertIn("key_feedback_mask != 0", source)


if __name__ == "__main__":
    unittest.main()
