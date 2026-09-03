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
    /* Legacy LED-index lighting messages retained for bridge compatibility. */
    MSG_SET_PIXEL = 0x22,
    MSG_SET_ALL = 0x23,
    MSG_CLEAR_PIXELS = 0x24,
    /* V2 uses stable physical key IDs and acknowledges every write. */
    MSG_GET_CAPABILITIES = 0x30,
    MSG_CAPABILITIES = 0x31,
    MSG_SET_KEY_RGB = 0x32,
    MSG_SET_KEYS_RGB = 0x33,
    MSG_SET_ALL_KEYS_RGB = 0x34,
    MSG_CLEAR_KEY_RGB = 0x35,
    MSG_CLEAR_ALL_KEY_RGB = 0x36,
    MSG_ACK = 0x37,
    MSG_PING = 0x7E,
    MSG_PONG = 0x7F,
};

enum protocol_status {
    ACK_OK = 0,
    ACK_BAD_LENGTH,
    ACK_BAD_KEY,
    ACK_BAD_ARGUMENT,
    ACK_UNSUPPORTED,
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
#define LIGHTING_PROTOCOL_VERSION 2
#define ACTION_LAYOUT_TEN_AGENT 2
#define LIGHTING_KEY_COUNT 17
#define LIGHTING_MAX_BATCH_KEYS 7
#define CAP_LOGICAL_KEYS (1U << 0)
#define CAP_BATCH_KEYS (1U << 1)
#define CAP_KEY_OVERRIDE (1U << 2)
#define CAP_ACK (1U << 3)
#define LIGHTING_CAPABILITIES (CAP_LOGICAL_KEYS | CAP_BATCH_KEYS | CAP_KEY_OVERRIDE | CAP_ACK)

/*
 * Stable logical lighting IDs in physical reading order. Matrix coordinates,
 * rather than WS2812 indices, keep this API compatible with MF17 revisions
 * that share the KISNT 5x4 switch matrix.
 */
enum lighting_key_id {
    LIGHT_NUM_LOCK = 0,
    LIGHT_DIVIDE,
    LIGHT_MULTIPLY,
    LIGHT_MINUS,
    LIGHT_7,
    LIGHT_8,
    LIGHT_9,
    LIGHT_PLUS,
    LIGHT_4,
    LIGHT_5,
    LIGHT_6,
    LIGHT_1,
    LIGHT_2,
    LIGHT_3,
    LIGHT_0,
    LIGHT_DECIMAL,
    LIGHT_ENTER,
};

static const uint8_t lighting_key_positions[LIGHTING_KEY_COUNT][2] = {
    {0, 0}, {0, 1}, {0, 2}, {0, 3},
    {1, 0}, {1, 1}, {1, 2}, {2, 3},
    {2, 0}, {2, 1}, {2, 2}, {3, 0},
    {3, 1}, {3, 2}, {4, 1}, {4, 2},
    {4, 3},
};

static const uint8_t agent_keys[AGENT_COUNT] = {
    LIGHT_1, LIGHT_2, LIGHT_3, LIGHT_4, LIGHT_5,
    LIGHT_6, LIGHT_7, LIGHT_8, LIGHT_9, LIGHT_0,
};
static uint8_t agent_statuses[AGENT_COUNT] = {
    STATUS_OFF, STATUS_OFF, STATUS_OFF, STATUS_OFF, STATUS_OFF,
    STATUS_OFF, STATUS_OFF, STATUS_OFF, STATUS_OFF, STATUS_OFF,
};
static uint8_t selected_agent = 0xFF;
/* Legacy base pixels are below press/status layers. */
static rgb_t host_pixels[RGB_MATRIX_LED_COUNT] = {0};
static uint32_t host_pixel_mask = 0;
/* V2 logical-key pixels are the final host override for every normal render. */
static rgb_t host_key_pixels[LIGHTING_KEY_COUNT] = {0};
static uint32_t host_key_mask = 0;
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
    /*
     * Keep QMK's effect renderer disabled and drive the WS2812 buffer from
     * housekeeping_task_user(). This makes host/status lighting independent of
     * stale RGB Matrix EEPROM state and of the advanced-indicator callback.
     */
    rgb_matrix_disable_noeeprom();
    lighting_dirty = true;
}

static void send_packet(uint8_t *packet) {
    raw_hid_send(packet, RAW_EPSIZE);
}

static void send_action(uint8_t action) {
    uint8_t packet[RAW_EPSIZE] = {0};
    packet[0] = MSG_ACTION;
    packet[1] = action;
    send_packet(packet);
}

static void send_ack(uint8_t request_id, uint8_t command, uint8_t status) {
    uint8_t packet[RAW_EPSIZE] = {0};
    packet[0] = MSG_ACK;
    packet[1] = request_id;
    packet[2] = command;
    packet[3] = status;
    send_packet(packet);
}

static uint8_t lighting_led_for_key(uint8_t key_id) {
    if (key_id >= LIGHTING_KEY_COUNT) {
        return NO_LED;
    }
    uint8_t row = lighting_key_positions[key_id][0];
    uint8_t col = lighting_key_positions[key_id][1];
    return g_led_config.matrix_co[row][col];
}

const uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {
    /*
     * Layout:
     * Row 0: Num Lock=Approve, /=Reject/Stop, *=Up, -=Down
     * Row 1: 7, 8, 9, (empty)
     * Row 2: 4, 5, 6, New Chat (+)
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
    /* Recovery chord: CX_DOWN ([0][3]) and CX_NEW_CHAT ([2][3]) held for 3 seconds */
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
        reset_keyboard();
    }
}

void raw_hid_receive(uint8_t *data, uint8_t length) {
    if (length < 1) {
        return;
    }

    switch (data[0]) {
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
        case MSG_GET_CAPABILITIES: {
            if (length < 2) {
                break;
            }
            uint8_t packet[RAW_EPSIZE] = {0};
            packet[0] = MSG_CAPABILITIES;
            packet[1] = data[1];
            packet[2] = LIGHTING_PROTOCOL_VERSION;
            packet[3] = LIGHTING_KEY_COUNT;
            packet[4] = (uint8_t)(LIGHTING_CAPABILITIES & 0xFF);
            packet[5] = (uint8_t)(LIGHTING_CAPABILITIES >> 8);
            packet[6] = LIGHTING_MAX_BATCH_KEYS;
            packet[7] = ACTION_LAYOUT_TEN_AGENT;
            send_packet(packet);
            break;
        }
        case MSG_SET_KEY_RGB:
            if (length < 6) {
                if (length >= 2) {
                    send_ack(data[1], data[0], ACK_BAD_LENGTH);
                }
                break;
            }
            if (data[2] >= LIGHTING_KEY_COUNT) {
                send_ack(data[1], data[0], ACK_BAD_KEY);
                break;
            }
            host_key_pixels[data[2]] = (rgb_t){data[3], data[4], data[5]};
            host_key_mask |= ((uint32_t)1 << data[2]);
            lighting_dirty = true;
            send_ack(data[1], data[0], ACK_OK);
            break;
        case MSG_SET_KEYS_RGB: {
            if (length < 3) {
                if (length >= 2) {
                    send_ack(data[1], data[0], ACK_BAD_LENGTH);
                }
                break;
            }
            uint8_t count = data[2];
            if (count == 0 || count > LIGHTING_MAX_BATCH_KEYS || (uint16_t)(3 + (count * 4)) > length) {
                send_ack(data[1], data[0], ACK_BAD_ARGUMENT);
                break;
            }
            bool valid = true;
            for (uint8_t item = 0; item < count; item++) {
                if (data[3 + (item * 4)] >= LIGHTING_KEY_COUNT) {
                    valid = false;
                    break;
                }
            }
            if (!valid) {
                send_ack(data[1], data[0], ACK_BAD_KEY);
                break;
            }
            for (uint8_t item = 0; item < count; item++) {
                uint8_t offset = 3 + (item * 4);
                uint8_t key_id = data[offset];
                host_key_pixels[key_id] = (rgb_t){data[offset + 1], data[offset + 2], data[offset + 3]};
                host_key_mask |= ((uint32_t)1 << key_id);
            }
            lighting_dirty = true;
            send_ack(data[1], data[0], ACK_OK);
            break;
        }
        case MSG_SET_ALL_KEYS_RGB:
            if (length < 5) {
                if (length >= 2) {
                    send_ack(data[1], data[0], ACK_BAD_LENGTH);
                }
                break;
            }
            for (uint8_t key_id = 0; key_id < LIGHTING_KEY_COUNT; key_id++) {
                host_key_pixels[key_id] = (rgb_t){data[2], data[3], data[4]};
                host_key_mask |= ((uint32_t)1 << key_id);
            }
            lighting_dirty = true;
            send_ack(data[1], data[0], ACK_OK);
            break;
        case MSG_CLEAR_KEY_RGB:
            if (length < 3) {
                if (length >= 2) {
                    send_ack(data[1], data[0], ACK_BAD_LENGTH);
                }
                break;
            }
            if (data[2] >= LIGHTING_KEY_COUNT) {
                send_ack(data[1], data[0], ACK_BAD_KEY);
                break;
            }
            host_key_mask &= ~((uint32_t)1 << data[2]);
            lighting_dirty = true;
            send_ack(data[1], data[0], ACK_OK);
            break;
        case MSG_CLEAR_ALL_KEY_RGB:
            if (length < 2) {
                break;
            }
            host_key_mask = 0;
            host_pixel_mask = 0;
            lighting_dirty = true;
            send_ack(data[1], data[0], ACK_OK);
            break;
        case MSG_PING: {
            uint8_t packet[RAW_EPSIZE] = {0};
            packet[0] = MSG_PONG;
            packet[1] = LIGHTING_PROTOCOL_VERSION;
            packet[2] = ACTION_LAYOUT_TEN_AGENT;
            send_packet(packet);
            break;
        }
        default:
            if (data[0] >= MSG_GET_CAPABILITIES && data[0] < MSG_PING && length >= 2) {
                send_ack(data[1], data[0], ACK_UNSUPPORTED);
            }
            break;
    }
}

static rgb_t status_color(uint8_t status) {
    switch (status) {
        case STATUS_IDLE:     return (rgb_t){255, 255, 255};
        case STATUS_THINKING: return (rgb_t){48, 79, 254};
        case STATUS_COMPLETE: return (rgb_t){0, 255, 0};
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

    for (uint8_t slot = 0; slot < AGENT_COUNT; slot++) {
        uint8_t led = lighting_led_for_key(agent_keys[slot]);
        if (led >= RGB_MATRIX_LED_COUNT) {
            continue;
        }
        rgb_t color = status_color(agent_statuses[slot]);
        if (slot == selected_agent) {
            color.r = scale_channel(color.r, pulse);
            color.g = scale_channel(color.g, pulse);
            color.b = scale_channel(color.b, pulse);
        }
        rgb_matrix_set_color(led, color.r, color.g, color.b);
    }

    /* V2 overrides are last so the bridge can control every key, including
     * active Agent keys and control keys with press feedback. */
    for (uint8_t key_id = 0; key_id < LIGHTING_KEY_COUNT; key_id++) {
        if (!(host_key_mask & ((uint32_t)1 << key_id))) {
            continue;
        }
        uint8_t led = lighting_led_for_key(key_id);
        if (led >= RGB_MATRIX_LED_COUNT) {
            continue;
        }
        rgb_t color = host_key_pixels[key_id];
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
