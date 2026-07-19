"""In-process plugin registry for verify hooks and lifecycle callbacks."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .api import (
        AfterRunContext,
        AfterRunHook,
        BeforeRunContext,
        BeforeRunHook,
        SetupFn,
        VerifyPlugin,
    )

_verify: dict[str, VerifyPlugin] = {}
_setup_fns: list[SetupFn] = []
_before_run: list[BeforeRunHook] = []
_after_run: list[AfterRunHook] = []
_loaded_keys: set[str] = set()


def register_verify(name: str, fn: VerifyPlugin) -> VerifyPlugin:
    """Register a verify plugin callable under a stable name (referenced from JSONL)."""
    key = name.strip()
    if not key:
        raise ValueError("verify plugin name must be non-empty")
    _verify[key] = fn
    return fn


def get_verify(name: str) -> VerifyPlugin | None:
    return _verify.get(name)


def list_verify_plugins() -> list[str]:
    return sorted(_verify.keys())


def register_setup(fn: SetupFn) -> SetupFn:
    """Optional one-shot setup when a plugin module loads."""
    _setup_fns.append(fn)
    return fn


def run_setup_hooks() -> None:
    for fn in _setup_fns:
        fn()


def register_before_run(fn: BeforeRunHook) -> BeforeRunHook:
    """Register a hook called after prepare, before SimLeg connects."""
    _before_run.append(fn)
    return fn


def list_before_run_hooks() -> list[BeforeRunHook]:
    return list(_before_run)


def run_before_run_hooks(ctx: BeforeRunContext) -> None:
    for fn in _before_run:
        fn(ctx)


def register_after_run(fn: AfterRunHook) -> AfterRunHook:
    """Register a hook called after run finishes (done or failed)."""
    _after_run.append(fn)
    return fn


def list_after_run_hooks() -> list[AfterRunHook]:
    return list(_after_run)


def run_after_run_hooks(ctx: AfterRunContext) -> None:
    for fn in _after_run:
        fn(ctx)


def mark_loaded(key: str) -> bool:
    """Return False if this load key was already processed."""
    if key in _loaded_keys:
        return False
    _loaded_keys.add(key)
    return True


def reset_for_tests() -> None:
    _verify.clear()
    _setup_fns.clear()
    _before_run.clear()
    _after_run.clear()
    _loaded_keys.clear()
