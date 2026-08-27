.syntax unified
.cpu cortex-m0
.thumb

.section .text
.global rgb_overlay_callsite
.type rgb_overlay_callsite, %function
.thumb_func
rgb_overlay_callsite:
    bl 0x0800A298
