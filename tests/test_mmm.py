import unittest

from src.mmm import extract_last_error_text, generate_mmm


class TestMMM(unittest.TestCase):
    def test_extract_last_error_from_analysis(self):
        analysis = """
line 1 ok
WARN: something slow
ERROR: Payment gateway declined with code PAY_001
""".strip()
        got = extract_last_error_text(analysis, [])
        self.assertIn("ERROR", got)

    def test_extract_last_error_fallback(self):
        got = extract_last_error_text("", [])
        # No files; deterministic placeholder
        self.assertIsInstance(got, str)

    def test_generate_mmm_fallback(self):
        err = "ERROR: Payment declined: card issuer decline"
        mirror, mentor, multiplier = generate_mmm(err, persona="developer", ollama_url=None, model=None)
        self.assertTrue(mirror and mentor and multiplier)
        self.assertIn("decline", mirror.lower())


if __name__ == '__main__':
    unittest.main()





