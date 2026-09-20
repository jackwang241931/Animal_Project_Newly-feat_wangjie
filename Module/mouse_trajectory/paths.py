from configparser import ConfigParser
from datetime import datetime
import os
from pathlib import Path
import re


PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent.parent
CALIBRATION_DIR = PACKAGE_DIR / "calibration"
CALIBRATION_IMAGES_DIR = CALIBRATION_DIR / "images"
CALIBRATION_JSON_DIR = CALIBRATION_DIR / "json"
MODEL_DIR = PROJECT_ROOT / "model"


def _load_camera_config_parser() -> ConfigParser | None:
    config_path = PROJECT_ROOT / "config" / "camera_config.ini"
    if not config_path.exists():
        return None

    parser = ConfigParser()
    try:
        parser.read(config_path, encoding="utf-8")
        return parser
    except Exception:
        return None


def _resolve_export_dir() -> Path:
    default_dir = PROJECT_ROOT / "exports" / "mouse_trajectory"
    parser = _load_camera_config_parser()
    if parser is None:
        return default_dir

    try:
        configured_dir = parser.get("MOUSE_TRAJECTORY", "export_dir", fallback="").strip()
        if not configured_dir:
            return default_dir

        export_dir = Path(configured_dir)
        if not export_dir.is_absolute():
            export_dir = (PROJECT_ROOT / export_dir).resolve()
        return export_dir
    except Exception:
        return default_dir


EXPORT_DIR = _resolve_export_dir()


def _resolve_export_root(export_root: Path | str | None = None) -> Path:
    if export_root is None:
        root = EXPORT_DIR
    else:
        root = Path(export_root)
        if not root.is_absolute():
            root = (EXPORT_DIR / root).resolve()

    root.mkdir(parents=True, exist_ok=True)
    return root


def _sanitize_path_component(value: str) -> str:
    text = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(value).strip())
    return text.strip("._") or "experiment"


def create_experiment_export_dir(prefix: str = "experiment") -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    base_name = f"{_sanitize_path_component(prefix)}_{timestamp}_pid{os.getpid()}"
    candidate = EXPORT_DIR / base_name
    suffix = 1

    while candidate.exists():
        suffix += 1
        candidate = EXPORT_DIR / f"{base_name}_{suffix}"

    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


def first_existing_path(*paths: Path) -> Path | None:
    for path in paths:
        if path is not None and path.exists():
            return path
    return None


def _resolve_configured_model_path(option_name: str) -> Path | None:
    parser = _load_camera_config_parser()
    if parser is None or not parser.has_section("MOUSE_TRAJECTORY"):
        return None

    configured_value = parser.get("MOUSE_TRAJECTORY", option_name, fallback="").strip()
    if not configured_value:
        return None

    configured_path = Path(configured_value)
    if not configured_path.is_absolute():
        configured_path = (PROJECT_ROOT / configured_path).resolve()

    return configured_path if configured_path.exists() else None


DEFAULT_REFERENCE_IMAGE_PATH = first_existing_path(
    CALIBRATION_IMAGES_DIR / "WIN_20260419_15_45_44_Pro.jpg",
    CALIBRATION_IMAGES_DIR / "top.jpg",
)

DEFAULT_IMAGE_REGISTRATION_JSON_PATH = first_existing_path(
    CALIBRATION_JSON_DIR / "WIN_20260419_15_45_44_Pro_image_registration.json",
)

DEFAULT_GRID_JSON_PATH = first_existing_path(
    CALIBRATION_JSON_DIR / "WIN_20260419_15_45_44_Pro_grid_completed.json",
    CALIBRATION_JSON_DIR / "WIN_20260402_15_55_10_Pro_grid_completed.json",
)

DEFAULT_INSTRUMENT_AREA_JSON_PATH = first_existing_path(
    CALIBRATION_JSON_DIR / "仪器区域.json",
)

DEFAULT_BOX_MODEL_PATH = first_existing_path(
    _resolve_configured_model_path("box_model_path"),
    MODEL_DIR / "best_box_fp16.onnx",
    MODEL_DIR / "best_box.onnx",
    MODEL_DIR / "best_box.pt",
    MODEL_DIR / "best.pt",
)

DEFAULT_MOUSE_MODEL_PATH = first_existing_path(
    _resolve_configured_model_path("mouse_model_path"),
    MODEL_DIR / "best_mouse_fp16.onnx",
    MODEL_DIR / "best_mouse.onnx",
    MODEL_DIR / "best_mouse.pt",
    MODEL_DIR / "best.pt",
    MODEL_DIR / "best_box.pt",
)


def get_cage_export_dir(cage_number: int, export_root: Path | str | None = None) -> Path:
    cage_dir = _resolve_export_root(export_root) / f"cage_{int(cage_number)}"
    cage_dir.mkdir(parents=True, exist_ok=True)
    return cage_dir


def get_cage_plots_dir(cage_number: int, export_root: Path | str | None = None) -> Path:
    plots_dir = get_cage_export_dir(cage_number, export_root=export_root) / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)
    return plots_dir


def get_cage_data_dir(cage_number: int, export_root: Path | str | None = None) -> Path:
    data_dir = get_cage_export_dir(cage_number, export_root=export_root) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_cage_annotated_dir(cage_number: int, export_root: Path | str | None = None) -> Path:
    annotated_dir = get_cage_export_dir(cage_number, export_root=export_root) / "annotated"
    annotated_dir.mkdir(parents=True, exist_ok=True)
    return annotated_dir


def get_cage_annotated_latest_dir(cage_number: int, export_root: Path | str | None = None) -> Path:
    latest_dir = get_cage_annotated_dir(cage_number, export_root=export_root) / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    return latest_dir


def get_cage_annotated_history_dir(cage_number: int, export_root: Path | str | None = None) -> Path:
    history_dir = get_cage_annotated_dir(cage_number, export_root=export_root) / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    return history_dir
