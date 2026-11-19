"""Helpers derived from a project manifest (pure functions, no hardware)."""
from typing import Any, Dict


def off_status_defaults(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Status values to publish while the worker is OFF, one per manifest status field.

    bool -> False, bool_map -> {} (filled with cameras by MQTTControl), enum ->
    "OFF" for program_status else "", number -> None, text -> "".
    """
    out: Dict[str, Any] = {}
    for field in (manifest or {}).get("status", []) or []:
        if not isinstance(field, dict) or not field.get("key"):
            continue
        key, ftype = field["key"], field.get("type", "text")
        if key == "program_status":
            out[key] = "OFF"
        elif ftype == "bool":
            out[key] = False
        elif ftype == "bool_map":
            out[key] = {}
        elif ftype == "number":
            out[key] = None
        else:
            out[key] = ""
    out.setdefault("program_status", "OFF")
    return out
