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


if __name__ == "__main__":
    unittest.main()
