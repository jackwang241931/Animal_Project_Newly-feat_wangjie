# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['../main.py'],
    pathex=['.', '.venv/lib/site-packages'],
    binaries=[

    ],
    datas=[
    ('D:/WorkSpace/PythonProject/Animal_Project_newly/config/camera_config.ini', 'config'),
    ('D:/WorkSpace/PythonProject/Animal_Project_newly/config/deep_camera_intrinsics.json', 'config'),
    ('D:/WorkSpace/PythonProject/Animal_Project_newly/config/gui_config.ini', 'config'),
    ('D:/WorkSpace/PythonProject/Animal_Project_newly/config/monitor_datas_config.ini', 'config'),
    ('D:/WorkSpace/PythonProject/Animal_Project_newly/model/yolo11n.pt', 'model')
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='main',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='main',
)
