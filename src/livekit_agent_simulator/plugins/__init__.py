"""Scenario plugins — register verify hooks and load them from JSONL / target `.agent-sim/plugins/`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .api import (
    AfterRunContext,
    AfterRunHook,
    BeforeRunContext,
    BeforeRunHook,
    VerifyContext,
    VerifyResult,
)
from .loader import ENTRY_POINT_GROUP, ensure_plugins_loaded, plugins_dir
from .registry import (
    get_verify,
    list_after_run_hooks,
    list_before_run_hooks,
    list_verify_plugins,
    register_after_run,
    register_before_run,
    register_setup,
    register_verify,
    reset_for_tests,
)

if TYPE_CHECKING:
    from .api import VerifyPlugin


def verify_plugin(name: str):
    """Decorator: `@verify_plugin("my_check")` registers a verify hook."""

    def decorator(fn: VerifyPlugin) -> VerifyPlugin:
        return register_verify(name, fn)

    return decorator


__all__ = [
    "AfterRunContext",
    "AfterRunHook",
    "BeforeRunContext",
    "BeforeRunHook",
    "ENTRY_POINT_GROUP",
    "VerifyContext",
    "VerifyResult",
    "ensure_plugins_loaded",
    "get_verify",
    "list_after_run_hooks",
    "list_before_run_hooks",
    "list_verify_plugins",
    "plugins_dir",
    "register_after_run",
    "register_before_run",
    "register_setup",
    "register_verify",
    "reset_for_tests",
    "verify_plugin",
]
