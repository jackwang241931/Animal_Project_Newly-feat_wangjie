"""Focused, hardware-free checks for logic synchronized from the source app."""

import tempfile
import unittest
from pathlib import Path

from public.dao.SQLite.Monitor_Datas_Handle import Monitor_Datas_Handle
from public.function.weight.weight_window_assembler import WeightWindowAssembler
from Service.UFC_UGC_ZOS_Service.function.calibration_log import CalibrationLogRecorder
from Service.UFC_UGC_ZOS_Service.function.gas_path_system.Gas_path_system import (
    UFC_gas_path_system_start_thread,
)


class SourceLogicSyncTests(unittest.TestCase):
    def test_weight_epoch_tail_is_backfilled_without_consuming_source_round(self):
        assembler = WeightWindowAssembler(1000, 0)
        assembler.add_packet(1, list(range(30)), 20)
        first = assembler.pop_epoch_window(1)
        self.assertEqual(first.padding_points, 10)
        self.assertEqual(first.values[:20], tuple(range(10, 30)))
        self.assertEqual(first.values[20:], (None,) * 10)

        assembler.add_packet(1, list(range(10, 40)), 30)
        second = assembler.pop_epoch_window(1)
        self.assertIsNotNone(second)
        self.assertEqual(second.values[:10], tuple(range(30, 40)))

    def test_sqlite_backfills_only_pending_weight_tail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            handle = Monitor_Datas_Handle(db_name=str(Path(temp_dir) / "weight.db"))
            manager = handle.sqlite_manager
            manager.create_table("Epoch_data_cage_1", {
                "id": "INTEGER PRIMARY KEY",
                "time": "TEXT",
                "mouse_cage_number": "INTEGER",
                "WM_weight_num": "TEXT",
            })
            manager.insert(
                "Epoch_data_cage_1", time="epoch-1", mouse_cage_number=1,
                WM_weight_num=','.join(str(value) for value in range(20)) + ',None' * 10,
            )
            handle._record_weight_window_state({
                "module_name": "Epoch", "time": "epoch-1",
                "_weight_window_state": {
                    "cage_number": 1, "origin_time": 1000,
                    "start_time": 1000, "end_time": 1030,
                    "resolved_points": 20,
                },
            }, "Epoch_data_cage_1")
            success, _ = handle.backfill_epoch_weight_rows({
                "origin_time": 1000, "cage_number": 1,
                "sample_interval_seconds": 1, "packet_tick": 30,
                "values": list(range(30)),
            })
            self.assertTrue(success)
            with manager.get_connection() as connection:
                row = connection.execute(
                    'SELECT WM_weight_num FROM "Epoch_data_cage_1"'
                ).fetchone()
                state = connection.execute(
                    'SELECT resolved_points FROM "Weight_window_state_meta"'
                ).fetchone()
            self.assertEqual(row[0].split(',')[20:], [f"{value:.2f}" for value in range(20, 30)])
            self.assertEqual(state[0], 30)
            handle.stop()

    def test_ufc_flow_response_must_echo_channel_and_value(self):
        checker = UFC_gas_path_system_start_thread._require_flow_setpoint_response
        valid = {"send_state": True, "response_hex": "0206001003660000"}
        self.assertIs(checker(valid, 1, 870), valid)
        with self.assertRaises(RuntimeError):
            checker({"send_state": True, "response_hex": "0206001103660000"}, 1, 870)

    def test_calibration_log_is_written_before_gui_consumes_event(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            recorder = CalibrationLogRecorder(directory=temp_dir)
            path = recorder.record("ZERO_CALIBRATION", {
                "type": "set_start_zero_calibration_time",
                "value": "2026-09-16 12:00:00",
            })
            self.assertIsNotNone(path)
            self.assertIn("set_start_zero_calibration_time", Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
