"""Run `python build_exe.py` to build a portable Windows EXE (PyInstaller)."""
import argparse
import importlib.util
import subprocess
import shutil
import sys
from datetime import datetime
from pathlib import Path


def cleanup_build(root):
    """Remove only this version's verified build directory after a successful build."""
    root = Path(root).resolve()
    target = root / 'build'
    if not target.exists():
        return
    resolved = target.resolve()
    if target.is_symlink() or resolved != target or resolved.parent != root:
        raise RuntimeError(f'拒绝清理非本版本 build 目录：{target}')
    shutil.rmtree(resolved)
    print('CLEANED:', resolved, flush=True)


def build(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--onedir', action='store_true', help='Build a folder instead of one self-extracting EXE')
    parser.add_argument('--smoke-test', action='store_true', help='Launch the built EXE in simulation and verify its GUI')
    args = parser.parse_args(argv)
    if sys.platform != 'win32': raise RuntimeError('请在 Windows 上运行，以生成 Windows EXE。')
    if importlib.util.find_spec('PyInstaller') is None:
        raise RuntimeError('请先安装打包依赖：python -m pip install -r requirements-build.txt')
    root = Path(__file__).resolve().parent
    name = 'wb-monitor-inDAS-' + datetime.now().strftime('%Y%m%d-%H%M%S')
    cmd = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed',
           '--onedir' if args.onedir else '--onefile', '--name', name,
           '--distpath', str(root / 'dist'), '--workpath', str(root / 'build' / name),
           '--specpath', str(root / 'build'), '--paths', str(root / 'src'),
           '--add-data', str(root / 'resources') + ';resources',
           '--add-data', str(root / 'config' / 'app_config.json') + ';config',
           '--add-binary', str(root / 'libs' / 'pcie7821_api.dll') + ';libs',
           '--hidden-import', 'scipy.signal', '--hidden-import', 'scipy.special',
           '--hidden-import', 'cryptography.hazmat.primitives.ciphers.aead',
           '--hidden-import', 'zstandard', '--hidden-import', 'h5py']
    for module in ('PyQt6', 'PySide2', 'PySide6', 'tkinter', 'IPython', 'jupyter',
                   'notebook', 'pytest', 'matplotlib', 'pandas', 'numba', 'torch', 'tensorflow'):
        cmd.extend(['--exclude-module', module])
    # Neither logs, live parameter snapshots nor stored sensor data enter the bundle.
    cmd.append(str(root / 'run.py'))
    subprocess.run(cmd, cwd=root, check=True)
    executable = root / 'dist' / name / (name + '.exe') if args.onedir else root / 'dist' / (name + '.exe')
    if not executable.is_file(): raise RuntimeError('打包工具未生成预期 EXE')
    print('EXE:', executable, flush=True)
    if args.smoke_test:
        report = executable.parent / 'smoke_test.json'
        if report.exists(): report.unlink()
        subprocess.run([str(executable), '--smoke-test'], cwd=executable.parent, timeout=120, check=True)
        if not report.exists(): raise RuntimeError('EXE 启动自检没有产生报告')
        print('SMOKE:', report.read_text(encoding='utf-8'), flush=True)
    cleanup_build(root)
    return 0


if __name__ == '__main__':
    raise SystemExit(build())
