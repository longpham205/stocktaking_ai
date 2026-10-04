"""Request and response bodies of the advanced-settings routes (field names are the legacy API's)."""

from typing import Any

from pydantic import BaseModel


class ConfigOut(BaseModel):
    # one per registry entry: key, label, group, tier, type, help, min, max, default, value, overridden
    items: list[dict[str, Any]]
    # a pipeline reload is running
    reloading: bool
    # the stored overrides no longer fit the YAML (shown, never silently dropped)
    config_error: str | None
    advanced_password_set: bool
    # the engine YAML's file name
    pipeline_config: str
    # after an apply: whether anything changed
    applied: bool | None = None


class ApplyIn(BaseModel):
    """`changes`: {dotted key: value}; a value of null goes back to the YAML's."""

    changes: Any = None
    confirm: Any = None
    advanced_password: str | None = None
