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
    CX_AGENT_7,
    CX_AGENT_8,
    CX_AGENT_9,
    CX_AGENT_0,
    CX_APPROVE,
    CX_REJECT,
    CX_UP,
    CX_DOWN,
    CX_NEW_CHAT,
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
    MSG_BOOTLOADER = 0xAA,
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

#define AGENT_COUNT 10

/*
 * QMK LED indices for digits 1-9 and 0 on the MF17 matrix:
 * Slot 0: Key 1 -> LED 4
 * Slot 1: Key 2 -> LED 3
 * Slot 2: Key 3 -> LED 2
 * Slot 3: Key 4 -> LED 8
 * Slot 4: Key 5 -> LED 7
 * Slot 5: Key 6 -> LED 6
 * Slot 6: Key 7 -> LED 10
 * Slot 7: Key 8 -> LED 11
 * Slot 8: Key 9 -> LED 12
 * Slot 9: Key 0 -> LED 0
 */
static const uint8_t agent_leds[AGENT_COUNT] = {4, 3, 2, 8, 7, 6, 10, 11, 12, 0};
static uint8_t agent_statuses[AGENT_COUNT] = {
    STATUS_OFF, STATUS_OFF, STATUS_OFF, STATUS_OFF, STATUS_OFF,
    STATUS_OFF, STATUS_OFF, STATUS_OFF, STATUS_OFF, STATUS_OFF,
};
static uint8_t selected_agent = 0xFF;
static rgb_t host_pixels[RGB_MATRIX_LED_COUNT] = {0};
static uint32_t host_pixel_mask = 0;
static uint32_t key_feedback_mask = 0;
static uint32_t key_feedback_timers[RGB_MATRIX_LED_COUNT] = {0};
static bool lighting_dirty = true;
static bool lighting_was_suspended = false;
static uint32_t lighting_render_timer = 0;
static bool recovery_top_right_down = false;
static bool recovery_plus_down = false;
static bool recovery_chord_active = false;
static uint32_t recovery_chord_timer = 0;

void keyboard_post_init_user(void) {
    rgb_matrix_disable_noeeprom();
    for (uint8_t led = 0; led < RGB_MATRIX_LED_COUNT; led++) {
        rgb_matrix_set_color(led, 0, 0, 0);
    }
    rgb_matrix_update_pwm_buffers();
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
     * Physical Layout:
     * Row 0: Approve, Reject, Up, Down (Top-Right / physical -)
     * Row 1: 7, 8, 9, (empty)
     * Row 2: 4, 5, 6, New Chat (physical +)
     * Row 3: 1, 2, 3, (empty)
     * Row 4: (empty), 0, Dictate (.), Send (Enter)
     */
    [0] = {
        { CX_APPROVE, CX_REJECT,  CX_UP,      CX_DOWN     },
        { CX_AGENT_7, CX_AGENT_8, CX_AGENT_9, KC_NO       },
        { CX_AGENT_4, CX_AGENT_5, CX_AGENT_6, CX_NEW_CHAT },
        { CX_AGENT_1, CX_AGENT_2, CX_AGENT_3, KC_NO       },
        { KC_NO,      CX_AGENT_0, CX_DICTATE, CX_SEND     },
    },
};

bool process_record_user(uint16_t keycode, keyrecord_t *record) {
    /* Recovery chord: CX_DOWN ([0][3], top-right) and CX_NEW_CHAT ([2][3], middle-right +) */
    if (keycode == CX_DOWN || keycode == CX_NEW_CHAT) {
        if (keycode == CX_DOWN) {
            recovery_top_right_down = record->event.pressed;
        } else {
            recovery_plus_down = record->event.pressed;
        }

        if (recovery_top_right_down && recovery_plus_down) {
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

    /* Non-Agent keys flash white and fade after each press */
    if (record->event.pressed && keycode >= CX_APPROVE && keycode <= CX_SEND) {
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

    if (keycode >= CX_AGENT_1 && keycode <= CX_AGENT_0) {
        selected_agent = (uint8_t)(keycode - CX_AGENT_1);
        lighting_dirty = true;
    }

    return false;
}

void matrix_scan_user(void) {
    if (recovery_chord_active && timer_elapsed32(recovery_chord_timer) >= RECOVERY_HOLD_MS) {
        recovery_chord_active = false;
        bootloader_jump();
    }
}

void raw_hid_receive(uint8_t *data, uint8_t length) {
    if (length < 2) {
        return;
    }

    switch (data[0]) {
        case MSG_BOOTLOADER:
            bootloader_jump();
            break;
        case MSG_SET_STATUS:
            if (length >= 3 && data[1] < AGENT_COUNT && data[2] <= STATUS_ERROR) {
                agent_statuses[data[1]] = data[2];
                lighting_dirty = true;
            }
            break;
        case MSG_SET_SELECTED:
            selected_agent = data[1] < AGENT_COUNT ? data[1] : 0xFF;
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
        case STATUS_IDLE:     return (rgb_t){40, 40, 40};    /* Soft dim white for idle */
        case STATUS_THINKING: return (rgb_t){48, 79, 254};   /* Blue */
        case STATUS_COMPLETE: return (rgb_t){0, 255, 76};    /* Green */
        case STATUS_WAITING:  return (rgb_t){255, 109, 0};   /* Amber */
        case STATUS_ERROR:    return (rgb_t){255, 0, 51};     /* Red */
        default:              return (rgb_t){0, 0, 0};       /* Off */
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

    for (uint8_t slot = 0; slot < AGENT_COUNT; slot++) {
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
