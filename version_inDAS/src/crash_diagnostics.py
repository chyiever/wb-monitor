"""Durable Python/native crash evidence, installed before loading Qt or the DLL."""
import atexit
import faulthandler
import logging
import os
import platform
import struct
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

_instance = None


class CrashDiagnostics:
    def __init__(self, root):
        folder = Path(root) / 'logs' / 'crash_reports'
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / ('crash_' + datetime.now().strftime('%Y%m%d_%H%M%S') + f'_{os.getpid()}.log')
        self.stream = self.path.open('a', encoding='utf-8', errors='backslashreplace', buffering=1)
        self.exception_callback = None
        self._notifying = False
        self._qt_handler = None
        self._qt_message_counts = {}
        self.write(f'SESSION_START pid={os.getpid()} bits={struct.calcsize("P")*8} python={sys.version} platform={platform.platform()} frozen={getattr(sys,"frozen",False)} executable={sys.executable}')
        faulthandler.enable(file=self.stream, all_threads=True)
        sys.excepthook = self._python_exception
        threading.excepthook = self._thread_exception
        if sys.stderr is None:
            sys.stderr = self.stream
        atexit.register(lambda: self.write('PYTHON_ATEXIT'))

    def write(self, message):
        try:
            self.stream.write(datetime.now().isoformat(timespec='milliseconds') + ' ' + str(message) + '\n')
            self.stream.flush()
        except (OSError, ValueError):
            pass

    def arm_gui_watchdog(self, timeout=8.):
        """Qt heartbeat re-arms a native timer, which can dump even if Python stalls."""
        faulthandler.dump_traceback_later(timeout, repeat=True, file=self.stream)

    def cancel_gui_watchdog(self):
        faulthandler.cancel_dump_traceback_later()

    def _record_exception(self, kind, exc_type, value, tb):
        self.write(kind)
        try:
            traceback.print_exception(exc_type, value, tb, file=self.stream)
            self.stream.flush()
        except OSError:
            pass
        logging.getLogger('crash').critical('%s; diagnostic=%s', kind, self.path, exc_info=(exc_type,value,tb))
        if self.exception_callback is not None and not self._notifying:
            self._notifying = True
            try:
                self.exception_callback(f'{exc_type.__name__}: {value}')
            except Exception:
                traceback.print_exc(file=self.stream)

    def _python_exception(self, exc_type, value, tb):
        self._record_exception('UNHANDLED_PYTHON_EXCEPTION', exc_type, value, tb)

    def _thread_exception(self, args):
        self._record_exception(f'UNHANDLED_THREAD_EXCEPTION thread={args.thread.name}', args.exc_type, args.exc_value, args.exc_traceback)

    def attach_qt(self):
        from PyQt5.QtCore import qInstallMessageHandler
        if self._qt_handler is not None:
            return
        def handler(kind, context, message):
            if int(kind) in (1,2,3):
                key = (int(kind), message)
                if key not in self._qt_message_counts and len(self._qt_message_counts) >= 128:
                    self._qt_message_counts.pop(next(iter(self._qt_message_counts)))
                count = self._qt_message_counts.get(key, 0) + 1
                self._qt_message_counts[key] = count
                if int(kind) != 3 and count > 1 and count % 100:
                    return
                self.write(f'QT_MESSAGE type={int(kind)} file={context.file} line={context.line} {message}')
                logging.getLogger('qt').warning('%s', message)
        self._qt_handler = handler
        qInstallMessageHandler(handler)

    def resources(self):
        try:
            import psutil
            process = psutil.Process()
            memory = process.memory_info()
            system = psutil.virtual_memory()
            message = (f'PROCESS_MEMORY pid={os.getpid()} rss_mb={memory.rss/1048576:.1f} '
                       f'private_mb={getattr(memory,"private",memory.vms)/1048576:.1f} '
                       f'threads={process.num_threads()} system_available_mb={system.available/1048576:.1f} '
                       f'system_total_mb={system.total/1048576:.1f}')
            self.write(message)
            logging.getLogger('crash').info(message)
        except Exception as exc:
            self.write(f'RESOURCE_SNAPSHOT_FAILED {exc}')


def install(root):
    global _instance
    if _instance is None:
        _instance = CrashDiagnostics(root)
    return _instance
