"""Small structured-output helpers used by the CLI wiring layer."""

from __future__ import annotations

import json
from typing import Any

import typer


def emit_event(event: str, *, json_logs: bool, **fields: Any) -> None:
    """Emit one stable structured line in text or JSON form."""
    payload = {"event": event, **fields}
    if json_logs:
        typer.echo(json.dumps(payload, sort_keys=True, default=str))
        return
    rendered_fields = " ".join(
        f"{key}={json.dumps(value, default=str)}" for key, value in fields.items()
    )
    typer.echo(f"{event} {rendered_fields}".rstrip())
