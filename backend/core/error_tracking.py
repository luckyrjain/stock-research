"""Pluggable error-tracking/APM hook, gated behind the optional SENTRY_DSN env
var and wired into observability.log_event()'s error-level path.

Same "missing optional infra degrades rather than breaks" convention as
DATABASE_URL/SMTP_HOST/REDIS_URL elsewhere in this codebase: every function
here is a no-op whenever SENTRY_DSN is unset, the sentry_sdk package isn't
importable, or a call into it raises — this module must never be the reason
a request or a batch job fails. "Pluggable" here means "swap the DSN" (point
it at any Sentry-compatible ingest endpoint — self-hosted Sentry, GlitchTip,
etc. all speak the same DSN/init() protocol) rather than a plugin registry
of multiple backends; this codebase has exactly one thing that consumes
errors today (log_event's error-level path), so a second abstraction layer
on top of that would be speculative.

Uses the stdlib `logging` module directly for its own diagnostics (never
observability.log_event) — this module is a dependency of core/observability.py,
so routing its own warnings back through log_event would be circular.
"""
import logging
import os
import threading
from typing import Any

_DIAG_LOGGER = logging.getLogger("stock_research.error_tracking")

_lock = threading.Lock()
_initialized = False
_enabled = False
_warned_missing_sdk = False

# Extra fields commonly attached to log_event calls that are large, already
# captured elsewhere (e.g. the "error" string duplicates what
# capture_exception's own stack trace conveys), or not useful as Sentry tags
# — dropped rather than sent as noisy/oversized extras.
_SKIP_CONTEXT_KEYS = frozenset({"error"})


def init_error_tracking() -> None:
    """Idempotent. Call once per process at startup — api.py's app import,
    and the CLI entrypoints of main.py / pipelines/sme_ema_pipeline.py /
    pipelines/market_picks_pipeline.py / pipelines/watchlist_alerts.py / pipelines/screener_pipeline.py.
    A second call is a harmless no-op (sentry_sdk.init() itself is not
    idempotent-safe to call twice with different configs, so this guards it)."""
    global _initialized, _enabled
    with _lock:
        if _initialized:
            return
        _initialized = True

        dsn = os.environ.get("SENTRY_DSN")
        if not dsn:
            return

        try:
            import sentry_sdk  # pylint: disable=import-outside-toplevel
            from sentry_sdk.integrations.logging import LoggingIntegration
        except ImportError:
            _warn_missing_sdk()
            return

        try:
            sentry_sdk.init(
                dsn=dsn,
                environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
                # This is an error-tracking hook, not a performance-tracing
                # one — no APM transaction sampling.
                traces_sample_rate=0.0,
                send_default_pii=False,
                # The SDK's default LoggingIntegration auto-captures any
                # stdlib logging.error()/.critical() call as its own event —
                # including observability.log_event()'s own plain log line,
                # which fires just before it explicitly calls capture_error()
                # below. Without event_level=None that would double-report
                # every error (one well-structured event from capture_error,
                # one raw-JSON-message event from the auto-capture) —
                # keep breadcrumbs (level=INFO, the default) but disable the
                # redundant auto-capture-as-event.
                integrations=[LoggingIntegration(event_level=None)],
            )
            _enabled = True
        except Exception:  # pylint: disable=broad-exception-caught
            _DIAG_LOGGER.warning("sentry_sdk.init() failed; error tracking disabled", exc_info=True)


def _warn_missing_sdk() -> None:
    global _warned_missing_sdk
    if _warned_missing_sdk:
        return
    _warned_missing_sdk = True
    _DIAG_LOGGER.warning(
        "SENTRY_DSN is set but the sentry-sdk package isn't installed; "
        "error tracking disabled. Run `pip install -r requirements.txt`."
    )


def capture_error(event: str, fields: dict[str, Any], exc: BaseException | None = None) -> None:
    """Best-effort forward of one error-level log_event() call to Sentry.
    No-op unless init_error_tracking() ran and actually enabled the SDK
    (unset SENTRY_DSN, missing package, or a failed init all degrade here
    silently — already warned about once at init time)."""
    if not _enabled:
        return

    try:
        import sentry_sdk  # pylint: disable=import-outside-toplevel

        with sentry_sdk.new_scope() as scope:
            scope.set_tag("event", event)
            for key, value in fields.items():
                if key in _SKIP_CONTEXT_KEYS:
                    continue
                scope.set_extra(key, value)

            if exc is not None:
                sentry_sdk.capture_exception(exc)
            else:
                sentry_sdk.capture_message(event, level="error")
    except Exception:  # pylint: disable=broad-exception-caught
        _DIAG_LOGGER.warning("Failed to forward error event to Sentry", exc_info=True)
