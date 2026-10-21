# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['E:\\codes\\pccpHOST\\wb-monitor\\fipedasREAD\\run.py'],
    pathex=['E:\\codes\\pccpHOST\\wb-monitor\\fipedasREAD\\src'],
    binaries=[],
    datas=[],
    hiddenimports=['fipedas_read', 'fipedas_read.main', 'fipedas_read.viewer', 'fipedas_read.worker', 'fipedas_read.data_loader', 'fipedas_read.preprocess', 'fipedas_read.settings', 'fipedas_read.logger', 'PyQt5', 'PyQt5.sip', 'numpy', 'pyqtgraph', 'scipy', 'scipy.signal', 'h5py'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt6', 'PyQt6.QtCore', 'PyQt6.QtGui', 'PyQt6.QtWidgets', 'PySide2', 'PySide2.QtCore', 'PySide2.QtGui', 'PySide2.QtWidgets', 'PySide6', 'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets', 'IPython', 'matplotlib', 'pandas', 'openpyxl', 'sqlalchemy', 'numba', 'llvmlite', 'OpenGL', 'tkinter', 'jedi', 'zmq', 'torch'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='FIPeDASReplay-2026.10.14',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['C:\\Users\\QiGh\\AppData\\Local\\Temp\\fipedas_build_icon.ico'],
)
