import unittest
from datetime import date
from unittest.mock import patch

import wx_scores


@patch("wx_scores.read_query", return_value=[])
class WxScoresTests(unittest.TestCase):
    def test_leaderboard_defaults_to_test_window(self, q):
        wx_scores.leaderboard(lead_days=1)
        sql, params = q.call_args.args
        self.assertIn("nostos_wx.leaderboard_daily", sql)
        self.assertEqual(params, (date(2026, 9, 14), date(2026, 11, 12), 1, 1))

    def test_city_lowercases_and_pivots(self, q):
        wx_scores.city_series("Austin")
        sql, params = q.call_args.args
        self.assertIn("FILTER (WHERE model = 'nostos')", sql)
        self.assertIn("austin", params)

    def test_bad_input(self, q):
        with self.assertRaises(ValueError):
            wx_scores.leaderboard(lead_days=4)
        with self.assertRaises(ValueError):
            wx_scores.weekly_trend(date(2026, 11, 1), date(2026, 10, 1))
        q.assert_not_called()

    def test_views(self, q):
        wx_scores.leaderboard_test(2)
        sql, params = q.call_args.args
        self.assertIn("nostos_wx.leaderboard_test", sql)
        self.assertEqual(params, (2, 2))
        wx_scores.city_accuracy()
        self.assertIn("nostos_wx.city_accuracy", q.call_args.args[0])
        wx_scores.city_timeline("new_york")
        sql, params = q.call_args.args
        self.assertIn("nostos_wx.city_timeline", sql)
        self.assertEqual(params, ("new_york",))

    def test_view_bad_input(self, q):
        for bad in ("Austin", "austin;drop", "", "a" * 41):
            with self.assertRaises(ValueError):
                wx_scores.city_timeline(bad)
        with self.assertRaises(ValueError):
            wx_scores.leaderboard_test(0)
        q.assert_not_called()


if __name__ == "__main__":
    unittest.main()
