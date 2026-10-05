import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rag"))

from redaction import redact_local_paths  # noqa: E402


class RagRedactionTests(unittest.TestCase):
    def test_redacts_local_paths_without_changing_other_content(self):
        text = "Found /Users/alex/project/config.json and /var/www/site/app.py."
        self.assertEqual(
            redact_local_paths(text),
            "Found [redacted local path] and [redacted local path].",
        )

    def test_keeps_public_urls_and_normal_answers(self):
        text = "See https://alexpavsky.com/api/rag/query for 7 deployed agents."
        self.assertEqual(redact_local_paths(text), text)


if __name__ == "__main__":
    unittest.main()
