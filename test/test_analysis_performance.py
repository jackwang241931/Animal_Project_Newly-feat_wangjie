"""Regression checks for large data-analysis previews."""

import os
import csv
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from PyQt6.QtCore import QCoreApplication

from public.component.analysis_performance import extrema_preview, sleep_preview, sleep_segments
from public.component.workflow_pages import _AnalysisCsvExporter, _AnalysisPreviewLoader, _AnalysisWorkContext
from Module.mouse_trajectory_analysis.analysis_core import load_trajectory_experiment


class AnalysisPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QCoreApplication.instance() or QCoreApplication([])

    def test_extrema_preview_keeps_full_range_and_spike(self):
        points = [(float(index), 1000.0 if index == 32123 else 1.0) for index in range(50000)]
        preview = extrema_preview(points, 3000)
        self.assertLessEqual(len(preview), 3000)
        self.assertEqual(preview[0], points[0])
        self.assertEqual(preview[-1], points[-1])
        self.assertIn(points[32123], preview)

    def test_sleep_segments_and_bounded_cells(self):
        points = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 0.0), (30.0, 0.0, 0.0)]
        states = sleep_segments(points, [0.0, 1.0, 11.0, 12.0], 10.0)
        self.assertEqual(states, [1.0, 0.0])
        preview, multiplier = sleep_preview([None, 0.0, 1.0, 0.0] * 1000, 100)
        self.assertLessEqual(len(preview), 100)
        self.assertEqual(multiplier, 40)
        self.assertIn(1.0, preview)

    def test_background_request_bounds_large_database_preview(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "data.db"
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute(
                    "CREATE TABLE Epoch_data_cage_1 (time REAL, WM_weight_num REAL, CO2_num REAL)"
                )
                connection.executemany(
                    "INSERT INTO Epoch_data_cage_1 VALUES (?, ?, ?)",
                    (
                        (float(index), 999.0 if index == 32123 else 20.0, float(index % 10))
                        for index in range(50000)
                    ),
                )
                connection.commit()
            request = {
                "context": {"experiment_db_path": os.fspath(db_path)},
                "module": "3d",
                "trajectory_channels": (1,),
                "bio_channel": 1,
                "trajectory_export_path": None,
                "trajectory_selection_user_set": False,
                "excel_path": None,
                "comparison_origin": 0.0,
                "comparison_cache": {},
                "left_channels": (1,),
                "right_channels": (1,),
                "left_metric": 0,
                "right_metric": 4,
                "left_metric_label": "称重",
                "right_metric_label": "二氧化碳",
                "behavior_categories": ("自由活动",),
            }
            worker = _AnalysisPreviewLoader(("test",), request)
            result = []
            errors = []
            worker.loaded.connect(lambda _key, payload: result.append(payload))
            worker.failed.connect(lambda _key, error: errors.append(error))
            worker.run()
            self.assertFalse(errors, errors)
            self.assertEqual(len(result), 1)
            left, right = result[0]["compare"]
            self.assertLessEqual(len(left[0][1]), 3000)
            self.assertLessEqual(len(right[0][1]), 3000)
            self.assertEqual(left[0][1][0][0], "0.0")
            self.assertEqual(left[0][1][-1][0], "49999.0")
            self.assertIn(999.0, [value for _time, value in left[0][1]])
            full = _AnalysisWorkContext(request, lambda: False)._metric_channel_series({1}, 0)
            self.assertEqual(len(full[0][1]), 50000)
            detail = _AnalysisWorkContext(request, lambda: False)._metric_channel_series(
                {1}, 0, preview_limit=20000, time_range=(10000.0, 11000.0)
            )
            self.assertEqual(detail[0][1][0][0], "10000.0")
            self.assertEqual(detail[0][1][-1][0], "11000.0")
            self.assertEqual(len(detail[0][1]), 1001)
            export_path = Path(folder) / "comparison.csv"
            exporter = _AnalysisCsvExporter("compare", os.fspath(export_path), "对比数据", request)
            messages = []
            exporter.completed.connect(messages.append)
            exporter.run()
            self.assertEqual(len(messages), 1)
            self.assertIn("已导出", messages[0])
            with export_path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.reader(stream)
                header = next(reader)
                rows = list(reader)
            self.assertEqual(header[2], "左轴指标")
            self.assertEqual(len(rows), 50000)
            self.assertEqual(rows[0][3], "0.0")
            self.assertEqual(rows[-1][3], "49999.0")
            self.assertEqual(rows[32123][4], "999.0")

    def test_trajectory_preview_reads_only_selected_cages(self):
        with tempfile.TemporaryDirectory() as folder:
            export = Path(folder) / "20260921_120000_000"
            for channel in (1, 2):
                csv_path = export / f"cage_{channel}" / "data" / "trajectory.csv"
                csv_path.parent.mkdir(parents=True)
                with csv_path.open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=("captureTimestamp", "X", "Y", "status"))
                    writer.writeheader()
                    writer.writerow({
                        "captureTimestamp": "2026-09-21 12:00:01",
                        "X": str(channel),
                        "Y": "10",
                        "status": "ok",
                    })
            preview = load_trajectory_experiment(
                export, selected_channels={1}, include_comparison=False
            )
            self.assertEqual(preview.channels[1].total_rows, 1)
            self.assertEqual(preview.channels[2].total_rows, 0)
            self.assertFalse(preview.comparison_series)
            request = {
                "context": {},
                "module": "3d",
                "trajectory_channels": (1,),
                "bio_channel": 1,
                "trajectory_export_path": os.fspath(export),
                "trajectory_selection_user_set": True,
                "excel_path": None,
                "comparison_origin": 0.0,
                "comparison_cache": {},
                "left_channels": (),
                "right_channels": (),
                "left_metric": 0,
                "right_metric": 0,
                "behavior_categories": (),
            }
            output = Path(folder) / "trajectory_export.csv"
            exporter = _AnalysisCsvExporter("trajectory", os.fspath(output), "轨迹数据", request)
            exporter.run()
            with output.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1][0], "CH01")
            self.assertEqual(rows[1][4], "")


if __name__ == "__main__":
    unittest.main()
