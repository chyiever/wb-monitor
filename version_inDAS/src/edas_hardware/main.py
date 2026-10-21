"""
`src/main.py` 是应用主入口，负责把命令行、日志系统、Qt 应用生命周期和主窗口创建串起来。

当前版本的启动链路比较克制：`run.py` 只做 `src` 路径注入，然后直接调用本文件中的 `main()`。本文件不承载采集逻辑、显示逻辑或业务计算，而是专门处理四类启动期职责：解析参数、初始化日志、设置高 DPI 以及安装异常钩子并创建 `MainWindow`。

对后续类似项目而言，这种拆分方式值得保留。启动文件越薄，越容易在设备不可用、依赖缺失、日志路径异常或 GUI 初始化失败时快速定位问题。
"""
import sys
import argparse
import traceback
import logging
import ctypes
from pathlib import Path
from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon

from .logger import setup_logging, get_logger, default_log_directory


def get_bundle_root() -> Path:
    """Return the runtime bundle root for source and frozen builds."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


def get_app_icon_path() -> Path:
    """Return the preferred icon path with fallback to the legacy asset."""
    bundle_root = get_bundle_root()
    primary = bundle_root / "resources" / "eDAS-LOGO.png"
    if primary.exists():
        return primary
    return bundle_root / "resources" / "logo.png"


def set_windows_app_user_model_id() -> None:
    """Set an explicit AppUserModelID so Windows taskbar uses the app icon."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "CAS.eDAS.PCIe7821"
        )
    except Exception:
        pass


# ----- DISPLAY CONFIGURATION UTILITIES -----
# High-DPI display support for modern monitors and variable scaling

def setup_high_dpi():
    """
    Configure high-DPI display support for modern monitors.

    Modern displays often have high pixel densities that require special
    handling to render GUI elements at appropriate sizes. This function
    enables Qt's built-in high-DPI scaling mechanisms.

    Features Enabled:
    - AA_EnableHighDpiScaling: Automatic DPI-aware scaling of GUI elements
    - AA_UseHighDpiPixmaps: High-resolution pixmap rendering for crisp graphics

    Platform Compatibility:
    - Windows: Supports DPI awareness levels (System DPI Aware, Per-Monitor DPI Aware)
    - macOS: Retina display support with automatic scaling
    - Linux: Fractional scaling support for high-DPI displays

    Side Effects:
    - Must be called before QApplication creation
    - Affects all subsequent GUI element scaling
    - May change coordinate system for manual pixel positioning

    Note: These attributes were introduced in Qt 5.6+. The hasattr() checks
          ensure compatibility with older Qt versions.
    """
    # Enable automatic high-DPI scaling (Qt 5.6+)
    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)

    # Enable high-resolution pixmaps for crisp graphics (Qt 5.7+)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)


# ----- GLOBAL EXCEPTION HANDLING -----
# Centralized error reporting and logging for unhandled exceptions

def exception_hook(exc_type, exc_value, exc_tb):
    """
    Global exception handler for capturing and reporting unhandled exceptions.

    This function replaces the default Python exception handler to provide:
    - Comprehensive logging of all unhandled exceptions
    - User-friendly error dialogs in GUI mode
    - Full stack trace preservation for debugging
    - Graceful error reporting without application termination

    Args:
        exc_type: Exception class (e.g., ValueError, RuntimeError)
        exc_value: Exception instance with error details
        exc_tb: Traceback object containing stack frame information

    Behavior:
    - Logs complete exception information at CRITICAL level
    - Shows error dialog to user if QApplication exists (GUI mode)
    - Preserves original exception information for debugging
    - Does not suppress exceptions - they still propagate normally

    Integration:
    This function is installed as sys.excepthook to capture all unhandled
    exceptions that would otherwise terminate the application.

    Error Dialog Strategy:
    - Only show GUI dialog if QApplication is active (prevents crashes)
    - Include enough detail for user reporting without overwhelming
    - Allow application to continue running after dialog dismissal

    Security Note:
    Exception messages may contain sensitive information (file paths, etc.)
    Consider sanitizing for production deployments.
    """
    # Get logger for centralized error reporting
    log = get_logger("main")

    # Format complete exception information with stack trace
    error_msg = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))

    # Log exception at highest severity level for visibility
    log.critical(f"Unhandled exception:\n{error_msg}")

    # Show user-friendly error dialog if GUI is available
    if QApplication.instance():
        # Create modal error dialog with exception details
        QMessageBox.critical(None, "Error", f"An error occurred:\n\n{error_msg}")


# ----- MAIN APPLICATION ENTRY POINT -----
# Application initialization, configuration, and lifecycle management

def main():
    """
    Main application entry point and initialization sequence.

    This function orchestrates the complete application startup process:
    1. Command-line argument parsing and validation
    2. Logging system initialization with appropriate configuration
    3. Display system setup for high-DPI compatibility
    4. Qt application creation and configuration
    5. Main window initialization and display
    6. Event loop execution and shutdown handling

    Command-Line Arguments:
    --simulate, -s: Enable simulation mode for testing without hardware
    --debug, -d: Enable DEBUG level logging for detailed troubleshooting
    --log FILE, -l FILE: Specify custom log file location
        - If FILE is empty string, use the default D:/eDAS-log directory
        - If not specified, also use the default D:/eDAS-log directory

    Error Handling:
    - Comprehensive exception handling with user feedback
    - Proper exit codes for automation (0=success, 1=error)
    - Graceful degradation when hardware unavailable

    Performance Considerations:
    - Deferred import of heavy modules (main_window) until after QApplication
    - Minimal startup time through optimized initialization sequence
    - Memory-efficient logging configuration

    Platform Support:
    - Windows: Full hardware and simulation mode support
    - macOS/Linux: Simulation mode supported, hardware support TBD

    Exit Codes:
    - 0: Normal application termination
    - 1: Startup failure or critical error
    - N: QApplication exit code (user-initiated shutdown)
    """
    # ----- COMMAND-LINE ARGUMENT PARSING -----
    # Parse and validate user-specified options and modes

    parser = argparse.ArgumentParser(
        description='PCIe-7821 DAS Acquisition Software',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                    # Normal operation with hardware
  python main.py --simulate         # Testing without hardware
  python main.py --debug --log ""   # Debug mode with default auto-rotated logs
  python main.py -s -d -l debug.log # Simulation + debug + custom log
        """
    )

    # Simulation mode: Enable operation without physical hardware
    parser.add_argument('--simulate', '-s', action='store_true',
                        help='Run in simulation mode without hardware')

    # Debug mode: Enable verbose logging for troubleshooting
    parser.add_argument('--debug', '-d', action='store_true',
                        help='Enable debug logging')

    # Log file: Flexible logging output configuration
    parser.add_argument('--log', '-l', type=str, default=None,
                        help='Save log to file (default: auto-create files under D:/eDAS-log)')

    args = parser.parse_args()

    # ----- LOGGING SYSTEM INITIALIZATION -----
    # Configure comprehensive logging with appropriate levels and outputs

    # Determine logging level based on debug flag
    log_level = logging.DEBUG if args.debug else logging.INFO

    # Handle log file configuration with smart defaults
    log_file = args.log
    auto_file = log_file in (None, "")
    if log_file == '':
        log_file = None

    # Initialize logging system with determined configuration
    setup_logging(level=log_level, log_file=log_file, console=True, auto_file=auto_file)
    log = get_logger("main")

    # ----- APPLICATION STARTUP BANNER -----
    # Log comprehensive startup information for debugging and audit trails

    log.info("=" * 60)
    log.info("PCIe-7821 DAS Acquisition Software Starting")
    log.info(f"Simulation mode: {args.simulate}")
    log.info(f"Debug mode: {args.debug}")
    log.info(f"Log target: {log_file or default_log_directory()}")
    log.info("=" * 60)

    # ----- GLOBAL ERROR HANDLING SETUP -----
    # Install custom exception handler for comprehensive error reporting
    sys.excepthook = exception_hook

    # ----- DISPLAY SYSTEM CONFIGURATION -----
    # Configure high-DPI support before QApplication creation
    setup_high_dpi()
    set_windows_app_user_model_id()

    # ----- QT APPLICATION INITIALIZATION -----
    # Create Qt application with proper metadata and styling

    app = QApplication(sys.argv)

    # Set application metadata for system integration
    app.setApplicationName("eDAS-pt1g-gh-26.6.6")  # Used by system for window grouping
    app.setApplicationVersion("26.6.6")             # Version for about dialogs, etc.
    app.setDesktopFileName("eDAS")

    icon_path = get_app_icon_path()
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
        log.info(f"Application icon loaded: {icon_path}")
    else:
        log.warning(f"Application icon not found: {icon_path}")

    # Apply modern visual style across all platforms
    app.setStyle('Fusion')  # Consistent modern appearance

    log.info("QApplication created")

    # ----- MAIN WINDOW CREATION AND DISPLAY -----
    # Import and initialize main window (deferred to reduce startup time)

    try:
        log.info("Creating main window...")

        # Import main window module (after QApplication exists)
        from main_window import MainWindow

        # Create main window with appropriate operation mode
        window = MainWindow(simulation_mode=args.simulate)

        # Indicate simulation mode in window title for user awareness
        if args.simulate:
            window.setWindowTitle("eDAS-pt1g-gh-26.6.6 [SIMULATION MODE]")

        # Display main window to user
        window.show()
        log.info("Main window shown")

        # ----- EVENT LOOP EXECUTION -----
        # Enter Qt event loop for GUI operation and user interaction

        log.info("Entering event loop...")
        exit_code = app.exec_()  # Block here until application termination
        log.info(f"Event loop exited with code {exit_code}")

        # Terminate with Qt application's exit code
        sys.exit(exit_code)

    except Exception as e:
        # ----- STARTUP ERROR HANDLING -----
        # Handle critical failures during application initialization

        log.exception(f"Failed to start application: {e}")

        # Show error dialog if possible (QApplication exists)
        QMessageBox.critical(None, "Startup Error", f"Failed to start application:\n\n{e}")

        # Exit with error code for automation scripts
        sys.exit(1)


# ----- MODULE EXECUTION GUARD -----
# Ensure main() only runs when script is executed directly (not imported)

if __name__ == '__main__':
    main()
