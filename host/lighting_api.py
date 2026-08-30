"""Versioned per-key lighting API shared by the MF17 bridge and clients.

The bridge accepts these operations as local UDP JSON packets.  Firmware v2
uses stable logical key IDs; firmware v1 falls back to the original LED-index
messages so updated bridge installations can still control older images.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import socket
from typing import Any


UDP_HOST = "127.0.0.1"
UDP_PORT = 41717
PROTOCOL_VERSION = 2
KEY_COUNT = 17
MAX_BATCH_KEYS = 7

# Legacy, LED-index-based messages.  These values cannot be changed without
# breaking existing host bridges.
MSG_SET_PIXEL = 0x22
MSG_SET_ALL = 0x23
MSG_CLEAR_PIXELS = 0x24

# Versioned logical-key protocol.
MSG_GET_CAPABILITIES = 0x30
MSG_CAPABILITIES = 0x31
MSG_SET_KEY_RGB = 0x32
MSG_SET_KEYS_RGB = 0x33
MSG_SET_ALL_KEYS_RGB = 0x34
MSG_CLEAR_KEY_RGB = 0x35
MSG_CLEAR_ALL_KEY_RGB = 0x36
MSG_ACK = 0x37

ACK_OK = 0
ACK_BAD_LENGTH = 1
ACK_BAD_KEY = 2
ACK_BAD_ARGUMENT = 3
ACK_UNSUPPORTED = 4

CAP_LOGICAL_KEYS = 1 << 0
CAP_BATCH_KEYS = 1 << 1
CAP_KEY_OVERRIDE = 1 << 2
CAP_ACK = 1 << 3
CAPABILITIES = CAP_LOGICAL_KEYS | CAP_BATCH_KEYS | CAP_KEY_OVERRIDE | CAP_ACK

# Logical IDs follow physical reading order, not QMK's WS2812 chain order.
# Every alias identifies the same physical switch and LED.
KEY_NAMES = (
    "num_lock",
    "divide",
    "multiply",
    "minus",
    "7",
    "8",
    "9",
    "plus",
    "4",
    "5",
    "6",
    "1",
    "2",
    "3",
    "0",
    "decimal",
    "enter",
)

KEY_ALIASES = {
    "num": "num_lock",
    "numlock": "num_lock",
    "approve": "num_lock",
    "/": "divide",
    "reject": "divide",
    "*": "multiply",
    "up": "multiply",
    "-": "minus",
    "down": "minus",
    "+": "plus",
    "new_chat": "plus",
    ".": "decimal",
    "dictate": "decimal",
    "return": "enter",
    "send": "enter",
    **{f"agent_{digit}": digit for digit in "1234567890"},
}

# Used only when talking to legacy firmware.  V2 resolves IDs in firmware from
# the matrix, so it does not depend on this WS2812 chain ordering.
LEGACY_LED_BY_KEY = (13, 14, 15, 16, 10, 11, 12, 9, 6, 7, 8, 3, 4, 5, 0, 1, 2)


def resolve_key(key: str | int) -> int:
    """Return a stable 0-based logical key ID."""
    if isinstance(key, bool):
        raise ValueError("a boolean is not a key ID")
    if isinstance(key, int):
        if 0 <= key < KEY_COUNT:
            return key
        raise ValueError(f"key ID must be between 0 and {KEY_COUNT - 1}")

    normalized = str(key).strip().lower().replace(" ", "_").replace("-", "_")
    normalized = KEY_ALIASES.get(normalized, normalized)
    try:
        return KEY_NAMES.index(normalized)
    except ValueError as exc:
        names = ", ".join(KEY_NAMES)
        raise ValueError(f"unknown key {key!r}; expected one of: {names}") from exc


def normalize_rgb(rgb: Sequence[Any]) -> tuple[int, int, int]:
    if isinstance(rgb, (str, bytes)) or len(rgb) != 3:
        raise ValueError("rgb must contain exactly three channels")
    try:
        channels = tuple(max(0, min(255, int(value))) for value in rgb)
    except (TypeError, ValueError) as exc:
        raise ValueError("rgb channels must be integers") from exc
    return channels  # type: ignore[return-value]


class LightingReportEncoder:
    """Encode JSON API operations into v2 or backward-compatible HID reports."""

    def __init__(self, protocol_version: int = 1) -> None:
        self.protocol_version = protocol_version
        self._request_id = 0

    def _next_request_id(self) -> int:
        self._request_id = (self._request_id % 255) + 1
        return self._request_id

    def _v2(self, command: int, *values: int) -> list[int]:
        return [command, self._next_request_id(), *values]

    @staticmethod
    def _items(value: Any) -> list[tuple[int, tuple[int, int, int]]]:
        if isinstance(value, Mapping):
            return [(resolve_key(key), normalize_rgb(rgb)) for key, rgb in value.items()]
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            result: list[tuple[int, tuple[int, int, int]]] = []
            for item in value:
                if not isinstance(item, Mapping) or "key" not in item or "rgb" not in item:
                    raise ValueError("each keys item must contain key and rgb")
                result.append((resolve_key(item["key"]), normalize_rgb(item["rgb"])))
            return result
        raise ValueError("keys must be an object or a list of {key, rgb} objects")

    def encode(self, payload: Mapping[str, Any]) -> list[list[int]]:
        if payload.get("clear") is True or payload.get("clear_all") is True:
            if self.protocol_version >= PROTOCOL_VERSION:
                return [self._v2(MSG_CLEAR_ALL_KEY_RGB)]
            return [[MSG_CLEAR_PIXELS]]

        if "clear_key" in payload:
            key_id = resolve_key(payload["clear_key"])
            if self.protocol_version >= PROTOCOL_VERSION:
                return [self._v2(MSG_CLEAR_KEY_RGB, key_id)]
            # Legacy firmware cannot remove one mask bit. Black is the closest
            # compatible behavior for its otherwise-off base layer.
            return [[MSG_SET_PIXEL, LEGACY_LED_BY_KEY[key_id], 0, 0, 0]]

        if "all" in payload:
            rgb = normalize_rgb(payload["all"])
            if self.protocol_version >= PROTOCOL_VERSION:
                return [self._v2(MSG_SET_ALL_KEYS_RGB, *rgb)]
            return [[MSG_SET_ALL, *rgb]]

        if "key" in payload and "rgb" in payload:
            key_id = resolve_key(payload["key"])
            rgb = normalize_rgb(payload["rgb"])
            if self.protocol_version >= PROTOCOL_VERSION:
                return [self._v2(MSG_SET_KEY_RGB, key_id, *rgb)]
            return [[MSG_SET_PIXEL, LEGACY_LED_BY_KEY[key_id], *rgb]]

        if "keys" in payload:
            items = self._items(payload["keys"])
            if not items:
                return []
            if self.protocol_version < PROTOCOL_VERSION:
                return [
                    [MSG_SET_PIXEL, LEGACY_LED_BY_KEY[key_id], *rgb]
                    for key_id, rgb in items
                ]

            reports: list[list[int]] = []
            for offset in range(0, len(items), MAX_BATCH_KEYS):
                batch = items[offset : offset + MAX_BATCH_KEYS]
                values: list[int] = [len(batch)]
                for key_id, rgb in batch:
                    values.extend((key_id, *rgb))
                reports.append(self._v2(MSG_SET_KEYS_RGB, *values))
            return reports

        # Keep the original low-level pixel operation for diagnostic tools.
        if "pixel" in payload and "rgb" in payload:
            pixel = int(payload["pixel"])
            if not 0 <= pixel < KEY_COUNT:
                raise ValueError(f"pixel must be between 0 and {KEY_COUNT - 1}")
            return [[MSG_SET_PIXEL, pixel, *normalize_rgb(payload["rgb"])]]

        raise ValueError("lighting request contains no supported operation")


class LightingClient:
    """Small localhost client for scripts that want to control the bridge."""

    def __init__(self, host: str = UDP_HOST, port: int = UDP_PORT) -> None:
        self.address = (host, port)

    def _send(self, operation: Mapping[str, Any]) -> None:
        payload = {"kind": "lighting", **operation}
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(encoded, self.address)

    def set_key(self, key: str | int, rgb: Sequence[int]) -> None:
        self._send({"key": key, "rgb": normalize_rgb(rgb)})

    def set_keys(self, keys: Mapping[str | int, Sequence[int]]) -> None:
        self._send({"keys": keys})

    def set_all(self, rgb: Sequence[int]) -> None:
        self._send({"all": normalize_rgb(rgb)})

    def clear_key(self, key: str | int) -> None:
        self._send({"clear_key": key})

    def clear_all(self) -> None:
        self._send({"clear": True})
