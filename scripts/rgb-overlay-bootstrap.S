.syntax unified
.cpu cortex-m0
.thumb

/*
 * Rebuild fallback for the already-verified MF17 image. Future normal QMK
 * builds get this behavior from keyboard_post_init_user() in keymap.c.
 */
.section .text
.global rgb_overlay_bootstrap
.type rgb_overlay_bootstrap, %function
.thumb_func
rgb_overlay_bootstrap:
    push {r4, lr}

    ldr  r3, =0x08004F61 /* rgb_matrix_enable_noeeprom, Thumb */
    blx  r3

    movs r0, #1         /* RGB_MATRIX_SOLID_COLOR */
    movs r1, #0         /* do not write EEPROM */
    ldr  r3, =0x08004FC9 /* rgb_matrix_mode_eeprom_helper, Thumb */
    blx  r3

    movs r0, #0         /* hue */
    movs r1, #0         /* saturation */
    movs r2, #0         /* base brightness */
    movs r3, #0         /* do not write EEPROM */
    ldr  r4, =0x0800505D /* rgb_matrix_sethsv_eeprom_helper, Thumb */
    blx  r4

    pop  {r4, pc}

.align 2
