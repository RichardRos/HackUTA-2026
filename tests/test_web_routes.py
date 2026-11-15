import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class WebRoutesTest(unittest.TestCase):
    def test_every_react_page_is_served_on_reload(self):
        pages = set(re.findall(r'<Route path="(/[^"]*)"', (ROOT / "web/src/App.tsx").read_text(encoding="utf-8")))
        served = set(re.findall(r'@app\.get\("(/[^"]*)", include_in_schema=False\)', (ROOT / "api/main.py").read_text(encoding="utf-8")))
        self.assertTrue(pages)
        self.assertEqual(pages - served, set(), "add these pages to web_app() in api/main.py")
