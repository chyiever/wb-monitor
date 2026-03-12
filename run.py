#!/usr/bin/env python
"""
PCCP Wire Break Monitoring Software
Quick launch script

Usage:
    python run.py              # Normal mode
    python run.py --debug      # Enable debug logging
    python run.py --log FILE   # Save log to file
    python run.py --config FILE # Use custom config file
"""

import sys
import argparse
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

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

    return parser.parse_args()


if __name__ == '__main__':
    args = parse_arguments()

    # Configure logging level
    if args.debug:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    # Pass arguments to main function
    sys.exit(main(args))