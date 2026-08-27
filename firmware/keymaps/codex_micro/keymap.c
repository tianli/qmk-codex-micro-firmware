/*
 * Copyright 2026
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include QMK_KEYBOARD_H
#include "raw_hid.h"

enum custom_keycodes {
    CX_AGENT_1 = SAFE_RANGE,
    CX_AGENT_2,
    CX_AGENT_3,
    CX_AGENT_4,
    CX_AGENT_5,
    CX_AGENT_6,
    CX_UP,
    CX_RIGHT,
    CX_DOWN,
    CX_LEFT,
    CX_CENTER,
    CX_FAST,
    CX_APPROVE,
    CX_DECLINE,
    CX_CONTINUE,
    CX_DICTATE,
    CX_SEND,
};

enum protocol_messages {
    MSG_ACTION = 0x10,
    MSG_SET_STATUS = 0x20,
    MSG_SET_SELECTED = 0x21,
    MSG_SET_PIXEL = 0x22,
    MSG_SET_ALL = 0x23,
    MSG_CLEAR_PIXELS = 0x24,
    MSG_PING = 0x7E,
    MSG_PONG = 0x7F,
};

enum agent_status {
    STATUS_OFF = 0,
    STATUS_IDLE,
    STATUS_THINKING,
    STATUS_COMPLETE,
    STATUS_WAITING,
    STATUS_ERROR,
};

/* QMK LED indices for Num, /, *, -, 7 and 9 on the upstream MF17 matrix. */
static const uint8_t agent_leds[6] = {13, 14, 15, 16, 10, 12};
static uint8_t agent_statuses[6] = {
    STATUS_OFF, STATUS_OFF, STATUS_OFF, STATUS_OFF, STATUS_OFF, STATUS_OFF,
};
static uint8_t selected_agent = 0xFF;
static rgb_t host_pixels[RGB_MATRIX_LED_COUNT] = {0};
static uint32_t host_pixel_mask = 0;
static uint32_t key_feedback_mask = 0;
static uint32_t key_feedback_timers[RGB_MATRIX_LED_COUNT] = {0};
static bool lighting_dirty = true;
static bool lighting_was_suspended = false;
static uint32_t lighting_render_timer = 0;
static bool recovery_minus_down = false;
static bool recovery_plus_down = false;
static bool recovery_chord_active = false;
static uint32_t recovery_chord_timer = 0;

void keyboard_post_init_user(void) {
    /*
     * Keep QMK's effect renderer disabled and drive the WS2812 buffer from
     * housekeeping_task_user(). This makes host/status lighting independent of
     * stale RGB Matrix EEPROM state and of the advanced-indicator callback.
     */
    rgb_matrix_disable_noeeprom();
    lighting_dirty = true;
}

static void send_action(uint8_t action) {
    uint8_t packet[RAW_EPSIZE] = {0};
    packet[0] = MSG_ACTION;
    packet[1] = action;
    raw_hid_send(packet, sizeof(packet));
}

const uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {
    /*
     * The KISNT-branded MF17 uses the lower/right matrix cells for its
     * oversized +, 0, and Enter switches.  Upstream LAYOUT_numpad_5x4 maps
     * those labels to [1][3], [4][0], and [3][3], which are electrically
     * empty on this unit.  Use the physical 5x4 matrix directly instead.
     */
    [0] = {
        { CX_AGENT_1, CX_AGENT_2, CX_AGENT_3, CX_AGENT_4 },
        { CX_AGENT_5, CX_UP,      CX_AGENT_6, KC_NO      },
        { CX_LEFT,    CX_CENTER,  CX_RIGHT,   CX_FAST    },
        { CX_APPROVE, CX_DOWN,    CX_DECLINE, KC_NO      },
        { KC_NO,      CX_CONTINUE,CX_DICTATE, CX_SEND    },
    },
};

bool process_record_user(uint16_t keycode, keyrecord_t *record) {
    /* CX_AGENT_4 and CX_FAST occupy the physical - and + key positions. */
    if (keycode == CX_AGENT_4 || keycode == CX_FAST) {
        if (keycode == CX_AGENT_4) {
            recovery_minus_down = record->event.pressed;
        } else {
            recovery_plus_down = record->event.pressed;
        }

        if (recovery_minus_down && recovery_plus_down) {
            if (!recovery_chord_active) {
                recovery_chord_active = true;
                recovery_chord_timer = timer_read32();
                lighting_dirty = true;
            }
        } else {
            recovery_chord_active = false;
            lighting_dirty = true;
        }
    }

    if (record->event.pressed && keycode >= CX_UP && keycode <= CX_SEND) {
        uint8_t led = g_led_config.matrix_co[record->event.key.row][record->event.key.col];
        if (led < RGB_MATRIX_LED_COUNT) {
            key_feedback_timers[led] = timer_read32();
            key_feedback_mask |= ((uint32_t)1 << led);
            lighting_dirty = true;
        }
    }

    if (!record->event.pressed || keycode < CX_AGENT_1 || keycode > CX_SEND) {
        return keycode < CX_AGENT_1 || keycode > CX_SEND;
    }

    send_action((uint8_t)(keycode - CX_AGENT_1 + 1));

    if (keycode >= CX_AGENT_1 && keycode <= CX_AGENT_6) {
        selected_agent = (uint8_t)(keycode - CX_AGENT_1);
        lighting_dirty = true;
    }

    return false;
}

void matrix_scan_user(void) {
    if (recovery_chord_active && timer_elapsed32(recovery_chord_timer) >= RECOVERY_HOLD_MS) {
        recovery_chord_active = false;
        reset_keyboard();
    }
}

void raw_hid_receive(uint8_t *data, uint8_t length) {
    if (length < 2) {
        return;
    }

    switch (data[0]) {
        case MSG_SET_STATUS:
            if (length >= 3 && data[1] < 6 && data[2] <= STATUS_ERROR) {
                agent_statuses[data[1]] = data[2];
                lighting_dirty = true;
            }
            break;
        case MSG_SET_SELECTED:
            selected_agent = data[1] < 6 ? data[1] : 0xFF;
            lighting_dirty = true;
            break;
        case MSG_SET_PIXEL:
            if (length >= 5 && data[1] < RGB_MATRIX_LED_COUNT) {
                host_pixels[data[1]] = (rgb_t){data[2], data[3], data[4]};
                host_pixel_mask |= ((uint32_t)1 << data[1]);
                lighting_dirty = true;
            }
            break;
        case MSG_SET_ALL:
            if (length >= 4) {
                for (uint8_t led = 0; led < RGB_MATRIX_LED_COUNT; led++) {
                    host_pixels[led] = (rgb_t){data[1], data[2], data[3]};
                    host_pixel_mask |= ((uint32_t)1 << led);
                }
                lighting_dirty = true;
            }
            break;
        case MSG_CLEAR_PIXELS:
            host_pixel_mask = 0;
            lighting_dirty = true;
            break;
        case MSG_PING: {
            uint8_t packet[RAW_EPSIZE] = {0};
            packet[0] = MSG_PONG;
            packet[1] = 1;
            raw_hid_send(packet, sizeof(packet));
            break;
        }
    }
}

static rgb_t status_color(uint8_t status) {
    switch (status) {
        case STATUS_IDLE:     return (rgb_t){255, 255, 255};
        case STATUS_THINKING: return (rgb_t){48, 79, 254};
        case STATUS_COMPLETE: return (rgb_t){0, 255, 76};
        case STATUS_WAITING:  return (rgb_t){255, 109, 0};
        case STATUS_ERROR:    return (rgb_t){255, 0, 51};
        default:              return (rgb_t){0, 0, 0};
    }
}

static uint8_t scale_channel(uint8_t value, uint8_t factor) {
    return (uint8_t)(((uint16_t)value * factor) / 255);
}

static void render_lighting(void) {
    if (recovery_chord_active) {
        uint32_t elapsed = timer_elapsed32(recovery_chord_timer);
        if (elapsed > RECOVERY_HOLD_MS) {
            elapsed = RECOVERY_HOLD_MS;
        }
        uint8_t brightness = (uint8_t)(32 + ((elapsed * 223) / RECOVERY_HOLD_MS));

        for (uint8_t led = 0; led < RGB_MATRIX_LED_COUNT; led++) {
            rgb_matrix_set_color(led, 0, brightness / 4, brightness);
        }
        rgb_matrix_update_pwm_buffers();
        return;
    }

    uint8_t phase = (uint8_t)(timer_read32() >> 3);
    uint8_t triangle = phase & 0x80 ? (uint8_t)((255 - phase) << 1) : (uint8_t)(phase << 1);
    uint8_t pulse = scale_channel(triangle, 110) + 145;

    for (uint8_t led = 0; led < RGB_MATRIX_LED_COUNT; led++) {
        if (host_pixel_mask & ((uint32_t)1 << led)) {
            rgb_t color = host_pixels[led];
            rgb_matrix_set_color(led, color.r, color.g, color.b);
        } else {
            rgb_matrix_set_color(led, 0, 0, 0);
        }
    }

    /* A non-Agent press temporarily overrides its base pixel with fading white. */
    for (uint8_t led = 0; led < RGB_MATRIX_LED_COUNT; led++) {
        uint32_t led_mask = (uint32_t)1 << led;
        if (!(key_feedback_mask & led_mask)) {
            continue;
        }

        uint32_t elapsed = timer_elapsed32(key_feedback_timers[led]);
        if (elapsed >= KEY_FEEDBACK_FADE_MS) {
            key_feedback_mask &= ~led_mask;
            continue;
        }

        uint8_t brightness = (uint8_t)(((KEY_FEEDBACK_FADE_MS - elapsed) * 255) / KEY_FEEDBACK_FADE_MS);
        rgb_matrix_set_color(led, brightness, brightness, brightness);
    }

    for (uint8_t slot = 0; slot < 6; slot++) {
        uint8_t led = agent_leds[slot];
        rgb_t color = status_color(agent_statuses[slot]);
        if (slot == selected_agent) {
            color.r = scale_channel(color.r, pulse);
            color.g = scale_channel(color.g, pulse);
            color.b = scale_channel(color.b, pulse);
        }
        rgb_matrix_set_color(led, color.r, color.g, color.b);
    }

    rgb_matrix_update_pwm_buffers();
}

void housekeeping_task_user(void) {
    if (rgb_matrix_get_suspend_state()) {
        lighting_was_suspended = true;
        return;
    }

    if (lighting_was_suspended) {
        lighting_was_suspended = false;
        lighting_dirty = true;
    }

    bool animated = recovery_chord_active || selected_agent != 0xFF || key_feedback_mask != 0;
    if ((!lighting_dirty && !animated) || timer_elapsed32(lighting_render_timer) < 16) {
        return;
    }

    lighting_render_timer = timer_read32();
    render_lighting();
    lighting_dirty = false;
}
