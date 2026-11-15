import unittest

from ingest.global_points import global_points


class GlobalPointTests(unittest.TestCase):
    def test_us_cities_have_global_gfs_coverage(self):
        points = global_points()
        us = {point_id for point_id, _, country, _, _ in points if country == "US"}
        self.assertIn("glb_new_york", us)
        self.assertIn("glb_los_angeles", us)
        self.assertEqual(len({point[0] for point in points}), len(points))


if __name__ == "__main__":
    unittest.main()
