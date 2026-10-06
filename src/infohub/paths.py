"""Re-export paths utilities from infohub_common for backward compatibility."""

from infohub_common.paths import (
    cache_dir,
    config_dir,
    data_dir,
    ensure_dirs,
    fix_permissions,
    resolve_config_path,
    safe_component,
    write_json_atomic,
)

__all__ = [
    "config_dir",
    "data_dir",
    "cache_dir",
    "resolve_config_path",
    "ensure_dirs",
    "safe_component",
    "fix_permissions",
    "write_json_atomic",
]

