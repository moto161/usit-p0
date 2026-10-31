from pathlib import Path
import unittest

PROTOCOL = Path(__file__).resolve().parents[1] / "PILOT-READONLY-PROTOCOL.md"
TITLE = "# USIT \u53ea\u8bfb\u8bd5\u70b9\u51c6\u5165\u534f\u8bae"


class PilotProtocolTests(unittest.TestCase):
    def test_protocol_is_strict_utf8_and_readable(self):
        raw = PROTOCOL.read_bytes()
        text = raw.decode("utf-8", errors="strict")
        self.assertTrue(text.startswith(TITLE))
        self.assertNotIn("\ufffd", text)
        self.assertNotIn("\u9359\u951b\u20ac", text)

    def test_protocol_has_explicit_authorization_and_safety_gates(self):
        text = PROTOCOL.read_text(encoding="utf-8", errors="strict")
        required = (
            "P0.7.1", "junction", "reparse point", "..", ".git", "node_modules",
            ".env*", "INCOMPLETE", "BLOCKED / NOT STARTED", "import", "eval",
            "HTTP/WS", "NOT RUN", "NOT MEASURED", "\u4e2a\u4eba\u6570\u636e",
            "\u6388\u6743\u8d77\u6b62\u65f6\u95f4", "\u8f93\u51fa\u76ee\u5f55",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_protocol_has_fixed_numeric_resource_caps(self):
        text = PROTOCOL.read_text(encoding="utf-8", errors="strict")
        for value in ("5,000", "1,048,576", "268,435,456", "600", "45"):
            with self.subTest(value=value):
                self.assertIn(value, text)
        self.assertIn("\u5e76\u53d1\u89e3\u6790\u4efb\u52a1", text)
        self.assertIn("\u5355\u4e2a\u89e3\u6790\u5668\u8fdb\u7a0b/\u4efb\u52a1", text)
        self.assertIn("\u8f93\u51fa\u76ee\u5f55\u7d2f\u8ba1\u5927\u5c0f", text)
        self.assertIn("\u7d2f\u8ba1\u8bfb\u53d6\u5b57\u8282", text)


if __name__ == "__main__":
    unittest.main()
