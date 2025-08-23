from copy import deepcopy


def str_to_bool(value: str | bool | int | None) -> bool:
    """Convert common truthy / falsy strings and values to `bool`."""

    truthy_values = {"true", "1", "yes", "y", "t", "on"}
    falsy_values = {"false", "0", "no", "n", "f", "off"}

    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if not isinstance(value, str):
        raise ValueError(f"Cannot convert '{value}' to a boolean.")

    value = value.strip().lower()

    if value in truthy_values:
        return True
    if value in falsy_values:
        return False
    raise ValueError(f"Cannot convert '{value}' to a boolean.")


def deep_merge(base: dict, override: dict) -> dict:
    out = deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(out.get(k), dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out
