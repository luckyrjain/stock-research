import logging
import os
import sys
import unittest
import warnings
from unittest.mock import MagicMock, patch

from core import error_tracking
from core import observability

try:
    import sentry_sdk as _real_sentry_sdk
    from sentry_sdk.transport import Transport as _RealTransport
except ImportError:  # pragma: no cover - sentry-sdk is a hard requirements.txt dep
    _real_sentry_sdk = None
    _RealTransport = None

_TEST_DSN = "https://public@example.ingest.sentry.io/1"


def _mock_sentry_modules(fake_sdk: MagicMock) -> dict:
    """`from sentry_sdk.integrations.logging import LoggingIntegration` does its
    own sys.modules lookup for the 'sentry_sdk.integrations.logging' key —
    patching only the top-level 'sentry_sdk' entry isn't enough to satisfy it
    (Python falls back to a real filesystem import of the submodule, which
    fails against a MagicMock parent with no __path__). Patch all three
    levels so the import resolves entirely from mocks."""
    fake_logging_mod = MagicMock()
    fake_logging_mod.LoggingIntegration = MagicMock(name="LoggingIntegration")
    fake_sdk.integrations.logging = fake_logging_mod
    return {
        "sentry_sdk": fake_sdk,
        "sentry_sdk.integrations": MagicMock(logging=fake_logging_mod),
        "sentry_sdk.integrations.logging": fake_logging_mod,
    }


class _StateResetMixin:
    def setUp(self) -> None:
        self._dsn = os.environ.pop("SENTRY_DSN", None)
        self._env = os.environ.pop("SENTRY_ENVIRONMENT", None)
        self._reset_module_state()

    def tearDown(self) -> None:
        if self._dsn is not None:
            os.environ["SENTRY_DSN"] = self._dsn
        else:
            os.environ.pop("SENTRY_DSN", None)
        if self._env is not None:
            os.environ["SENTRY_ENVIRONMENT"] = self._env
        else:
            os.environ.pop("SENTRY_ENVIRONMENT", None)
        self._reset_module_state()
        if _real_sentry_sdk is not None:
            _real_sentry_sdk.get_global_scope().set_client(None)

    @staticmethod
    def _reset_module_state() -> None:
        error_tracking._initialized = False
        error_tracking._enabled = False
        error_tracking._warned_missing_sdk = False


class InitErrorTrackingTest(_StateResetMixin, unittest.TestCase):
    def test_noop_without_dsn(self) -> None:
        error_tracking.init_error_tracking()
        self.assertFalse(error_tracking._enabled)

    def test_enables_when_dsn_set_and_sdk_importable(self) -> None:
        os.environ["SENTRY_DSN"] = _TEST_DSN
        fake_sdk = MagicMock()
        with patch.dict(sys.modules, _mock_sentry_modules(fake_sdk)):
            error_tracking.init_error_tracking()

        self.assertTrue(error_tracking._enabled)
        fake_sdk.init.assert_called_once()
        _, kwargs = fake_sdk.init.call_args
        self.assertEqual(kwargs["dsn"], _TEST_DSN)
        self.assertEqual(kwargs["environment"], "production")
        self.assertEqual(kwargs["traces_sample_rate"], 0.0)
        self.assertFalse(kwargs["send_default_pii"])
        # See the double-capture regression test below: without this, the
        # SDK's own LoggingIntegration auto-captures every logger.error()
        # call (including log_event's own plain log line) as a second,
        # redundant event on top of the one capture_error() sends explicitly.
        integration = kwargs["integrations"][0]
        fake_sdk.integrations.logging.LoggingIntegration.assert_called_once_with(event_level=None)
        self.assertIs(integration, fake_sdk.integrations.logging.LoggingIntegration.return_value)

    def test_respects_sentry_environment_override(self) -> None:
        os.environ["SENTRY_DSN"] = _TEST_DSN
        os.environ["SENTRY_ENVIRONMENT"] = "staging"
        fake_sdk = MagicMock()
        with patch.dict(sys.modules, _mock_sentry_modules(fake_sdk)):
            error_tracking.init_error_tracking()

        self.assertEqual(fake_sdk.init.call_args.kwargs["environment"], "staging")

    def test_idempotent_second_call_is_noop(self) -> None:
        os.environ["SENTRY_DSN"] = _TEST_DSN
        fake_sdk = MagicMock()
        with patch.dict(sys.modules, _mock_sentry_modules(fake_sdk)):
            error_tracking.init_error_tracking()
            error_tracking.init_error_tracking()

        fake_sdk.init.assert_called_once()

    def test_missing_sdk_package_degrades_to_disabled(self) -> None:
        os.environ["SENTRY_DSN"] = _TEST_DSN
        # sys.modules[name] = None forces `import name` to raise ImportError.
        with patch.dict(sys.modules, {"sentry_sdk": None}):
            error_tracking.init_error_tracking()

        self.assertFalse(error_tracking._enabled)

    def test_sdk_init_failure_degrades_to_disabled(self) -> None:
        os.environ["SENTRY_DSN"] = _TEST_DSN
        fake_sdk = MagicMock()
        fake_sdk.init.side_effect = RuntimeError("bad dsn")
        with patch.dict(sys.modules, _mock_sentry_modules(fake_sdk)):
            error_tracking.init_error_tracking()

        self.assertFalse(error_tracking._enabled)


class CaptureErrorTest(_StateResetMixin, unittest.TestCase):
    def _enable(self) -> MagicMock:
        os.environ["SENTRY_DSN"] = _TEST_DSN
        fake_sdk = MagicMock()
        scope = MagicMock()
        fake_sdk.new_scope.return_value.__enter__.return_value = scope
        with patch.dict(sys.modules, _mock_sentry_modules(fake_sdk)):
            error_tracking.init_error_tracking()
        return fake_sdk

    def test_noop_when_not_enabled(self) -> None:
        fake_sdk = MagicMock()
        with patch.dict(sys.modules, _mock_sentry_modules(fake_sdk)):
            error_tracking.capture_error("some_event", {"symbol": "TCS"})
        fake_sdk.new_scope.assert_not_called()

    def test_forwards_exception_with_context_tags(self) -> None:
        fake_sdk = self._enable()
        scope = fake_sdk.new_scope.return_value.__enter__.return_value
        exc = ValueError("boom")

        with patch.dict(sys.modules, _mock_sentry_modules(fake_sdk)):
            error_tracking.capture_error("analyst_llm_failed", {"symbol": "TCS", "error": "boom"}, exc=exc)

        scope.set_tag.assert_called_once_with("event", "analyst_llm_failed")
        scope.set_extra.assert_called_once_with("symbol", "TCS")  # "error" key is skipped
        fake_sdk.capture_exception.assert_called_once_with(exc)
        fake_sdk.capture_message.assert_not_called()

    def test_captures_message_when_no_exception_given(self) -> None:
        fake_sdk = self._enable()

        with patch.dict(sys.modules, _mock_sentry_modules(fake_sdk)):
            error_tracking.capture_error("sme_no_stocks_fetched", {})

        fake_sdk.capture_message.assert_called_once_with("sme_no_stocks_fetched", level="error")
        fake_sdk.capture_exception.assert_not_called()

    def test_sdk_failure_is_swallowed(self) -> None:
        fake_sdk = self._enable()
        fake_sdk.new_scope.side_effect = RuntimeError("network down")

        with patch.dict(sys.modules, _mock_sentry_modules(fake_sdk)):
            error_tracking.capture_error("some_event", {})  # must not raise


class LogEventIntegrationTest(_StateResetMixin, unittest.TestCase):
    """Confirms observability.log_event's error-level path actually reaches
    error_tracking.capture_error, without depending on a real/mocked SDK."""

    def setUp(self) -> None:
        super().setUp()
        self.logger = logging.getLogger("stock_research.test_error_tracking")
        self.logger.addHandler(logging.NullHandler())

    def test_error_level_forwards_to_capture_error(self) -> None:
        with patch("core.observability.error_tracking.capture_error") as mock_capture:
            observability.log_event(self.logger, "boom_event", level="error", symbol="TCS")

        mock_capture.assert_called_once_with("boom_event", {"symbol": "TCS"}, exc=None)

    def test_error_level_forwards_exception_object(self) -> None:
        exc = ValueError("bad")
        with patch("core.observability.error_tracking.capture_error") as mock_capture:
            observability.log_event(self.logger, "boom_event", level="error", exc=exc, symbol="TCS")

        mock_capture.assert_called_once_with("boom_event", {"symbol": "TCS"}, exc=exc)

    def test_info_level_never_forwards(self) -> None:
        with patch("core.observability.error_tracking.capture_error") as mock_capture:
            observability.log_event(self.logger, "fine_event", level="info", symbol="TCS")

        mock_capture.assert_not_called()

    def test_capture_error_exception_never_breaks_logging(self) -> None:
        with patch("core.observability.error_tracking.capture_error", side_effect=RuntimeError("boom")):
            observability.log_event(self.logger, "boom_event", level="error", symbol="TCS")  # must not raise


@unittest.skipUnless(_real_sentry_sdk is not None, "sentry-sdk not installed")
class RealSdkRegressionTest(_StateResetMixin, unittest.TestCase):
    """Exercises init_error_tracking()/capture_error() against the actual
    installed sentry-sdk package (not a mock) — mocking every sys.modules
    entry elsewhere in this file verifies our own call shapes, but can't
    catch a real SDK behavior mismatch (a deprecated API, or the SDK's own
    default integrations double-reporting). Both were real bugs caught by
    exactly this kind of check; see CLAUDE.md's "Error tracking / APM hook"
    disclosed limitation for what's still unverified (live delivery)."""

    class _RecordingTransport(_RealTransport if _RealTransport else object):
        def __init__(self) -> None:
            super().__init__()
            self.envelopes = []

        def capture_envelope(self, envelope) -> None:
            self.envelopes.append(envelope)

    def _init_with_recording_transport(self) -> "RealSdkRegressionTest._RecordingTransport":
        os.environ["SENTRY_DSN"] = _TEST_DSN
        error_tracking.init_error_tracking()
        transport = self._RecordingTransport()
        _real_sentry_sdk.get_client().transport = transport
        return transport

    def test_capture_error_does_not_use_deprecated_push_scope(self) -> None:
        transport = self._init_with_recording_transport()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            error_tracking.capture_error("some_event", {"symbol": "TCS"}, exc=ValueError("boom"))

        push_scope_warnings = [w for w in caught if "push_scope" in str(w.message)]
        self.assertEqual(push_scope_warnings, [])
        self.assertEqual(len(transport.envelopes), 1)

    def test_log_event_error_path_sends_exactly_one_event(self) -> None:
        # Regression test: sentry_sdk's default LoggingIntegration auto-
        # captures any logger.error()/.critical() call as its own event.
        # observability.log_event() calls logger.error(...) itself (the
        # plain structured-log line) immediately before it explicitly calls
        # capture_error() — without disabling the integration's event-level
        # auto-capture (event_level=None in core/error_tracking.py's
        # LoggingIntegration(...) call), this fires an extra, low-quality
        # duplicate event alongside the properly-tagged one.
        transport = self._init_with_recording_transport()
        logger = logging.getLogger("stock_research.real_sdk_regression")
        logger.addHandler(logging.NullHandler())

        observability.log_event(logger, "boom_event", level="error", exc=ValueError("bad"), symbol="TCS")

        self.assertEqual(len(transport.envelopes), 1)


if __name__ == "__main__":
    unittest.main()
