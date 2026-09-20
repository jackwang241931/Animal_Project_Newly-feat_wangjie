from my_abc.BaseInterfaceWidget import BaseInterfaceWidget
from my_abc.BaseModule import BaseModule
from my_abc.BaseService import BaseService
from public.entity.BaseWindow import BaseWindow
from public.entity.enum.Public_Enum import AppState, BaseInterfaceType

class MouseTrajectoryAnalysisService(BaseService):
    def start(self, resolve, reject):
        resolve()

    def stop(self):
        pass


class MouseTrajectoryAnalysisInterface(BaseInterfaceWidget):
    def __init__(self, create_window: bool = True):
        super().__init__()
        self.type = self.get_type()
        self.frame_obj = self.create_middle_window() if create_window else None
        self.left_frame_obj = self.create_left_window()
        self.right_frame_obj = self.create_right_window()
        self.bottom_frame_obj = self.create_bottom_window()

    def get_type(self):
        return BaseInterfaceType.WIDGET

    def create_middle_window(self) -> BaseWindow:
        from Module.mouse_trajectory_analysis.index.analysis_window import TrajectoryAnalysisWindow

        return TrajectoryAnalysisWindow()

    def create_left_window(self) -> BaseWindow:
        return None

    def create_right_window(self) -> BaseWindow:
        return None

    def create_bottom_window(self) -> BaseWindow:
        return None


class MainMouseTrajectoryAnalysisModule(BaseModule):
    toolbar_order = 1

    def __init__(self):
        super().__init__()
        self.selected_experiment_path: str | None = None
        # The module loader runs during application startup. Build the chart-heavy
        # interface only after the user opens this module.
        self.interface_widget = MouseTrajectoryAnalysisInterface(create_window=False)
        self.name = self.get_name()
        self.title = self.get_title()
        self.menu_name = self.get_menu_name()
        self.service = self.create_service()
        self.app_state = self.get_app_state()

    def get_app_state(self) -> AppState:
        return AppState.INITIALIZED

    def get_name(self):
        return "MainMouseTrajectoryAnalysis"

    def get_title(self):
        return "行为规律"

    def get_menu_name(self):
        return {"id": 3, "text": "数据分析"}

    def create_service(self) -> BaseService:
        return MouseTrajectoryAnalysisService()

    def get_interface_widget(self) -> BaseInterfaceWidget:
        interface = MouseTrajectoryAnalysisInterface()
        interface.module = self
        interface.frame_obj.set_preferred_experiment_path(self.selected_experiment_path)
        interface.frame_obj.experiment_selection_changed.connect(
            self._remember_selected_experiment
        )
        return interface

    def _remember_selected_experiment(self, path: str):
        self.selected_experiment_path = path


class DataComparisonInterface(BaseInterfaceWidget):
    def __init__(self, create_window: bool = True):
        super().__init__()
        self.type = self.get_type()
        self.frame_obj = self.create_middle_window() if create_window else None
        self.left_frame_obj = self.create_left_window()
        self.right_frame_obj = self.create_right_window()
        self.bottom_frame_obj = self.create_bottom_window()

    def get_type(self):
        return BaseInterfaceType.WIDGET

    def create_middle_window(self) -> BaseWindow:
        from Module.mouse_trajectory_analysis.index.analysis_window import DataComparisonWindow

        return DataComparisonWindow()

    def create_left_window(self) -> BaseWindow:
        return None

    def create_right_window(self) -> BaseWindow:
        return None

    def create_bottom_window(self) -> BaseWindow:
        return None


class MainDataComparisonModule(BaseModule):
    toolbar_order = 2

    def __init__(self):
        super().__init__()
        self.selected_experiment_path: str | None = None
        self.interface_widget = DataComparisonInterface(create_window=False)
        self.name = self.get_name()
        self.title = self.get_title()
        self.menu_name = self.get_menu_name()
        self.service = self.create_service()
        self.app_state = self.get_app_state()

    def get_app_state(self) -> AppState:
        return AppState.INITIALIZED

    def get_name(self):
        return "MainDataComparison"

    def get_title(self):
        return "数据对比"

    def get_menu_name(self):
        return {"id": 3, "text": "数据分析"}

    def create_service(self) -> BaseService:
        return MouseTrajectoryAnalysisService()

    def get_interface_widget(self) -> BaseInterfaceWidget:
        interface = DataComparisonInterface()
        interface.module = self
        interface.frame_obj.set_preferred_experiment_path(self.selected_experiment_path)
        interface.frame_obj.experiment_selection_changed.connect(
            self._remember_selected_experiment
        )
        return interface

    def _remember_selected_experiment(self, path: str):
        self.selected_experiment_path = path
