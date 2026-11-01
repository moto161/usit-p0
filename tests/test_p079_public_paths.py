"""Ensure parser diagnostics never publish the absolute TypeScript install path."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from usit.js_ts_parser import parser_availability


class ParserPathDisclosureTests(unittest.TestCase):
    def test_trusted_install_path_is_not_exposed_in_availability(self):
        trust = {
            "trusted": True,
            "version": "6.0.3",
            "ts_lib": "/workspace/usit/node_modules/typescript",
            "real_path": "/workspace/usit/node_modules/typescript",
        }
        with patch("usit.js_ts_parser.shutil.which", return_value="/opt/node/bin/node"):
            with patch("usit.js_ts_parser.verify_ts_lib_trust", return_value=trust):
                result = parser_availability(Path.cwd())
        serialized = json.dumps(result)
        self.assertNotIn("node_modules/typescript", serialized)
        self.assertNotIn("/workspace/usit", serialized)
        self.assertTrue(result["available"])
        self.assertEqual(result["version"], "6.0.3")

    def test_untrusted_install_path_is_not_exposed_in_availability(self):
        trust = {
            "trusted": False,
            "reason": "TS_LIB_ROOT_REDIRECTED",
            "ts_lib": "/outside/node_modules/typescript",
            "real_path": "/outside/node_modules/typescript",
        }
        with patch("usit.js_ts_parser.shutil.which", return_value="/opt/node/bin/node"):
            with patch("usit.js_ts_parser.verify_ts_lib_trust", return_value=trust):
                result = parser_availability(Path.cwd())
        serialized = json.dumps(result)
        self.assertNotIn("node_modules/typescript", serialized)
        self.assertNotIn("/outside", serialized)
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "TS_LIB_ROOT_REDIRECTED")


if __name__ == "__main__":
    unittest.main()
