import csv
import tempfile
import unittest
from pathlib import Path

from measure_websites import PING_SUMMARY_RE, Target, clean_row, haversine_km, load_targets
from plot_results import ecdf_points, number


class MeasurementHelpersTest(unittest.TestCase):
    def test_haversine_known_equatorial_degree(self):
        self.assertAlmostEqual(haversine_km(0, 0, 0, 1), 111.195, places=2)

    def test_ping_parser_selects_minimum_rtt(self):
        match = PING_SUMMARY_RE.search("rtt min/avg/max/mdev = 12.1/15.2/20.0/1.0 ms")
        self.assertIsNotNone(match)
        self.assertEqual(float(match.group(1)), 12.1)

    def test_load_targets_accepts_csv_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sites.csv"
            path.write_text(
                "rank,domain\n1,Example.COM\n2,https://openai.com/path\n3,example.com\n",
                encoding="utf-8",
            )
            self.assertEqual(
                load_targets(path, 10),
                [Target(1, "example.com"), Target(2, "openai.com")],
            )

    def test_clean_row_formats_missing_and_float_values(self):
        row = clean_row({"rank": 1, "domain": "example.com", "ping_ms": 1.23456789})
        self.assertEqual(row["ping_ms"], "1.234568")
        self.assertEqual(row["dns_ms"], "")

    def test_ecdf_clips_values_to_display_range(self):
        x, y = ecdf_points([0.5, 2, 2000], x_min=1, x_max=1000)
        self.assertEqual(x, [1, 1, 2, 1000, 1000])
        self.assertEqual(y, [0, 1 / 3, 2 / 3, 1, 1])

    def test_number_rejects_non_finite(self):
        self.assertIsNone(number("nan"))
        self.assertIsNone(number(""))
        self.assertEqual(number("1.25"), 1.25)


if __name__ == "__main__":
    unittest.main()
