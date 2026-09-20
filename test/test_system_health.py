import queue
import unittest

from public.system_health import (
    ExperimentRunState,
    HealthReporter,
    HealthSnapshot,
    HealthState,
    HealthStore,
)


class HealthStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = HealthStore(clock=lambda: 0.0)

    def test_one_failure_is_a_warning_and_three_failures_are_a_fault(self):
        first = self.store.record_failure("monitor_data", reason_code="modbus_timeout", now=1)
        second = self.store.record_failure("monitor_data", reason_code="modbus_timeout", now=2)
        third = self.store.record_failure("monitor_data", reason_code="modbus_timeout", now=3)

        self.assertEqual(first.state, HealthState.WARNING)
        self.assertEqual(first.consecutive_failures, 1)
        self.assertEqual(second.state, HealthState.WARNING)
        self.assertEqual(third.state, HealthState.FAULT)
        self.assertEqual(third.consecutive_failures, 3)

    def test_fault_recovers_only_after_two_successes(self):
        for moment in (1, 2, 3):
            self.store.record_failure("deep_camera", reason_code="frame_read_failed", now=moment)

        first_success = self.store.record_success("deep_camera", now=4)
        recovered = self.store.record_success("deep_camera", now=5)

        self.assertEqual(first_success.state, HealthState.FAULT)
        self.assertEqual(first_success.consecutive_successes, 1)
        self.assertEqual(recovered.state, HealthState.NORMAL)
        self.assertEqual(recovered.reason_code, "recovered")
        self.assertEqual(recovered.consecutive_failures, 0)

    def test_external_occupation_is_unavailable_not_a_hardware_fault(self):
        unavailable = self.store.record_unavailable(
            "infrared_camera",
            reason_code="external_occupied",
            message="The camera is used by another program.",
            now=1,
        )

        self.assertEqual(unavailable.state, HealthState.UNAVAILABLE)
        self.assertEqual(unavailable.consecutive_failures, 0)
        self.assertIn(unavailable, self.store.active_issues())
        self.assertEqual(self.store.overall_state(), HealthState.UNAVAILABLE)

    def test_stopped_experiment_does_not_create_a_stale_fault(self):
        self.store.record_success("monitor_data", now=0)

        changed = self.store.check_staleness(
            now=1_000,
            experiment_state=ExperimentRunState.STOPPED,
        )

        self.assertEqual(changed, [])
        self.assertEqual(self.store.get("monitor_data").state, HealthState.NORMAL)

    def test_stopping_experiment_does_not_turn_an_intentional_shutdown_into_fault(self):
        self.store.record_success("deep_camera", now=0)

        changed = self.store.check_staleness(
            now=1_000,
            experiment_state=ExperimentRunState.STOPPING,
        )

        self.assertEqual(changed, [])
        self.assertEqual(self.store.get("deep_camera").state, HealthState.NORMAL)

    def test_running_experiment_marks_stale_collector_as_fault(self):
        self.store.record_success(
            "monitor_data",
            metrics={"sampling_interval_seconds": 4},
            now=0,
        )

        self.assertEqual(
            self.store.check_staleness(now=11, experiment_state=ExperimentRunState.RUNNING),
            [],
        )
        changed = self.store.check_staleness(now=13, experiment_state=ExperimentRunState.RUNNING)

        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0].state, HealthState.FAULT)
        self.assertEqual(changed[0].reason_code, "data_stale")

    def test_unknown_first_sample_is_not_fabricated_into_a_failure(self):
        self.store.update(HealthSnapshot(component="trajectory_service", observed_at=0))

        changed = self.store.check_staleness(now=1_000, experiment_state=ExperimentRunState.RUNNING)

        self.assertEqual(changed, [])
        self.assertEqual(self.store.get("trajectory_service").state, HealthState.UNKNOWN)

    def test_data_anomaly_is_warning_not_proof_of_broken_hardware(self):
        snapshot = self.store.record_data_anomaly(
            "monitor_data", reason_code="data_out_of_range", now=1
        )

        self.assertEqual(snapshot.state, HealthState.WARNING)
        self.assertEqual(snapshot.consecutive_failures, 0)

    def test_warning_outranks_external_unavailability_in_top_summary(self):
        self.store.record_unavailable("deep_camera", now=1)
        self.store.record_failure("monitor_data", now=2)

        self.assertEqual(self.store.overall_state(), HealthState.WARNING)

    def test_expected_unobserved_component_keeps_top_summary_unknown(self):
        store = HealthStore(expected_components={"monitor_collection", "deep_camera"})
        store.record_success("monitor_collection", now=1)

        self.assertEqual(store.overall_state(), HealthState.UNKNOWN)


class HealthReporterTests(unittest.TestCase):
    def test_reporter_emits_a_standalone_health_message(self):
        health_queue = queue.Queue()
        reporter = HealthReporter(health_queue, "gas_path")

        snapshot = reporter.failure("device_fault_code", force_fault=True, now=5)
        message = health_queue.get_nowait()

        self.assertEqual(snapshot.state, HealthState.FAULT)
        self.assertEqual(message["type"], "system_health_snapshot")
        self.assertEqual(message["component"], "gas_path")
        self.assertEqual(message["reason_code"], "device_fault_code")

    def test_store_rejects_unrelated_runtime_log_message(self):
        store = HealthStore()

        accepted = store.ingest({"type": "runtime_log", "message": "not health"})

        self.assertFalse(accepted)
        self.assertEqual(store.snapshots(), [])


if __name__ == "__main__":
    unittest.main()
