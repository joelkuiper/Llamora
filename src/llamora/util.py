from copy import deepcopy
from pathlib import Path


def _repo_root() -> Path:
    module_path = Path(__file__).resolve()
    # util.py lives under src/llamora/, so the repository root is two levels up.
    return module_path.parents[2]


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


def resolve_data_path(
    configured_path: str,
    *,
    fallback_dir: str | Path,
    fallback_name: str | None = None,
) -> Path:
    """Resolve a configurable data path with sensible fallbacks.

    The resolution order is:
    1. Absolute path as provided.
    2. Relative to the current working directory (e.g., running scripts from the repo root).
    3. Relative to the repository root (for tools that change CWD).
    4. The packaged fallback directory, using the provided ``fallback_name`` or the original
       filename when the path cannot be located elsewhere.
    """

    candidate = Path(configured_path)
    fallback_dir_path = Path(fallback_dir).resolve()
    repo_root = _repo_root()

    search_paths: list[Path] = []

    if candidate.is_absolute():
        search_paths.append(candidate)
    else:
        search_paths.extend(
            [
                Path.cwd() / candidate,
                repo_root / candidate,
            ]
        )

    resolved_fallback_name = fallback_name or candidate.name
    if resolved_fallback_name:
        search_paths.append(fallback_dir_path / resolved_fallback_name)

    tried: list[Path] = []
    seen: set[Path] = set()

    for path in search_paths:
        normalized = path.resolve()
        if normalized in seen:
            continue
        seen.add(normalized)
        tried.append(normalized)
        if normalized.exists():
            return normalized

    raise FileNotFoundError(
        f"Unable to locate '{configured_path}'. Checked: {', '.join(str(p) for p in tried)}"
    )
