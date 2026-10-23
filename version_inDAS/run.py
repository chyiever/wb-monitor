#!/usr/bin/env python
"""
PCCP Wire Break Monitoring Software
Quick launch script

Usage:
    python run.py              # Hardware mode
    python run.py --simulate   # Simulated eDAS without hardware
    python run.py --debug      # Enable debug logging
    python run.py --log FILE   # Save log to file
    python run.py --config FILE # Use custom config file
"""

import sys
import os
import argparse
from pathlib import Path

_root = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
os.chdir(_root)
if getattr(sys, 'frozen', False):
    import shutil
    target = _root / 'config' / 'app_config.json'
    seed = Path(sys._MEIPASS) / 'config' / 'app_config.json'
    if not target.exists() and seed.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(seed, target)

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from crash_diagnostics import install as install_crash_diagnostics
try:
    install_crash_diagnostics(_root)
except OSError as exc:
    print(f'Crash diagnostics unavailable: {exc}', file=sys.stderr)

from src.main import main


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="PCCP Wire Break Monitoring Software",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging'
    )

    parser.add_argument(
        '--log',
        type=str,
        metavar='FILE',
        help='Save log to specified file'
    )

    parser.add_argument(
        '--config',
        type=str,
        metavar='FILE',
        help='Use custom configuration file'
    )

    parser.add_argument("--simulate", action="store_true", help="Simulate PCIe eDAS acquisition without hardware")
    return parser.parse_args()


if __name__ == '__main__':
    if '--smoke-test' in sys.argv:
        import json
        from types import SimpleNamespace
        from src.main import PCCPMonitorApp
        controller = PCCPMonitorApp(SimpleNamespace(simulate=True, debug=False, log=None))
        window = controller.main_window
        window.show()
        controller.app.processEvents()
        report = {'tabs': [window.tab_widget.tabText(i) for i in range(window.tab_widget.count())],
                  'frozen': bool(getattr(sys, 'frozen', False)),
                  'processors': all(value is not None for value in (controller.edas_manager, controller.tab1_manager, controller.alignment_coordinator))}
        (_root / 'smoke_test.json').write_text(json.dumps(report), encoding='utf-8')
        window.close()
        controller.cleanup()
        sys.exit(0)
    args = parse_arguments()

    # Pass arguments to main function. src.main owns logging configuration.
    sys.exit(main(args))
