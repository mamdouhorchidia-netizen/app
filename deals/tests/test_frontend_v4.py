"""Server-written chart dates in both languages (/p/ID), and the live page's keyboard entry point."""

import re
import unittest
from pathlib import Path

from deals import pages, web

LIVE = Path(web.__file__).with_name("live.html").read_text(encoding="utf-8")
I18N = Path(web.__file__).with_name("i18n.js").read_text(encoding="utf-8")


class ChartDates(unittest.TestCase):
    T = 1_761_696_000  # 29 Oct 2025, 02:00 Cairo

    def test_dates_in_both_languages(self):
        self.assertEqual(pages.date_text(self.T), ("29 Oct", "29 أكتوبر"))
        self.assertEqual(pages.date_text(self.T, year=True), ("29 Oct 2025", "29 أكتوبر 2025"))
        self.assertEqual(pages.date_html(self.T), '<span data-ar="29 أكتوبر" data-noi18n>29 Oct</span>')

    def test_chart_labels_are_text_beside_the_drawing(self):
        html = pages.chart([{"at": self.T, "price": 100.0}, {"at": self.T + 5 * 86400, "price": 80.0}], self.T + 9 * 86400)
        svg = re.search(r"<svg.*</svg>", html, re.S).group(0)
        self.assertNotIn("<text", svg)  # a stretched svg squeezes its text: the labels are HTML
        self.assertNotRegex(html, r"\d\d/\d\d")  # no bare dd/mm
        self.assertIn('data-ar="29 أكتوبر"', html)
        self.assertIn('data-ar="100 · 29 أكتوبر"', svg)  # the dots' tooltips too
        self.assertIn('data-ar="7 نوفمبر"', html)

    def test_i18n_swaps_data_ar(self):
        self.assertIn("[data-ar]", I18N)
        self.assertIn('"tracked since"', I18N)


class Keyboard(unittest.TestCase):
    def test_skip_link_comes_first(self):
        body = LIVE[LIVE.index("<body>"):]
        first = re.search(r"<(a|button|input|select|textarea)\b[^>]*>", body).group(0)
        self.assertIn('class="skip"', first)
        self.assertIn('"Skip to deals"', I18N)

    def test_dialogs_open_through_the_focus_keeper(self):
        self.assertEqual(LIVE.count(".showModal("), 1)  # only inside openDialog, which remembers the opener


if __name__ == "__main__":
    unittest.main()
