import unittest
from unittest.mock import MagicMock, patch

from tools.nse_filings_tools import get_nse_filings


def _resp(status_code=200, content_type="application/json", json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {"Content-Type": content_type}
    resp.json.return_value = json_data if json_data is not None else []
    resp.text = text
    return resp


class GetNseFilingsTest(unittest.TestCase):
    def _session(self, response=None, get_side_effect=None):
        sess = MagicMock()
        if get_side_effect is not None:
            sess.get.side_effect = get_side_effect
        else:
            sess.get.return_value = response
        return sess

    def test_successful_response_returns_normalized_filings(self) -> None:
        data = [{
            "subject": "Board Meeting", "description": "Q4 results",
            "date": "01-Jan-2026", "category": "Financial Results",
            "attchmntFile": "https://example.com/a.pdf",
        }]
        sess = self._session(_resp(json_data=data))
        with patch("tools.nse_filings_tools._get_session", return_value=sess):
            result = get_nse_filings("TCS")

        self.assertEqual(result["symbol"], "TCS")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["filings"][0]["title"], "Board Meeting")
        self.assertEqual(result["filings"][0]["attachment"], "https://example.com/a.pdf")

    def test_non_200_status_returns_error(self) -> None:
        sess = self._session(_resp(status_code=503))
        with patch("tools.nse_filings_tools._get_session", return_value=sess):
            result = get_nse_filings("TCS")
        self.assertIn("error", result)
        self.assertIn("503", result["error"])

    def test_non_json_content_type_returns_error_with_raw_snippet(self) -> None:
        sess = self._session(_resp(content_type="text/html", text="<html>blocked</html>"))
        with patch("tools.nse_filings_tools._get_session", return_value=sess):
            result = get_nse_filings("TCS")
        self.assertIn("error", result)
        self.assertIn("blocked", result["raw"])

    def test_empty_filings_list_returns_zero_count(self) -> None:
        sess = self._session(_resp(json_data=[]))
        with patch("tools.nse_filings_tools._get_session", return_value=sess):
            result = get_nse_filings("TCS")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["filings"], [])

    def test_session_setup_failure_returns_error_not_raise(self) -> None:
        with patch("tools.nse_filings_tools._get_session", side_effect=ConnectionError("boom")):
            result = get_nse_filings("TCS")
        self.assertIn("error", result)
        self.assertEqual(result["symbol"], "TCS")

    def test_issuer_param_is_url_encoded_with_spaces(self) -> None:
        sess = self._session(_resp(json_data=[]))
        with patch("tools.nse_filings_tools._get_session", return_value=sess):
            get_nse_filings("TCS", issuer="Tata Group")
        called_url = sess.get.call_args[0][0]
        self.assertIn("issuer=Tata%20Group", called_url)

    def test_null_subject_and_description_default_to_empty_string(self) -> None:
        # Regression test: NSE sometimes returns a filing with subject/
        # description present but null (not simply absent), which used to
        # flow through as a bare None and crash signals/filings.py with a
        # TypeError on string concatenation, taking down the whole analysis.
        data = [{
            "subject": None, "description": None,
            "date": "01-Jan-2026", "category": "Financial Results",
            "attchmntFile": None,
        }]
        sess = self._session(_resp(json_data=data))
        with patch("tools.nse_filings_tools._get_session", return_value=sess):
            result = get_nse_filings("TCS")

        self.assertEqual(result["filings"][0]["title"], "")
        self.assertEqual(result["filings"][0]["desc"], "")

    def test_non_list_response_body_returns_error_not_raise(self) -> None:
        sess = self._session(_resp(json_data={"unexpected": "shape"}))
        with patch("tools.nse_filings_tools._get_session", return_value=sess):
            result = get_nse_filings("TCS")
        self.assertIn("error", result)
        self.assertNotIn("filings", result)

    def test_non_dict_item_in_list_is_skipped_not_raised(self) -> None:
        data = [
            "not-a-dict",
            {"subject": "Board Meeting", "description": "Q4 results", "date": "01-Jan-2026"},
        ]
        sess = self._session(_resp(json_data=data))
        with patch("tools.nse_filings_tools._get_session", return_value=sess):
            result = get_nse_filings("TCS")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["filings"][0]["title"], "Board Meeting")

    def test_symbol_is_uppercased_and_url_encoded(self) -> None:
        sess = self._session(_resp(json_data=[]))
        with patch("tools.nse_filings_tools._get_session", return_value=sess):
            result = get_nse_filings("tata steel")
        called_url = sess.get.call_args[0][0]
        self.assertIn("symbol=TATA%20STEEL", called_url)
        self.assertEqual(result["symbol"], "TATA STEEL")

    def test_date_window_uses_ist_not_local_time(self) -> None:
        # Pin "now" to a moment that's a different calendar date in UTC vs
        # IST to prove the request window is computed in IST, not whatever
        # timezone the host process happens to be running in.
        from datetime import datetime
        from zoneinfo import ZoneInfo
        fixed_ist = datetime(2026, 1, 15, 2, 0, tzinfo=ZoneInfo("Asia/Kolkata"))  # 2026-01-14 20:30 UTC

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_ist if tz is not None else fixed_ist.replace(tzinfo=None)

        sess = self._session(_resp(json_data=[]))
        with patch("tools.nse_filings_tools._get_session", return_value=sess), \
             patch("tools.nse_filings_tools.datetime", _FixedDatetime):
            get_nse_filings("TCS", days=1)
        called_url = sess.get.call_args[0][0]
        self.assertIn("to_date=15-01-2026", called_url)


if __name__ == "__main__":
    unittest.main()
