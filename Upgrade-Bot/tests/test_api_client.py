"""Fehlerauswertung im API-Client des Upgrade-Bots.

    .venv/bin/python -m unittest discover -s tests
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api_client  # noqa: E402


def antwort(status, body):
    r = mock.Mock(status_code=status, headers={}, content=b"x", text="roh")
    r.json.return_value = body
    return r


class Fehlerantwort(unittest.TestCase):
    def fehler(self, body):
        c = api_client.SeekampfClient()
        c.session.request = mock.Mock(return_value=antwort(409, body))
        with self.assertRaises(api_client.ApiError) as ctx:
            c._request("GET", "/x")
        return ctx.exception

    def test_normaler_fehler(self):
        e = self.fehler({"error": {"code": "building_disabled", "message": "aus"}})
        self.assertEqual((e.status_code, e.code, e.message), (409, "building_disabled", "aus"))

    def test_liste_statt_objekt_gibt_apierror_statt_attributeerror(self):
        self.assertEqual(self.fehler([1, 2]).code, "unknown")

    def test_error_ist_kein_objekt(self):
        self.assertEqual(self.fehler({"error": "kaputt"}).code, "unknown")


class Zuviele(unittest.TestCase):
    def test_429_wird_mit_retry_after_wiederholt(self):
        c = api_client.SeekampfClient()
        zu_viel = mock.Mock(status_code=429, headers={"Retry-After": "3"})
        ok = mock.Mock(status_code=200, headers={}, content=b"{}")
        ok.json.return_value = {"ok": 1}
        c.session.request = mock.Mock(side_effect=[zu_viel, ok])
        with mock.patch("api_client.time.sleep") as schlaf:
            self.assertEqual(c._request("POST", "/x"), {"ok": 1})
        schlaf.assert_called_once_with(3.0)

    def test_dauerhaft_429_gibt_apierror(self):
        c = api_client.SeekampfClient()
        zu_viel = mock.Mock(status_code=429, headers={}, content=b"", text="langsam")
        zu_viel.json.side_effect = ValueError
        c.session.request = mock.Mock(return_value=zu_viel)
        with mock.patch("api_client.time.sleep"):
            with self.assertRaises(api_client.ApiError) as ctx:
                c._request("GET", "/x")
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(c.session.request.call_count, 1 + api_client.MAX_429_VERSUCHE)


if __name__ == "__main__":
    unittest.main()
