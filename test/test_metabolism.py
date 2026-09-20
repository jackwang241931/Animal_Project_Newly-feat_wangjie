import math
import os
import sqlite3
import tempfile
import unittest

from public.dao.SQLite.Monitor_Datas_Handle import Monitor_Datas_Handle
from public.dao.SQLite.SQliteManager import SQLiteManager
from public.function.Monitor_data_storage.metabolism import calculate_epoch_metabolism


class MetabolismCalculationTests(unittest.TestCase):
    def setUp(self):
        self.sample = {
            "UFC_flow_num": 1000,  # 1 L/min
            "reference_dry_basis_oxygen_num": 20.93,
            "reference_CO2_num": 0.04,
            "ZOS_dry_basis_oxygen_num": 20.00,
            "UGC_CO2_num": 1.00,
        }

    def test_haldane_rer_and_hourly_weir_energy(self):
        result = calculate_epoch_metabolism(self.sample)
        vi = (1 - 0.20 - 0.01) / (1 - 0.2093 - 0.0004)
        vo2 = vi * 0.2093 - 0.20
        vco2 = 0.01 - vi * 0.0004
        self.assertAlmostEqual(result["vo2_consumption"], vo2)
        self.assertAlmostEqual(result["vco2_production"], vco2)
        self.assertAlmostEqual(result["rer_rq"], vco2 / vo2)
        self.assertAlmostEqual(result["ee_energy_expenditure"], 60 * (3.815 * vo2 + 1.232 * vco2))

    def test_missing_reference_and_invalid_flow_are_unavailable(self):
        for change in ({"reference_CO2_num": None}, {"UFC_flow_num": 0}, {"UFC_flow_num": math.nan}):
            sample = {**self.sample, **change}
            self.assertTrue(all(value is None for value in calculate_epoch_metabolism(sample).values()))

    def test_existing_epoch_tables_gain_persisted_result_columns(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            path = os.path.join(directory, "data.db")
            with sqlite3.connect(path) as connection:
                for table in ("Epoch_data_all", "Epoch_data_cage_0", "Epoch_data_cage_1"):
                    connection.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, time TEXT)")
            handle = object.__new__(Monitor_Datas_Handle)
            handle.sqlite_manager = SQLiteManager(path)
            try:
                handle._migrate_metabolism_epoch_columns([1], 0)
                with sqlite3.connect(path) as connection:
                    columns = {row[1] for row in connection.execute("PRAGMA table_info(Epoch_data_cage_1)")}
                self.assertTrue({
                    "reference_dry_basis_oxygen_num", "vo2_consumption", "vco2_production",
                    "rer_rq", "ee_energy_expenditure",
                }.issubset(columns))
            finally:
                handle.sqlite_manager.close()


if __name__ == "__main__":
    unittest.main()
