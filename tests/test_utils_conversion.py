import unittest

from crestron_cli.utils import percent_to_raw, raw_to_percent


class ConversionMathTests(unittest.TestCase):
    def test_percent_to_raw_uses_half_up_rounding(self) -> None:
        self.assertEqual(percent_to_raw(35), 22937)
        self.assertEqual(percent_to_raw(10), 6554)
        self.assertEqual(percent_to_raw(0), 0)
        self.assertEqual(percent_to_raw(100), 65535)

    def test_percent_to_raw_clamps_bounds(self) -> None:
        self.assertEqual(percent_to_raw(-1), 0)
        self.assertEqual(percent_to_raw(101), 65535)

    def test_raw_to_percent_uses_half_up_rounding_to_tenth(self) -> None:
        self.assertEqual(raw_to_percent(22937), 35.0)
        self.assertEqual(raw_to_percent(22938), 35.0)
        self.assertEqual(raw_to_percent(65535), 100.0)
        self.assertEqual(raw_to_percent(0), 0.0)

    def test_raw_to_percent_clamps_bounds(self) -> None:
        self.assertEqual(raw_to_percent(-1), 0.0)
        self.assertEqual(raw_to_percent(999999), 100.0)


if __name__ == "__main__":
    unittest.main()
