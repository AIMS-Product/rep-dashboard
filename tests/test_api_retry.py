import sys
import unittest
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_adherence_preview  # noqa: E402
import fetch_data  # noqa: E402


def response_with_read(read_side_effect):
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.side_effect = read_side_effect
    return response


class CloseApiRetryTests(unittest.TestCase):
    @patch.object(fetch_data, "CLOSE_API_KEY", "test-key")
    @patch("fetch_data.time.sleep")
    @patch("fetch_data.urlopen")
    def test_dashboard_fetch_retries_an_incomplete_response(self, urlopen, _sleep):
        urlopen.side_effect = [
            response_with_read([IncompleteRead(b'{"data":', 12)]),
            response_with_read([b'{"data": [], "has_more": false}']),
        ]

        result = fetch_data.api_get("/lead/", {"_limit": "100"})

        self.assertEqual(result, {"data": [], "has_more": False})
        self.assertEqual(urlopen.call_count, 2)

    @patch("build_adherence_preview.time.sleep")
    @patch("build_adherence_preview.urlopen")
    def test_adherence_fetch_retries_an_incomplete_response(self, urlopen, _sleep):
        urlopen.side_effect = [
            response_with_read([IncompleteRead(b'{"data":', 12)]),
            response_with_read([b'{"data": [], "has_more": false}']),
        ]
        client = build_adherence_preview.CloseClient("test-key", throttle=0)

        result = client.get("/activity/call/")

        self.assertEqual(result, {"data": [], "has_more": False})
        self.assertEqual(urlopen.call_count, 2)


if __name__ == "__main__":
    unittest.main()
