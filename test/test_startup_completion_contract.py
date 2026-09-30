import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _class_method(path, class_name, method_name):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == method_name:
                    return item
    raise AssertionError(f"{class_name}.{method_name} was not found in {path}")


class StartupCompletionContractTests(unittest.TestCase):
    def test_rejecting_self_check_exits_without_starting_windowless_event_loop(self):
        from PyQt6.QtWidgets import QDialog
        from Service import main_gui

        app = Mock()
        screen = Mock()
        screen.availableGeometry.return_value = Mock(height=Mock(return_value=900))
        app.primaryScreen.return_value = screen
        dialog = Mock()
        dialog.exec.return_value = QDialog.DialogCode.Rejected
        crash_handler = Mock()
        queue_thread = Mock()

        with patch.object(main_gui, "QApplication", return_value=app), patch.object(
            main_gui, "Program_self_check_index", return_value=dialog
        ), patch.object(main_gui, "CrashHandler", return_value=crash_handler), patch.object(
            main_gui, "read_queue_data_thread", queue_thread
        ), patch.object(main_gui.shared_video_frame_store, "close_reader") as close_reader:
            result = main_gui.start_qt_application()

        self.assertEqual(result, 0)
        queue_thread.stop.assert_called_once_with()
        queue_thread.requestInterruption.assert_called_once_with()
        queue_thread.wait.assert_called_once_with(2000)
        close_reader.assert_called_once_with()
        app.closeAllWindows.assert_called_once_with()
        app.exec.assert_not_called()

    def test_legacy_dialog_messages_are_forwarded_to_workflow_runtime_log(self):
        from index.MainWindow_index import MainWindow_Index

        fake = SimpleNamespace(_append_workflow_runtime_log=Mock())
        dialog = SimpleNamespace(insert_data_signal=Mock())

        MainWindow_Index._attach_legacy_dialog_runtime_log(fake, dialog, "启动")
        callback = dialog.insert_data_signal.connect.call_args.args[0]
        callback("UFC 正在启动")

        fake._append_workflow_runtime_log.assert_called_once_with("UFC 正在启动", "启动")
        self.assertTrue(dialog._workflow_log_forwarding_connected)

    def test_workflow_runtime_log_keeps_timestamp_type_and_each_message_line(self):
        from index.MainWindow_index import MainWindow_Index

        fake = SimpleNamespace(workflow_runtime_log_buffer=[])
        MainWindow_Index._append_workflow_runtime_log(
            fake,
            "ZOS 启动完成\nUFC 启动失败",
            "启动",
        )

        self.assertEqual(
            [entry["message"] for entry in fake.workflow_runtime_log_buffer],
            ["ZOS 启动完成", "UFC 启动失败"],
        )
        self.assertTrue(all(entry["type"] == "启动" for entry in fake.workflow_runtime_log_buffer))
        self.assertTrue(all(entry["time"] for entry in fake.workflow_runtime_log_buffer))

    def test_workflow_runtime_log_coalesces_immediate_duplicate_start_lines(self):
        from index.MainWindow_index import MainWindow_Index

        fake = SimpleNamespace(workflow_runtime_log_buffer=[])
        MainWindow_Index._append_workflow_runtime_log(
            fake, "2026-09-19 12:27:31.272 | ZOS 正在启动", "启动"
        )
        MainWindow_Index._append_workflow_runtime_log(
            fake, "2026-09-19 12:27:31.283 | ZOS 正在启动", "启动"
        )

        self.assertEqual(len(fake.workflow_runtime_log_buffer), 1)
        self.assertEqual(fake.workflow_runtime_log_buffer[0]["base_message"], "ZOS 正在启动")
        self.assertEqual(fake.workflow_runtime_log_buffer[0]["repeat_count"], 2)
        self.assertIn("重复 2 次", fake.workflow_runtime_log_buffer[0]["message"])

    def test_monitor_stops_before_sending_synchronous_completion(self):
        path = (
            PROJECT_ROOT
            / "Service"
            / "UFC_UGC_ZOS_Service"
            / "index"
            / "UFC_UGC_ZOS_index.py"
        )
        method = _class_method(path, "Monitor_start_state_Thread", "dosomething")
        source = ast.get_source_segment(path.read_text(encoding="utf-8-sig"), method)

        self.assertLess(source.index("self.stop()"), source.index(".send()"))

    def test_completion_callback_never_waits_for_monitor_thread_deletion(self):
        path = (
            PROJECT_ROOT
            / "Service"
            / "UFC_UGC_ZOS_Service"
            / "index"
            / "UFC_UGC_ZOS_index.py"
        )
        method = _class_method(path, "UFC_UGC_ZOS_index", "update_start_state")
        source = ast.get_source_segment(path.read_text(encoding="utf-8-sig"), method)

        self.assertNotIn("deleteLater", source)
        self.assertIn("更新启动完成状态", source)

    def test_gui_success_handler_cancels_the_failure_timer(self):
        path = PROJECT_ROOT / "index" / "MainWindow_index.py"
        method = _class_method(path, "MainWindow_Index", "_on_gas_path_start_success")
        source = ast.get_source_segment(path.read_text(encoding="utf-8-sig"), method)

        self.assertIn("_gas_path_success = True", source)
        self.assertIn("_gas_path_timeout_timer.stop()", source)
        self.assertIn("气路启动成功", source)

    def test_stale_port_cannot_start_an_unconfirmed_experiment(self):
        from index.MainWindow_index import MainWindow_Index
        from public.config_class.global_setting import global_setting

        setting = SimpleNamespace(groups=[SimpleNamespace(is_selected=True)])
        fake = SimpleNamespace(
            status_bar=SimpleNamespace(_temp_tip_active=False),
            _gas_path_timeout_timer=None,
            _reject_experiment_start=Mock(),
        )
        with patch.dict(global_setting._setting, {
            "port": "COM7", "experiment_setting": setting,
            "confirmed_serial_setting": object(), "confirmed_serial_port": "COM7",
        }):
            MainWindow_Index.start_experiment(fake)
        fake._reject_experiment_start.assert_called_once_with(
            "当前实验尚未确认串口，请先在实验配置中确认并检测。"
        )

    def test_selected_air_calibration_is_preserved_for_start(self):
        from index.MainWindow_index import MainWindow_Index
        from public.config_class.global_setting import global_setting

        fake = SimpleNamespace(workflow_runtime_log_buffer=[])
        with patch.dict(global_setting._setting, {
            "startup_calibration_mode": "air", "is_auto_calibration": True,
            "air_modules_all_valid": False, "environment_module_only": False,
        }):
            mode = MainWindow_Index._resolve_startup_calibration_mode_for_start(fake)
            self.assertEqual(mode, "air")
            self.assertEqual(global_setting.get_setting("startup_calibration_mode"), "air")
            self.assertTrue(global_setting.get_setting("is_auto_calibration"))
        self.assertEqual(fake.workflow_runtime_log_buffer, [])

        path = PROJECT_ROOT / "index" / "MainWindow_index.py"
        source = ast.get_source_segment(path.read_text(encoding="utf-8-sig"),
                                        _class_method(path, "MainWindow_Index", "start_experiment"))
        self.assertIn("_resolve_startup_calibration_mode_for_start", source)
    def test_gui_timeout_does_not_report_a_confirmed_failure(self):
        path = PROJECT_ROOT / "index" / "MainWindow_index.py"
        method = _class_method(path, "MainWindow_Index", "_on_gas_path_final_timeout")
        source = ast.get_source_segment(path.read_text(encoding="utf-8-sig"), method)

        self.assertNotIn("气路初始化失败", source)
        self.assertIn("启动等待超时", source)
        self.assertIn("后台状态未确认", source)

    def test_hidden_start_dialog_arms_a_real_completion_timeout(self):
        path = PROJECT_ROOT / "index" / "MainWindow_index.py"
        method = _class_method(path, "MainWindow_Index", "show_open_dialog")
        source = ast.get_source_segment(path.read_text(encoding="utf-8-sig"), method)

        self.assertIn("_gas_path_timeout_timer.start", source)
        self.assertNotIn("force_entered", source)

    def test_start_sends_both_baseline_camera_commands(self):
        path = PROJECT_ROOT / "index" / "MainWindow_index.py"
        method = _class_method(path, "MainWindow_Index", "start_experiment")
        source = ast.get_source_segment(path.read_text(encoding="utf-8-sig"), method)

        self.assertIn("to='main_infrared_camera', title='start'", source)
        self.assertIn("to='main_deep_camera', title='start'", source)
        self.assertIn("'infrared_enabled': infrared_enabled", source)
        self.assertIn("'behavior_enabled': behavior_enabled", source)
        self.assertNotIn("if infrared_enabled:", source)
        self.assertNotIn("if behavior_enabled:", source)

    def test_calibration_messages_do_not_fake_startup_completion_percent(self):
        from index.MainWindow_index import MainWindow_Index
        from public.system_health import ExperimentRunState

        page = SimpleNamespace(set_operation_progress=Mock(), operation_progress=SimpleNamespace(value=lambda: 37))
        fake = SimpleNamespace(
            experiment_control_page=page,
            workflow_run_state=ExperimentRunState.STARTING,
        )
        MainWindow_Index._on_operation_progress(fake, 100, "空气校准完成")
        page.set_operation_progress.assert_called_once_with(37, "空气校准完成")

        fake.workflow_run_state = ExperimentRunState.RUNNING
        MainWindow_Index._on_operation_progress(fake, 37, "校零进行中")
        page.set_operation_progress.assert_called_with(37, "校零进行中")

        path = PROJECT_ROOT / "index" / "MainWindow_index.py"
        method = _class_method(path, "read_queue_data_Thread", "dosomething")
        source = ast.get_source_segment(path.read_text(encoding="utf-8-sig"), method)
        self.assertNotIn('emit(10, "校准进行中（收到真实校准信息）")', source)

    def test_late_calibration_updates_cannot_overwrite_stop_status(self):
        from index.MainWindow_index import MainWindow_Index
        from public.system_health import ExperimentRunState

        page = SimpleNamespace(
            set_operation_progress=Mock(),
            operation_progress=SimpleNamespace(value=lambda: 37),
            on_calibration_event=Mock(),
        )
        fake = SimpleNamespace(
            experiment_control_page=page,
            workflow_run_state=ExperimentRunState.STOPPING,
            _active_calibration_channels={"span"},
        )

        MainWindow_Index._on_operation_progress(fake, 100, "校量程完成")
        MainWindow_Index._on_calibration_event(fake, "span_finished")

        page.set_operation_progress.assert_not_called()
        page.on_calibration_event.assert_not_called()
        self.assertNotIn("span", fake._active_calibration_channels)

    def test_workflow_confirmation_precedes_monitor_start(self):
        from index.MainWindow_index import MainWindow_Index
        from public.config_class.global_setting import global_setting

        queue = Mock()
        with patch.dict(global_setting._setting, {
            "startup_calibration_mode": "full", "is_auto_calibration": True,
            "environment_module_only": False, "Vr": 20.9,
            "span_standard_oxygen_value": 20.8,
            "span_standard_carbon_value": 0.05,
        }):
            MainWindow_Index._confirm_workflow_device_config(None, "COM7", queue)
        first, second = [call.args[0] for call in queue.put.call_args_list]
        self.assertEqual((first.title, first.data), ("set_port", "COM7"))
        self.assertEqual(second.title, "set_experiment_basic_config")
        self.assertEqual(second.data["startup_calibration_mode"], "full")
        self.assertEqual(second.data["span_standard_oxygen_value"], 20.8)
        self.assertEqual(second.data["span_standard_carbon_value"], 0.05)

        path = PROJECT_ROOT / "index" / "MainWindow_index.py"
        source = ast.get_source_segment(path.read_text(encoding="utf-8-sig"),
                                        _class_method(path, "MainWindow_Index", "start_experiment"))
        self.assertLess(source.index("self._confirm_workflow_device_config"), source.index("title='start'"))

    def test_only_explicit_mode_selects_startup_calibration(self):
        from index.MainWindow_index import MainWindow_Index
        from public.config_class.global_setting import global_setting

        for requested, expected in ((None, "none"), ("invalid", "none"),
                                    ("none", "none"), ("air", "air"), ("full", "full")):
            with self.subTest(requested=requested):
                queue = Mock()
                with patch.dict(global_setting._setting, {
                    "startup_calibration_mode": requested,
                    "is_auto_calibration": True,
                    "environment_module_only": False,
                }):
                    MainWindow_Index._confirm_workflow_device_config(None, "COM7", queue)
                config = queue.put.call_args_list[1].args[0].data
                self.assertEqual(config["startup_calibration_mode"], expected)
                self.assertEqual(config["is_auto_calibration"], expected != "none")

    def test_backend_missing_mode_defaults_to_direct_start(self):
        path = (PROJECT_ROOT / "Service" / "UFC_UGC_ZOS_Service" / "index"
                / "UFC_UGC_ZOS_index.py")
        method = _class_method(path, "UFC_UGC_ZOS_index", "dosomething")
        source = ast.get_source_segment(path.read_text(encoding="utf-8-sig"), method)
        self.assertIn('startup_calibration_mode = "none"', source)
        self.assertIn('if startup_calibration_mode == "full"', source)
        self.assertIn('elif startup_calibration_mode == "air"', source)
        self.assertIn('self.start_btn_handle().then(', source)

    def test_gas_startup_async_chain_is_started_only_once(self):
        path = (PROJECT_ROOT / "Service" / "UFC_UGC_ZOS_Service" / "index"
                / "UFC_UGC_ZOS_index.py")
        text = path.read_text(encoding="utf-8-sig")
        init_source = ast.get_source_segment(
            text, _class_method(path, "UFC_UGC_ZOS_index", "__init__")
        )
        run_source = ast.get_source_segment(
            text, _class_method(path, "UFC_UGC_ZOS_index", "dosomething")
        )

        self.assertIn("self._startup_chain_started = False", init_source)
        self.assertIn("if self._startup_chain_started", run_source)
        self.assertLess(
            run_source.index("self._startup_chain_started = True"),
            run_source.index("self.start_btn_handle"),
        )

    def test_startup_elapsed_progress_stays_below_completion(self):
        from index.MainWindow_index import MainWindow_Index
        from public.system_health import ExperimentRunState

        page = SimpleNamespace(
            operation_progress=SimpleNamespace(value=lambda: 0),
            set_operation_progress=Mock(),
        )
        fake = SimpleNamespace(
            workflow_run_state=ExperimentRunState.STARTING,
            experiment_control_page=page,
            _startup_progress_started_at=100.0,
            _startup_progress_duration=20.0,
            _stop_startup_progress=Mock(),
        )
        with patch("index.MainWindow_index.time.monotonic", return_value=110.0):
            MainWindow_Index._advance_startup_progress(fake)
        page.set_operation_progress.assert_called_once_with(50)
        with patch("index.MainWindow_index.time.monotonic", return_value=200.0):
            MainWindow_Index._advance_startup_progress(fake)
        page.set_operation_progress.assert_called_with(99)

    def test_stop_matches_legacy_parallel_export_and_finishes_after_both(self):
        path = PROJECT_ROOT / "index" / "MainWindow_index.py"
        text = path.read_text(encoding="utf-8-sig")
        stop_method = _class_method(path, "MainWindow_Index", "stop_experiment")
        backend_method = _class_method(path, "MainWindow_Index", "_on_stop_backend_complete")
        finalize_method = _class_method(path, "MainWindow_Index", "_maybe_finalize_stop")
        stop_source = ast.get_source_segment(text, stop_method)
        backend_source = ast.get_source_segment(text, backend_method)
        finalize_source = ast.get_source_segment(text, finalize_method)

        self.assertIn("QTimer.singleShot(0, self.start_stop_cleanup_async)", stop_source)
        self.assertIn("self.show_stop_dialog_sync()", stop_source)
        self.assertNotIn("self.stop_update_gui_sync()", stop_source)
        self.assertIn("QTimer.singleShot(5000, self._on_stop_backend_grace_elapsed)", backend_source)
        self.assertIn("not self._stop_backend_complete", finalize_source)
        self.assertIn("not self._stop_backend_grace_complete", finalize_source)
        self.assertIn("not self.stop_export_finished", finalize_source)
        self.assertIn("self.stop_update_gui_sync()", finalize_source)
        self.assertIn("self._show_pending_stop_export_success_message()", finalize_source)

    def test_stop_backend_warning_still_waits_for_parallel_legacy_export(self):
        from index.MainWindow_index import MainWindow_Index
        from public.system_health import ExperimentRunState

        fake = SimpleNamespace(
            workflow_run_state=ExperimentRunState.STOPPING,
            _stop_backend_complete=False,
            _stop_backend_grace_complete=False,
            _stop_operation_failed=True,
            _stop_timeout_timer=None,
            stop_dialog_close_pending=False,
            stop_dialog=None,
            _append_workflow_runtime_log=Mock(),
            _maybe_finalize_stop=Mock(),
        )
        fake._on_stop_backend_grace_elapsed = lambda: (
            MainWindow_Index._on_stop_backend_grace_elapsed(fake)
        )
        with patch("index.MainWindow_index.QTimer.singleShot", side_effect=lambda _, fn: fn()):
            MainWindow_Index._on_stop_backend_complete(fake)

        self.assertTrue(fake._stop_backend_complete)
        self.assertTrue(fake._stop_backend_grace_complete)
        fake._maybe_finalize_stop.assert_called_once_with()
        self.assertIn(
            "继续按旧版流程保存并导出",
            fake._append_workflow_runtime_log.call_args.args[0],
        )

    def test_stop_timeout_uses_the_same_countdown_bound_as_legacy_dialog(self):
        path = PROJECT_ROOT / "index" / "MainWindow_index.py"
        text = path.read_text(encoding="utf-8-sig")
        stop_source = ast.get_source_segment(
            text, _class_method(path, "MainWindow_Index", "stop_experiment")
        )
        timeout_source = ast.get_source_segment(
            text, _class_method(path, "MainWindow_Index", "_on_stop_experiment_timeout")
        )
        self.assertIn("int(stop_wait_seconds * 1000)", stop_source)
        self.assertIn("self._stop_backend_complete = True", timeout_source)
        self.assertIn("self._stop_backend_grace_complete = True", timeout_source)
        self.assertNotIn("self._stop_operation_failed = True", timeout_source)
        self.assertIn("self._maybe_finalize_stop()", timeout_source)

    def test_monitor_sender_uses_the_legacy_stop_receipt_without_failure_gate(self):
        path = PROJECT_ROOT / "Service" / "main_monitor_data.py"
        text = path.read_text(encoding="utf-8-sig")
        stop_function = next(
            node for node in ast.parse(text).body
            if isinstance(node, ast.FunctionDef) and node.name == "stop"
        )
        source = ast.get_source_segment(text, stop_function)

        self.assertNotIn("send_thread.wait(15000)", source)
        self.assertIn('title="stop_monitor_data_return"', source)
        self.assertIn('data="鼠笼内模块报文发送线程 已关闭"', source)

    def test_stop_archive_exports_the_database_from_the_same_experiment_folder(self):
        path = PROJECT_ROOT / "public" / "util" / "custom_data_file_util.py"
        text = path.read_text(encoding="utf-8-sig")
        method = _class_method(
            path, "custom_data_file_util", "save_folder_contents_as_custom_file"
        )
        source = ast.get_source_segment(text, method)

        self.assertIn('os.path.join(folder_path, "data", "data.db")', source)
        self.assertIn("file_path=experiment_db_file_path", source)
        self.assertIn("use_atomic_replace=True", source)

    def test_excel_export_uses_reference_cage_zero_when_runtime_config_is_missing(self):
        from public.function.Tansfer.DbTransferExcel import DbTransferExcel

        with patch(
            "public.function.Tansfer.DbTransferExcel.global_setting.get_setting",
            return_value=None,
        ):
            self.assertEqual(DbTransferExcel._get_reference_cage_id(), 0)

    def test_collection_process_uses_the_path_fixed_by_the_start_command(self):
        path = PROJECT_ROOT / "Service" / "main_monitor_data.py"
        text = path.read_text(encoding="utf-8-sig")
        method = _class_method(path, "read_queue_data_Thread", "dosomething")
        source = ast.get_source_segment(text, method)

        self.assertIn('set_setting("experiment_root_path", data.get("experiment_root_path", None))', source)
        self.assertNotIn('"experiment_storage_path"', source)

    def test_missing_stop_export_directory_follows_legacy_silent_finish(self):
        path = PROJECT_ROOT / "index" / "MainWindow_index.py"
        source = ast.get_source_segment(
            path.read_text(encoding="utf-8-sig"),
            _class_method(path, "MainWindow_Index", "stop_store_info"),
        )

        self.assertNotIn("实验数据目录不存在，无法生成 Mdata 和 Excel", source)
        self.assertNotIn("self._stop_operation_failed = True", source)
        self.assertIn("self.stop_export_finished = True", source)

    def test_stop_status_freezes_immediately_and_has_terminal_labels(self):
        path = PROJECT_ROOT / "index" / "MainWindow_index.py"
        text = path.read_text(encoding="utf-8-sig")
        stop_source = ast.get_source_segment(
            text, _class_method(path, "MainWindow_Index", "stop_experiment")
        )
        context_source = ast.get_source_segment(
            text, _class_method(path, "MainWindow_Index", "_system_status_context")
        )
        finish_source = ast.get_source_segment(
            text, _class_method(path, "MainWindow_Index", "stop_update_gui_sync")
        )

        self.assertLess(
            stop_source.index('set_setting("stop_experiment_time"'),
            stop_source.index("_set_workflow_run_state(ExperimentRunState.STOPPING)"),
        )
        self.assertIn('set_operation_progress(0, "正在停止实验")', stop_source)
        self.assertNotIn("self.setEnabled(False)", stop_source)
        self.assertIn("page.refresh()", stop_source)
        self.assertIn("ExperimentRunState.STOPPING", context_source)
        self.assertIn('set_operation_progress(100, "实验已停止")', finish_source)

    def test_stop_completion_gate_waits_for_both_conditions(self):
        from index.MainWindow_index import MainWindow_Index
        from public.system_health import ExperimentRunState

        fake = SimpleNamespace(
            workflow_run_state=ExperimentRunState.STOPPING,
            _stop_backend_complete=False,
            _stop_backend_grace_complete=False,
            stop_export_finished=False,
            _stop_timeout_timer=Mock(),
            _stop_operation_failed=False,
            stop_update_gui_sync=Mock(),
            _show_pending_stop_export_success_message=Mock(),
        )
        MainWindow_Index._maybe_finalize_stop(fake)
        fake.stop_export_finished = True
        MainWindow_Index._maybe_finalize_stop(fake)
        fake.stop_update_gui_sync.assert_not_called()
        fake._stop_backend_complete = True
        MainWindow_Index._maybe_finalize_stop(fake)
        fake.stop_update_gui_sync.assert_not_called()
        fake._stop_backend_grace_complete = True
        MainWindow_Index._maybe_finalize_stop(fake)
        fake.stop_update_gui_sync.assert_called_once_with()
        fake._show_pending_stop_export_success_message.assert_called_once_with()

    def test_camera_stop_has_terminal_receipt_even_without_camera_instances(self):
        deep_path = PROJECT_ROOT / "Service" / "main_deep_camera.py"
        infrared_path = PROJECT_ROOT / "Service" / "main_infrared_camera.py"
        deep_source = ast.get_source_segment(
            deep_path.read_text(encoding="utf-8-sig"),
            next(node for node in ast.parse(deep_path.read_text(encoding="utf-8-sig")).body
                 if isinstance(node, ast.FunctionDef) and node.name == "stop"),
        )
        infrared_source = ast.get_source_segment(
            infrared_path.read_text(encoding="utf-8-sig"),
            next(node for node in ast.parse(infrared_path.read_text(encoding="utf-8-sig")).body
                 if isinstance(node, ast.FunctionDef) and node.name == "stop"),
        )
        self.assertIn("stop_deep_camera_complete", deep_source)
        self.assertIn("stop_infrared_camera_complete", infrared_source)
        self.assertIn("data={\"success\": stop_succeeded", deep_source)
        self.assertIn("data={\"success\": stop_succeeded", infrared_source)

    def test_partial_start_has_explicit_monitor_and_gas_stop_receipts(self):
        path = PROJECT_ROOT / "Service" / "main_monitor_data.py"
        text = path.read_text(encoding="utf-8-sig")
        stop_source = ast.get_source_segment(
            text,
            next(node for node in ast.parse(text).body
                 if isinstance(node, ast.FunctionDef) and node.name == "stop"),
        )
        self.assertIn("gas_path_was_skipped = ufc_ugc_zos_thread is None", stop_source)
        self.assertIn("_notify_skipped_gas_stop_complete()", stop_source)
        self.assertIn('title="stop_monitor_data_return"', stop_source)
        self.assertNotIn('title="stop_monitor_data_blocked"', stop_source)
        self.assertIn('title="stop_show_info_except_status_counts"', stop_source)

    def test_calibration_stop_events_report_completed_statuses(self):
        path = PROJECT_ROOT / "index" / "MainWindow_index.py"
        method = _class_method(path, "read_queue_data_Thread", "dosomething")
        source = ast.get_source_segment(path.read_text(encoding="utf-8-sig"), method)

        self.assertIn('calibration_detail_status_text = "零点标定完成"', source)
        self.assertIn('calibration_detail_status_text = "量程标定完成"', source)
        self.assertIn('calibration_detail_status_text = "Air空气校准完成"', source)
        self.assertIn("updateAirEndTime", source)

    def test_air_calibration_uses_its_own_ui_log_methods(self):
        path = (
            PROJECT_ROOT
            / "public"
            / "component"
            / "dialog"
            / "custom"
            / "calibration_detail_Dialog.py"
        )
        start_method = _class_method(path, "CalibrationDialog", "updateAirStartTime")
        end_method = _class_method(path, "CalibrationDialog", "updateAirEndTime")
        text = path.read_text(encoding="utf-8-sig")
        start_source = ast.get_source_segment(text, start_method)
        end_source = ast.get_source_segment(text, end_method)

        self.assertIn("Air空气校准开始", start_source)
        self.assertIn("Air空气校准完成", end_source)
        self.assertNotIn("零点标定", start_source + end_source)

    def test_direct_run_hides_legacy_calibration_shutdown_chatter(self):
        from index.MainWindow_index import MainWindow_Index
        from public.system_health import ExperimentRunState

        fake = SimpleNamespace(
            workflow_run_state=ExperimentRunState.STOPPING,
            _experiment_calibration_mode="none",
            calibration_detail_log_buffer=[],
            calibration_details_windows=None,
            _append_workflow_runtime_log=Mock(),
        )
        fake._is_uncalibrated_stop_housekeeping = lambda message: (
            MainWindow_Index._is_uncalibrated_stop_housekeeping(fake, message)
        )

        MainWindow_Index.cache_calibration_detail_log(
            fake, "Zero_Carlibration_thread标定 9. 标定完成"
        )
        MainWindow_Index.cache_calibration_detail_log(
            fake, "停止SPan量程标定 开始"
        )
        MainWindow_Index.cache_calibration_detail_log(
            fake, "停止零点量程标定 开始"
        )

        self.assertEqual(fake.calibration_detail_log_buffer, [])
        fake._append_workflow_runtime_log.assert_not_called()

        path = PROJECT_ROOT / "index" / "MainWindow_index.py"
        source = ast.get_source_segment(
            path.read_text(encoding="utf-8-sig"),
            _class_method(path, "read_queue_data_Thread", "dosomething"),
        )
        self.assertIn(
            "self.window._is_uncalibrated_stop_housekeeping(message.data)",
            source,
        )

    def test_gas_shutdown_cancels_active_calibrations_before_stopping_paths(self):
        path = (
            PROJECT_ROOT
            / "Service"
            / "UFC_UGC_ZOS_Service"
            / "index"
            / "UFC_UGC_ZOS_index.py"
        )
        text = path.read_text(encoding="utf-8-sig")
        stop_source = ast.get_source_segment(
            text, _class_method(path, "UFC_UGC_ZOS_index", "stop_btn_handle")
        )
        calibration_source = ast.get_source_segment(
            text,
            _class_method(
                path,
                "UFC_UGC_ZOS_index",
                "_stop_active_calibrations_then_finish",
            ),
        )

        calibration_source = ast.get_source_segment(text, _class_method(path, "UFC_UGC_ZOS_index", "_request_stop_active_calibrations"))
        self.assertLess(stop_source.index("self._request_stop_active_calibrations()"), stop_source.index("self._stop_gas_paths_then_finish("))
        self.assertIn("calibration.isRunning()", calibration_source)
        self.assertIn("is_STOP", calibration_source)
        self.assertIn("stop_calibrate(", calibration_source)
        self.assertIn("calibration.stop()", calibration_source)
        finish_source = ast.get_source_segment(text, _class_method(path, "UFC_UGC_ZOS_index", "_stop_active_calibrations_then_finish"))
        self.assertIn("self._request_stop_active_calibrations()", finish_source)
        self.assertIn("return AsyPromise(self.finished_stop)", finish_source)

    def test_selected_calibration_route_keeps_stop_detail_messages(self):
        from index.MainWindow_index import MainWindow_Index
        from public.system_health import ExperimentRunState

        fake = SimpleNamespace(
            workflow_run_state=ExperimentRunState.STOPPING,
            _experiment_calibration_mode="full",
        )

        self.assertFalse(MainWindow_Index._is_uncalibrated_stop_housekeeping(
            fake, "Range_Carlibration_thread标定 9. 标定完成"
        ))

    def test_stop_state_cannot_reopen_calibration_detail_window(self):
        from index.MainWindow_index import MainWindow_Index
        from public.system_health import ExperimentRunState

        fake = SimpleNamespace(
            workflow_run_state=ExperimentRunState.STOPPING,
            init__calibration_windows=Mock(),
        )

        MainWindow_Index.show_calibration_windows(fake, None)
        fake.init__calibration_windows.assert_not_called()

    def test_new_experiment_resets_previous_calibration_detail_state(self):
        from index.MainWindow_index import MainWindow_Index

        fake = SimpleNamespace(
            release_calibration_windows=Mock(),
            calibration_detail_log_buffer=[("old", True)],
            calibration_detail_status_text="量程标定完成",
            _active_calibration_channels={"zero", "span"},
            calibration_detail_zero_start_time=1,
            calibration_detail_zero_end_time=2,
            calibration_detail_span_start_time=3,
            calibration_detail_span_end_time=4,
            _experiment_calibration_mode="full",
        )

        MainWindow_Index._reset_calibration_detail_state(fake, "none")

        fake.release_calibration_windows.assert_called_once_with()
        self.assertEqual(fake.calibration_detail_log_buffer, [])
        self.assertEqual(fake._active_calibration_channels, set())
        self.assertIsNone(fake.calibration_detail_status_text)
        self.assertEqual(fake._experiment_calibration_mode, "none")


if __name__ == "__main__":
    unittest.main()
