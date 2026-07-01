"""Phase 2 hardening regression checks — pure logic, no network/Discord/Claude.

Run: python scripts/test_phase2.py
Exits non-zero on the first failed assertion. Safe to run anywhere (uses temp files).
"""
import os
import sys
import tempfile

# Make repo root importable when run as `python scripts/test_phase2.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.queue_manager import ProcessingState  # noqa: E402
from src.utils.retry import with_retry  # noqa: E402
from src.utils.validation import ConfigError, validate_startup  # noqa: E402

_checks = 0


def check(cond: bool, msg: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"  ok: {msg}")


def _full_config() -> dict:
    return {
        "paths": {
            "vault_root": tempfile.gettempdir(),
            "media_root": tempfile.gettempdir(),
            "inbox_file": os.path.join(tempfile.gettempdir(), "inbox.md"),
        },
        "discord": {
            "channel_approvals": "SAVES-approvals",
            "channel_log": "SAVES-logs",
            "channel_alerts": "SAVES-alerts",
        },
    }


def test_validate_startup_passes():
    validate_startup(_full_config())  # should not raise
    check(True, "validate_startup accepts a complete config")


def test_validate_startup_missing_path():
    cfg = _full_config()
    del cfg["paths"]["vault_root"]
    try:
        validate_startup(cfg)
    except ConfigError as e:
        check("vault_root" in str(e), "validate_startup fails on missing paths.vault_root")
    else:
        check(False, "validate_startup should have raised on missing vault_root")


def test_validate_startup_missing_channel():
    cfg = _full_config()
    cfg["discord"]["channel_alerts"] = ""
    try:
        validate_startup(cfg)
    except ConfigError as e:
        check("channel_alerts" in str(e), "validate_startup fails on empty discord.channel_alerts")
    else:
        check(False, "validate_startup should have raised on empty channel_alerts")


def test_validate_startup_reports_all_problems():
    cfg = {"paths": {}, "discord": {}}
    try:
        validate_startup(cfg)
    except ConfigError as e:
        # 3 paths + 3 channels = 6 problems in one message
        check(str(e).count("\n  - ") == 6, "validate_startup reports all 6 problems at once")
    else:
        check(False, "validate_startup should have raised")


def test_state_is_done_and_path_for():
    with tempfile.TemporaryDirectory() as d:
        state = ProcessingState(os.path.join(d, "state.json"))
        url = "https://example.com/x"
        check(not state.is_done(url), "is_done False for an unknown URL")
        check(state.path_for(url) is None, "path_for None for an unknown URL")

        state.mark_pending(url)
        check(not state.is_done(url), "is_done False while merely pending")

        note_path = "/vault/SAVES/X.md"
        state.mark_done(url, note_path)
        check(state.is_done(url), "is_done True after mark_done")
        check(state.path_for(url) == note_path, "path_for returns the recorded note path")

        # Persists across reload (source of truth for the _finalize idempotency guard).
        reloaded = ProcessingState(os.path.join(d, "state.json"))
        check(reloaded.is_done(url), "is_done True after reload from disk")
        check(reloaded.path_for(url) == note_path, "path_for survives reload")

        failed = "https://example.com/y"
        state.mark_failed(failed, "boom", permanent=True)
        check(not state.is_done(failed), "is_done False for a failed URL")


def test_with_retry_succeeds_after_transient():
    calls = {"n": 0}

    @with_retry(attempts=3, base_delay=0, exceptions=(ValueError,))
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("transient")
        return "ok"

    check(flaky() == "ok", "with_retry returns after transient failures")
    check(calls["n"] == 3, "with_retry made exactly 3 attempts")


def test_with_retry_reraises_after_exhaustion():
    calls = {"n": 0}

    @with_retry(attempts=2, base_delay=0, exceptions=(ValueError,))
    def always_fails():
        calls["n"] += 1
        raise ValueError("permanent")

    try:
        always_fails()
    except ValueError:
        check(calls["n"] == 2, "with_retry re-raises after exhausting attempts")
    else:
        check(False, "with_retry should have re-raised")


def main():
    print("Phase 2 hardening checks")
    for fn in (
        test_validate_startup_passes,
        test_validate_startup_missing_path,
        test_validate_startup_missing_channel,
        test_validate_startup_reports_all_problems,
        test_state_is_done_and_path_for,
        test_with_retry_succeeds_after_transient,
        test_with_retry_reraises_after_exhaustion,
    ):
        print(f"\n[{fn.__name__}]")
        fn()
    print(f"\n{_checks} checks passed.")


if __name__ == "__main__":
    main()
