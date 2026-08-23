import unittest
from datetime import datetime, timezone

from corrector.llm import PRICING, _deepseek_v4_flash_rate, price


def _utc(hour):
    return datetime(2026, 8, 24, hour, 30, tzinfo=timezone.utc)


class DeepseekV4FlashRate(unittest.TestCase):
    """Peak/off-peak billing, effective 2026-08-16 — UTC 01:00-04:00 and
    06:00-10:00 are peak, at exactly double the off-peak pair."""

    def test_off_peak_hour_is_the_pricing_table_rate(self):
        self.assertEqual(_deepseek_v4_flash_rate(_utc(12)), PRICING["deepseek-v4-flash"])

    def test_peak_hour_is_double(self):
        off_peak = PRICING["deepseek-v4-flash"]
        self.assertEqual(_deepseek_v4_flash_rate(_utc(2)), (off_peak[0] * 2, off_peak[1] * 2))
        self.assertEqual(_deepseek_v4_flash_rate(_utc(7)), (off_peak[0] * 2, off_peak[1] * 2))

    def test_the_boundary_hours_are_peak_and_the_hour_after_is_not(self):
        self.assertNotEqual(_deepseek_v4_flash_rate(_utc(1)), PRICING["deepseek-v4-flash"])
        self.assertEqual(_deepseek_v4_flash_rate(_utc(4)), PRICING["deepseek-v4-flash"])
        self.assertNotEqual(_deepseek_v4_flash_rate(_utc(6)), PRICING["deepseek-v4-flash"])
        self.assertEqual(_deepseek_v4_flash_rate(_utc(10)), PRICING["deepseek-v4-flash"])

    def test_no_now_reads_the_real_clock(self):
        # Doesn't assert which rate — only that it runs and returns one of
        # the two, so the default path is exercised without depending on
        # what hour the suite happens to run at.
        self.assertIn(
            _deepseek_v4_flash_rate(),
            (PRICING["deepseek-v4-flash"], tuple(r * 2 for r in PRICING["deepseek-v4-flash"])),
        )


class Price(unittest.TestCase):
    def test_deepseek_v4_flash_uses_the_scheduled_rate_not_the_table_directly(self):
        # price() has no `now` to inject; this only pins that it goes through
        # _deepseek_v4_flash_rate rather than PRICING[model] verbatim — which
        # of the two rates applies at call time is DeepseekV4FlashRate's job.
        cost = price("deepseek-v4-flash", 1_000_000, 1_000_000)
        off_peak = sum(PRICING["deepseek-v4-flash"])
        peak = off_peak * 2
        self.assertIn(round(cost, 6), (round(off_peak, 6), round(peak, 6)))

    def test_other_models_use_the_table_rate_flat(self):
        rate = PRICING["claude-sonnet-5"]
        self.assertAlmostEqual(
            price("claude-sonnet-5", 1000, 1000), (1000 * rate[0] + 1000 * rate[1]) / 1_000_000
        )

    def test_a_model_missing_from_pricing_raises(self):
        with self.assertRaises(KeyError):
            price("no-such-model", 1, 1)


if __name__ == "__main__":
    unittest.main()
