import unittest

from Service.UFC_UGC_ZOS_Service.function.o2_compensation.host_wet_o2_guard import (
    WetOxygenAnomalyGuard,
    WetOxygenReferenceFilter,
)


class WetOxygenReferenceFilterTests(unittest.TestCase):
    def test_first_abnormal_sample_uses_closest_usable_reference(self):
        filter_ = WetOxygenReferenceFilter(threshold=0.15)
        filter_.set_initial_references("M1", [20.90, 20.92, 21.05])

        result = filter_.filter_with_status("M1", 21.10)

        self.assertEqual(result["value"], 21.05)
        self.assertTrue(result["replaced"])
        self.assertTrue(result["accepted"])

        next_result = filter_.filter_with_status("M1", 21.10)
        self.assertEqual(next_result["value"], 21.10)
        self.assertFalse(next_result["replaced"])

        later_result = filter_.filter_with_status("M1", 21.30)
        self.assertEqual(later_result["value"], 21.10)
        self.assertTrue(later_result["replaced"])

    def test_first_sample_without_usable_reference_does_not_create_baseline(self):
        filter_ = WetOxygenReferenceFilter(threshold=0.15)
        filter_.set_initial_references("M1", [20.90, 20.92, 20.91])

        abnormal = filter_.filter_with_status("M1", 21.30)
        normal = filter_.filter_with_status("M1", 20.95)

        self.assertIsNone(abnormal["value"])
        self.assertFalse(abnormal["accepted"])
        self.assertEqual(normal["value"], 20.95)
        self.assertTrue(normal["accepted"])

    def test_guard_waits_three_cycles_then_collects_three_reference_cycles(self):
        guard = WetOxygenAnomalyGuard(
            jump_threshold=0.15,
            warmup_cycles=3,
            reference_cycles=3,
        )

        for value in (20.90, 20.91, 20.92):
            value_seen, replaced = guard.filter("M1", value)
            self.assertEqual(value_seen, value)
            self.assertFalse(replaced)
            guard.complete_cycle()

        for value in (20.90, 20.92, 21.05):
            value_seen, replaced = guard.filter("M1", value)
            self.assertEqual(value_seen, value)
            self.assertFalse(replaced)
            guard.complete_cycle()

        first_judged, replaced = guard.filter("M1", 21.10)
        self.assertEqual(first_judged, 21.05)
        self.assertTrue(replaced)

    def test_guard_keeps_channel_histories_independent(self):
        guard = WetOxygenAnomalyGuard(
            jump_threshold=0.15,
            warmup_cycles=0,
            reference_cycles=3,
        )
        for values in ((20.90, 20.91), (20.92, 20.93), (20.91, 20.92)):
            for channel, value in zip(("M1", "M2"), values):
                guard.filter(channel, value)
            guard.complete_cycle()

        m1, _ = guard.filter("M1", 20.95)
        m2, _ = guard.filter("M2", 21.20)

        self.assertEqual(m1, 20.95)
        self.assertIsNone(m2)


if __name__ == "__main__":
    unittest.main()
