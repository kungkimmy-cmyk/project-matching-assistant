"""
crash_logger.py
----------------
Low-level, dependency-free diagnostic logging for the Matching
Assistant. Writes to Matcher_Crash_Log.txt next to the running .exe
(or script), using an open-write-flush-close pattern (never holding a
handle open) so a hard crash a moment later doesn't lose what was just
written. Intentionally duplicated (not imported) from the RFQ
Extractor project -- the two applications are meant to be fully
independent.
"""
from __future__ import annotations

import datetime
import os
import platform
import sys
import threading
import traceback
from pathlib import Path
from typing import Optional

CRASH_LOG_FILENAME = "Matcher_Crash_Log.txt"


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def crash_log_path() -> Path:
    return _app_dir() / CRASH_LOG_FILENAME


def _system_info_block() -> str:
    th = threading.current_thread()
    return "\n".join([
        f"Time:            {datetime.datetime.now().isoformat()}",
        f"Python:          {sys.version.split()[0]}",
        f"Platform:        {platform.platform()}",
        f"Frozen (.exe):   {getattr(sys, 'frozen', False)}",
        f"sys.executable:  {sys.executable}",
        f"cwd:             {os.getcwd()}",
        f"Thread:          {th.name} (id={th.ident}, is_main={th is threading.main_thread()})",
    ])


def write_crash_report(context: str, exc: Optional[BaseException] = None) -> None:
    try:
        block = ["=" * 70, f"CONTEXT: {context}", _system_info_block()]
        if exc is not None:
            block.append("TRACEBACK:")
            block.append("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        block.append("")
        path = crash_log_path()
        with open(path, "a", encoding="utf-8", buffering=1) as f:
            f.write("\n".join(block) + "\n")
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        pass


def checkpoint(message: str) -> None:
    try:
        path = crash_log_path()
        with open(path, "a", encoding="utf-8", buffering=1) as f:
            f.write(f"{datetime.datetime.now().isoformat()}  {message}\n")
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        pass


def install_global_excepthooks() -> None:
    def _sys_excepthook(exc_type, exc_value, exc_tb):
        write_crash_report("Uncaught exception (sys.excepthook, main thread)", exc_value)
        sys.__excepthook__(exc_type, exc_value, exc_tb)
    sys.excepthook = _sys_excepthook

    def _threading_excepthook(args) -> None:
        write_crash_report(
            f"Uncaught exception on thread '{args.thread.name if args.thread else '?'}' (threading.excepthook)",
            args.exc_value,
        )
        try:
            threading.__excepthook__(args)
        except Exception:  # noqa: BLE001
            pass
    threading.excepthook = _threading_excepthook


def redirect_stdio_if_missing() -> None:
    if not getattr(sys, "frozen", False):
        return
    try:
        target = _app_dir() / "stdio_redirect.log"
        stream = open(target, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream
    except Exception:  # noqa: BLE001
        pass
