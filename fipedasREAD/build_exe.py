#!/usr/bin/env python
"""Build the FIP/eDAS replay viewer into a standalone Windows executable.

Default behavior:
    python build_exe.py

Common variants:
    python build_exe.py --console
    python build_exe.py --clean-only
    python build_exe.py --skip-clean
    python build_exe.py --name FIPeDASReplay-2026.10.14
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence


PROJECT_ROOT = Path(__file__).resolve().parent
ENTRY_SCRIPT = PROJECT_ROOT / "run.py"
SRC_DIR = PROJECT_ROOT / "src"
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"

ICON_CANDIDATES = (
    PROJECT_ROOT / "resources" / "logo.png",
    PROJECT_ROOT.parent / "resources" / "logo.png",
)


def default_app_name() -> str:
    now = datetime.now()
    return f"FIPeDASReplay-{now.year}.{now.month}.{now.day}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Use PyInstaller to package the project into a standalone exe."
    )
    parser.add_argument(
        "--console",
        action="store_true",
        help="Build a console-mode executable for debugging.",
    )
    parser.add_argument(
        "--skip-clean",
        action="store_true",
        help="Do not remove previous build/dist/spec outputs before packaging.",
    )
    parser.add_argument(
        "--clean-only",
        action="store_true",
        help="Only clean old packaging outputs and then exit.",
    )
    parser.add_argument(
        "--name",
        default=default_app_name(),
        help="Executable name. Default: FIPeDASReplay-YYYY.M.D",
    )
    parser.add_argument(
        "--distpath",
        default=str(DIST_DIR),
        help=f"PyInstaller dist directory. Default: {DIST_DIR}",
    )
    parser.add_argument(
        "--workpath",
        default=str(BUILD_DIR),
        help=f"PyInstaller build directory. Default: {BUILD_DIR}",
    )
    parser.add_argument(
        "--specpath",
        default=str(PROJECT_ROOT),
        help=f"Directory used to store the generated spec file. Default: {PROJECT_ROOT}",
    )
    parser.add_argument(
        "--onedir",
        action="store_true",
        help="Build a folder distribution instead of a single-file executable.",
    )
    parser.add_argument(
        "--upx-dir",
        default=None,
        help="Optional UPX directory path passed through to PyInstaller.",
    )
    return parser.parse_args()


def remove_path(path: Path) -> None:
    if not path.exists():
        return

    if path.is_dir():
        shutil.rmtree(path)
        print(f"[clean] Removed directory: {path}")
        return

    path.unlink()
    print(f"[clean] Removed file: {path}")


def clean_outputs(app_name: str, distpath: Path, workpath: Path, specpath: Path) -> None:
    spec_file = specpath / f"{app_name}.spec"
    for target in (workpath, spec_file, distpath / app_name, distpath / f"{app_name}.exe"):
        remove_path(target)


def ensure_entry_script() -> None:
    if not ENTRY_SCRIPT.exists():
        raise FileNotFoundError(f"Entry script not found: {ENTRY_SCRIPT}")
    if not SRC_DIR.exists():
        raise FileNotFoundError(f"Source directory not found: {SRC_DIR}")


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "PyInstaller is not installed. Install it first with:\n"
            "    pip install pyinstaller"
        ) from exc


def find_icon_source() -> Optional[Path]:
    for candidate in ICON_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def prepare_icon_file() -> Optional[Path]:
    """Convert the requested PNG logo into a Windows .ico for PyInstaller."""
    icon_source = find_icon_source()
    if icon_source is None:
        print("[warn] Icon source not found, skip exe icon.")
        return None

    try:
        from PIL import Image
    except ImportError:
        print("[warn] Pillow is not installed, skip exe icon. Install it with: pip install pillow")
        return None

    icon_path = Path(tempfile.gettempdir()) / "fipedas_build_icon.ico"
    with Image.open(icon_source) as image:
        image = image.convert("RGBA")
        image.save(
            icon_path,
            format="ICO",
            sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
        )
    print(f"[build] Prepared icon: {icon_path}")
    return icon_path


def build_hidden_imports() -> List[str]:
    return [
        "fipedas_read",
        "fipedas_read.main",
        "fipedas_read.viewer",
        "fipedas_read.worker",
        "fipedas_read.data_loader",
        "fipedas_read.preprocess",
        "fipedas_read.settings",
        "fipedas_read.logger",
        "PyQt5",
        "PyQt5.sip",
        "numpy",
        "pyqtgraph",
        "scipy",
        "scipy.signal",
        "h5py",
    ]


def build_excluded_modules() -> List[str]:
    return [
        "PyQt6",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        "PySide2",
        "PySide2.QtCore",
        "PySide2.QtGui",
        "PySide2.QtWidgets",
        "PySide6",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "IPython",
        "matplotlib",
        "pandas",
        "openpyxl",
        "sqlalchemy",
        "numba",
        "llvmlite",
        "OpenGL",
        "tkinter",
        "jedi",
        "zmq",
        "torch",
    ]


def build_pyinstaller_command(args: argparse.Namespace) -> List[str]:
    distpath = Path(args.distpath).resolve()
    workpath = Path(args.workpath).resolve()
    specpath = Path(args.specpath).resolve()
    src_path = SRC_DIR.resolve()
    icon_path = prepare_icon_file()

    command: List[str] = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        args.name,
        "--distpath",
        str(distpath),
        "--workpath",
        str(workpath),
        "--specpath",
        str(specpath),
        "--paths",
        str(src_path),
    ]

    if not args.onedir:
        command.append("--onefile")

    if icon_path is not None:
        command.extend(["--icon", str(icon_path)])

    if not args.console:
        command.append("--windowed")

    if args.upx_dir:
        command.extend(["--upx-dir", str(Path(args.upx_dir).resolve())])

    for hidden_import in build_hidden_imports():
        command.extend(["--hidden-import", hidden_import])

    for excluded_module in build_excluded_modules():
        command.extend(["--exclude-module", excluded_module])

    command.append(str(ENTRY_SCRIPT))
    return command


def run_command(command: Sequence[str]) -> None:
    print("[build] Running command:")
    print("        " + " ".join(f'"{part}"' if " " in part else part for part in command))
    subprocess.run(command, cwd=str(PROJECT_ROOT), check=True)


def expected_output_path(app_name: str, distpath: Path, onedir: bool) -> Path:
    if onedir:
        return distpath / app_name / f"{app_name}.exe"
    return distpath / f"{app_name}.exe"


def remove_build_directory(workpath: Path) -> None:
    if workpath.exists():
        shutil.rmtree(workpath)
        print(f"[post] Removed build directory: {workpath}")


def print_summary(exe_path: Path) -> None:
    size_mb = exe_path.stat().st_size / (1024 * 1024)
    print()
    print("[done] Packaging completed.")
    print(f"[done] EXE path: {exe_path}")
    print(f"[done] EXE size: {size_mb:.2f} MB")
    print("[done] The generated executable can run on Windows machines without a local Python installation.")


def main() -> int:
    args = parse_args()

    if os.name != "nt":
        print("[warn] This script is intended for Windows packaging. Current platform is not Windows.")

    ensure_entry_script()
    ensure_pyinstaller()

    distpath = Path(args.distpath).resolve()
    workpath = Path(args.workpath).resolve()
    specpath = Path(args.specpath).resolve()

    if not args.skip_clean:
        clean_outputs(args.name, distpath, workpath, specpath)

    if args.clean_only:
        print("[done] Clean-only mode finished.")
        return 0

    command = build_pyinstaller_command(args)
    run_command(command)

    exe_path = expected_output_path(args.name, distpath, args.onedir)
    if not exe_path.exists():
        raise FileNotFoundError(f"Packaging finished but exe was not found: {exe_path}")

    remove_build_directory(workpath)
    print_summary(exe_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
