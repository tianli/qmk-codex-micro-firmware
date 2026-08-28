"""Compatibility shim for codex_status_hook, delegating to jetski_status_hook."""

from __future__ import annotations

import sys
from host.jetski_status_hook import event_to_status, main, _contains_error

__all__ = ["event_to_status", "main", "_contains_error"]


if __name__ == "__main__":
    raise SystemExit(main())
