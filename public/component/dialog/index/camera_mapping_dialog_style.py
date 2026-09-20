"""Shared visual style for the camera-to-cage mapping dialogs.

The mapping dialogs are opened from the experiment configuration page, so they
use the same light blue surface, blue primary actions and white cards as P2.
Only presentation is defined here; camera discovery and mapping persistence
remain in the existing dialog classes.
"""


CAMERA_MAPPING_DIALOG_STYLE = """
QDialog {
    background-color: #f2f8fd;
    color: #17395c;
    font-family: "Microsoft YaHei", "SimSun";
    font-size: 13px;
}

QLabel {
    color: #567b9e;
}

QLabel#tip_label {
    background-color: #eef6ff;
    border: 1px solid #d5e5f3;
    border-radius: 8px;
    color: #6c8eaf;
    padding: 8px 10px;
}

QGroupBox {
    background-color: #ffffff;
    border: 1px solid #d5e5f3;
    border-radius: 10px;
    color: #075fae;
    font-weight: 700;
    margin-top: 10px;
    padding: 12px 8px 8px 8px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 5px;
}

QComboBox {
    min-height: 30px;
    background-color: #ffffff;
    border: 1px solid #cfe1f1;
    border-radius: 7px;
    color: #17395c;
    padding: 2px 9px;
}

QComboBox:hover,
QComboBox:focus {
    border: 1px solid #258dde;
}

QComboBox::drop-down {
    border: 0;
    width: 22px;
}

QComboBox QAbstractItemView {
    background-color: #ffffff;
    border: 1px solid #b9d7f1;
    color: #17395c;
    selection-background-color: #e5f2ff;
    selection-color: #075fae;
}

QPushButton {
    min-height: 30px;
    background-color: #ffffff;
    border: 1px solid #b9d7f1;
    border-radius: 8px;
    color: #1767ad;
    padding: 4px 13px;
}

QPushButton:hover {
    background-color: #eef7ff;
    border-color: #258dde;
}

QPushButton:pressed {
    background-color: #dceeff;
}

QPushButton#refresh {
    background-color: #1f78c8;
    border-color: #1f78c8;
    color: #ffffff;
}

QPushButton#refresh:hover {
    background-color: #1767ad;
    border-color: #1767ad;
}

QDialogButtonBox QPushButton {
    min-width: 84px;
}

"""
