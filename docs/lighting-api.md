# Per-key lighting API

Firmware protocol v2 lets the bridge address all 17 physical keys by a stable
logical key ID. It uses the existing Raw HID interface (`0xFF60:0x61`) and
keeps the v1 LED-index messages working for older bridges.

## Bridge API

Send one JSON object over UDP to `127.0.0.1:41717`, or import
`host.lighting_api.LightingClient`.

```json
{"kind":"lighting","key":"agent_1","rgb":[255,0,0]}
{"kind":"lighting","keys":{"approve":[0,255,0],"send":[0,0,255]}}
{"kind":"lighting","all":[32,0,80]}
{"kind":"lighting","clear_key":"send"}
{"kind":"lighting","clear":true}
```

The command-line client exposes the same operations:

```bash
python scripts/set-lighting.py key agent_1 255 0 0
python scripts/set-lighting.py all 32 0 80
python scripts/set-lighting.py clear agent_1
python scripts/set-lighting.py clear
python scripts/set-lighting.py list
```

Canonical key IDs follow physical reading order. Action names are accepted as
aliases, so the bridge does not need to know QMK's LED chain order.

| ID | Physical key | Action aliases |
|---:|---|---|
| 0 | `num_lock` | `approve` |
| 1 | `divide` | `reject` |
| 2 | `multiply` | `up` |
| 3 | `minus` | `down` |
| 4 | `7` | `agent_7` |
| 5 | `8` | `agent_8` |
| 6 | `9` | `agent_9` |
| 7 | `plus` | `new_chat` |
| 8 | `4` | `agent_4` |
| 9 | `5` | `agent_5` |
| 10 | `6` | `agent_6` |
| 11 | `1` | `agent_1` |
| 12 | `2` | `agent_2` |
| 13 | `3` | `agent_3` |
| 14 | `0` | `agent_0` |
| 15 | `decimal` | `dictate` |
| 16 | `enter` | `send` |

V2 colors are a deliberate final override: they can replace an Agent status
color or press-fade color. `clear_key` restores the normal status/feedback
layer for that key, and `clear` restores it for all keys. The three-channel
values are clamped to `0..255`.

## Raw HID v2

Every report is 32 bytes. Bytes not listed below are zero. Requests use a
nonzero request ID chosen by the host; write commands return `ACK` with the
same ID.

| Command | ID | Request bytes after ID | Response |
|---|---:|---|---|
| `GET_CAPABILITIES` | `0x30` | request ID | `0x31, request ID, version, key count, capability lo, capability hi, max batch` |
| `SET_KEY_RGB` | `0x32` | request ID, key ID, R, G, B | `ACK` |
| `SET_KEYS_RGB` | `0x33` | request ID, count, then count × (key ID, R, G, B) | `ACK` |
| `SET_ALL_KEYS_RGB` | `0x34` | request ID, R, G, B | `ACK` |
| `CLEAR_KEY_RGB` | `0x35` | request ID, key ID | `ACK` |
| `CLEAR_ALL_KEY_RGB` | `0x36` | request ID | `ACK` |
| `ACK` | `0x37` | request ID, original command, status | — |

`SET_KEYS_RGB` accepts at most seven keys per report. ACK status values are
`0` success, `1` bad length, `2` bad key, `3` bad argument, and `4`
unsupported command.

The bridge probes `GET_CAPABILITIES` on connection. If it receives no v2
response, logical-key operations are translated to the legacy `SET_PIXEL`,
`SET_ALL`, and `CLEAR_PIXELS` messages automatically.
