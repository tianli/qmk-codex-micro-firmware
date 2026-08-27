#pragma once

#define RAW_USAGE_PAGE 0xFF60
#define RAW_USAGE_ID 0x61
#define RAW_EPSIZE 32

/* Hold the physical - and + positions together to enter the DFU bootloader. */
#define RECOVERY_HOLD_MS 3000

/* Non-Agent keys flash white and fade after each press. */
#define KEY_FEEDBACK_FADE_MS 900

#define RGB_MATRIX_DEFAULT_MODE RGB_MATRIX_SOLID_COLOR
#define RGB_MATRIX_DEFAULT_HUE 128
#define RGB_MATRIX_DEFAULT_SAT 255
#define RGB_MATRIX_DEFAULT_VAL 72
#define RGB_MATRIX_TIMEOUT 0
