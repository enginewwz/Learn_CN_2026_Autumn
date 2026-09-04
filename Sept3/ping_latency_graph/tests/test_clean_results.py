import csv
import tempfile
import unittest
from pathlib import Path

from clean_results import METRICS, clean_results, structural_rejection


def valid_row(**overrides):
    row = {
        "domain": "example.com",
        "ip": "93.184.216.34",
        "http_code": "200",
        "c_latency_ms": "10",
        "ping_ms": "20",
        "dns_ms": "5",
        "tcp_transfer_ms": "10",
        "total_time_ms": "100",
        "error": "",
    }
    row.update(overrides)
    return row


class CleanResultsTest(unittest.TestCase):
    def test_valid_row_is_accepted(self):
        self.assertIsNone(structural_rejection(valid_row(), False))

    def test_fake_ip_is_rejected(self):
        self.assertEqual(
            structural_rejection(valid_row(ip="198.18.1.2"), False),
            "non_global_ip",
        )

    def test_ping_below_c_latency_is_rejected(self):
        self.assertEqual(
            structural_rejection(valid_row(ping_ms="5"), False),
            "ping_below_c_latency",
        )
        self.assertIsNone(structural_rejection(valid_row(ping_ms="5"), True))

    def test_clean_results_writes_kept_and_rejection_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.csv"
            output = root / "clean.csv"
            rejected = root / "rejected.csv"
            fields = ["domain", "ip", "http_code", *METRICS, "error"]
            with source.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerow(valid_row())
                writer.writerow(valid_row(domain="bad.test", ip="198.18.1.2"))
            total, kept, reasons, _ = clean_results(
                source, output, rejected, None, False
            )
            self.assertEqual((total, kept), (2, 1))
            self.assertEqual(reasons["non_global_ip"], 1)
            with rejected.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["cleaning_reason"], "non_global_ip")


if __name__ == "__main__":
    unittest.main()
