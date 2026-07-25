import logging
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import error_tracking
import observability


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
        os.environ["SENTRY_DSN"] = "https://public@example.ingest.sentry.io/1"
        fake_sdk = MagicMock()
        with patch.dict(sys.modules, {"sentry_sdk": fake_sdk}):
            error_tracking.init_error_tracking()

        self.assertTrue(error_tracking._enabled)
        fake_sdk.init.assert_called_once()
        _, kwargs = fake_sdk.init.call_args
        self.assertEqual(kwargs["dsn"], "https://public@example.ingest.sentry.io/1")
        self.assertEqual(kwargs["environment"], "production")
        self.assertEqual(kwargs["traces_sample_rate"], 0.0)
        self.assertFalse(kwargs["send_default_pii"])

    def test_respects_sentry_environment_override(self) -> None:
        os.environ["SENTRY_DSN"] = "https://public@example.ingest.sentry.io/1"
        os.environ["SENTRY_ENVIRONMENT"] = "staging"
        fake_sdk = MagicMock()
        with patch.dict(sys.modules, {"sentry_sdk": fake_sdk}):
            error_tracking.init_error_tracking()

        self.assertEqual(fake_sdk.init.call_args.kwargs["environment"], "staging")

    def test_idempotent_second_call_is_noop(self) -> None:
        os.environ["SENTRY_DSN"] = "https://public@example.ingest.sentry.io/1"
        fake_sdk = MagicMock()
        with patch.dict(sys.modules, {"sentry_sdk": fake_sdk}):
            error_tracking.init_error_tracking()
            error_tracking.init_error_tracking()

        fake_sdk.init.assert_called_once()

    def test_missing_sdk_package_degrades_to_disabled(self) -> None:
        os.environ["SENTRY_DSN"] = "https://public@example.ingest.sentry.io/1"
        # sys.modules[name] = None forces `import name` to raise ImportError.
        with patch.dict(sys.modules, {"sentry_sdk": None}):
            error_tracking.init_error_tracking()

        self.assertFalse(error_tracking._enabled)

    def test_sdk_init_failure_degrades_to_disabled(self) -> None:
        os.environ["SENTRY_DSN"] = "https://public@example.ingest.sentry.io/1"
        fake_sdk = MagicMock()
        fake_sdk.init.side_effect = RuntimeError("bad dsn")
        with patch.dict(sys.modules, {"sentry_sdk": fake_sdk}):
            error_tracking.init_error_tracking()

        self.assertFalse(error_tracking._enabled)


class CaptureErrorTest(_StateResetMixin, unittest.TestCase):
    def _enable(self) -> MagicMock:
        os.environ["SENTRY_DSN"] = "https://public@example.ingest.sentry.io/1"
        fake_sdk = MagicMock()
        scope = MagicMock()
        fake_sdk.push_scope.return_value.__enter__.return_value = scope
        with patch.dict(sys.modules, {"sentry_sdk": fake_sdk}):
            error_tracking.init_error_tracking()
        return fake_sdk

    def test_noop_when_not_enabled(self) -> None:
        fake_sdk = MagicMock()
        with patch.dict(sys.modules, {"sentry_sdk": fake_sdk}):
            error_tracking.capture_error("some_event", {"symbol": "TCS"})
        fake_sdk.push_scope.assert_not_called()

    def test_forwards_exception_with_context_tags(self) -> None:
        fake_sdk = self._enable()
        scope = fake_sdk.push_scope.return_value.__enter__.return_value
        exc = ValueError("boom")

        with patch.dict(sys.modules, {"sentry_sdk": fake_sdk}):
            error_tracking.capture_error("analyst_llm_failed", {"symbol": "TCS", "error": "boom"}, exc=exc)

        scope.set_tag.assert_called_once_with("event", "analyst_llm_failed")
        scope.set_extra.assert_called_once_with("symbol", "TCS")  # "error" key is skipped
        fake_sdk.capture_exception.assert_called_once_with(exc)
        fake_sdk.capture_message.assert_not_called()

    def test_captures_message_when_no_exception_given(self) -> None:
        fake_sdk = self._enable()

        with patch.dict(sys.modules, {"sentry_sdk": fake_sdk}):
            error_tracking.capture_error("sme_no_stocks_fetched", {})

        fake_sdk.capture_message.assert_called_once_with("sme_no_stocks_fetched", level="error")
        fake_sdk.capture_exception.assert_not_called()

    def test_sdk_failure_is_swallowed(self) -> None:
        fake_sdk = self._enable()
        fake_sdk.push_scope.side_effect = RuntimeError("network down")

        with patch.dict(sys.modules, {"sentry_sdk": fake_sdk}):
            error_tracking.capture_error("some_event", {})  # must not raise


class LogEventIntegrationTest(_StateResetMixin, unittest.TestCase):
    """Confirms observability.log_event's error-level path actually reaches
    error_tracking.capture_error, without depending on a real/mocked SDK."""

    def setUp(self) -> None:
        super().setUp()
        self.logger = logging.getLogger("stock_research.test_error_tracking")
        self.logger.addHandler(logging.NullHandler())

    def test_error_level_forwards_to_capture_error(self) -> None:
        with patch("observability.error_tracking.capture_error") as mock_capture:
            observability.log_event(self.logger, "boom_event", level="error", symbol="TCS")

        mock_capture.assert_called_once_with("boom_event", {"symbol": "TCS"}, exc=None)

    def test_error_level_forwards_exception_object(self) -> None:
        exc = ValueError("bad")
        with patch("observability.error_tracking.capture_error") as mock_capture:
            observability.log_event(self.logger, "boom_event", level="error", exc=exc, symbol="TCS")

        mock_capture.assert_called_once_with("boom_event", {"symbol": "TCS"}, exc=exc)

    def test_info_level_never_forwards(self) -> None:
        with patch("observability.error_tracking.capture_error") as mock_capture:
            observability.log_event(self.logger, "fine_event", level="info", symbol="TCS")

        mock_capture.assert_not_called()

    def test_capture_error_exception_never_breaks_logging(self) -> None:
        with patch("observability.error_tracking.capture_error", side_effect=RuntimeError("boom")):
            observability.log_event(self.logger, "boom_event", level="error", symbol="TCS")  # must not raise


if __name__ == "__main__":
    unittest.main()
