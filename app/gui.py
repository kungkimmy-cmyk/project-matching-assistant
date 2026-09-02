"""
gui.py
------
PySide6 desktop front-end for the 2025 Project Matching Assistant.

Thread-safety pattern unchanged from the original build (log records
relayed through a Qt Signal, never a direct widget callback -- see
the RFQ Extractor's CHANGELOG for the exact crash this prevents).

Supports MULTIPLE factory folders and MULTIPLE customer folders (Add/
Remove/Clear lists, same pattern as the RFQ Extractor), and a "Select
Sales Report..." file picker so the Mimosa Sales Invoice Report can be
chosen locally without ever being uploaded anywhere.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import traceback
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QThread, QObject, Signal
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QProgressBar, QPlainTextEdit, QFileDialog, QMessageBox, QStatusBar,
    QListWidget, QAbstractItemView,
)

from .config import MatcherConfig, load_config, save_config
from .orchestrator import run_matching, MatchingRunResult
from .excel_writer import write_matching_results
from . import crash_logger

logger = logging.getLogger(__name__)


class _LogSignalRelay(QObject):
    message = Signal(str)


class _QtLogHandler(logging.Handler):
    def __init__(self, relay: _LogSignalRelay):
        super().__init__()
        self.relay = relay

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.relay.message.emit(self.format(record))
        except Exception:  # noqa: BLE001
            pass


def _safe_slot(func):
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            crash_logger.write_crash_report(f"Exception in GUI slot '{func.__name__}'", exc)
            logger.exception("Exception in GUI slot '%s'", func.__name__)
            try:
                QMessageBox.critical(self, "Unexpected error", f"Something went wrong in '{func.__name__}':\n\n{exc}\n\nDetails written to Matcher_Crash_Log.txt.")
            except Exception:  # noqa: BLE001
                pass
            return None
    wrapper.__name__ = func.__name__
    return wrapper


def _open_in_default_app(path: Path) -> None:
    if sys.platform.startswith("win"):
        import os
        os.startfile(str(path))  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


class MatchingWorker(QThread):
    file_progress = Signal(int, int, str, float)
    finished_ok = Signal(object)
    failed = Signal(str, str)

    def __init__(self, factory_folders: List[str], customer_folders: List[str], cfg: MatcherConfig, photo_store_dir: Path):
        super().__init__()
        self.factory_folders = factory_folders
        self.customer_folders = customer_folders
        self.cfg = cfg
        self.photo_store_dir = photo_store_dir

    def run(self) -> None:
        crash_logger.checkpoint(f"MatchingWorker starting: factory={self.factory_folders} customer={self.customer_folders}")
        try:
            result = run_matching(
                self.factory_folders, self.customer_folders, self.cfg,
                photo_store_dir=self.photo_store_dir, on_file_progress=self.file_progress.emit,
            )
            crash_logger.checkpoint(f"MatchingWorker finished: {len(result.matching.confirmed)} confirmed matches")
            self.finished_ok.emit(result)
        except Exception as exc:  # noqa: BLE001
            full_tb = traceback.format_exc()
            crash_logger.write_crash_report("Exception inside MatchingWorker.run()", exc)
            logger.error("Matching failed:\n%s", full_tb)
            self.failed.emit(str(exc), full_tb)


class MainWindow(QMainWindow):
    def __init__(self, config_path: str = "matcher_config.json"):
        super().__init__()
        self.setWindowTitle("2025 Project Matching Assistant  (TEST BUILD -- first local validation)")
        self.resize(980, 760)

        self.config_path = config_path
        self.cfg: MatcherConfig = load_config(config_path)
        self.log_file_path = Path(config_path).resolve().parent / "Matching_Log.txt"
        self.photo_store_dir = Path(config_path).resolve().parent / "photo_store"
        self.factory_folders: List[str] = []
        self.customer_folders: List[str] = []
        self.result: Optional[MatchingRunResult] = None
        self.worker: Optional[MatchingWorker] = None
        self.last_saved_path: Optional[Path] = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # --- Factory folders (multiple) ---
        layout.addWidget(QLabel("Factory quotation folder(s):"))
        factory_row = QHBoxLayout()
        self.factory_list = QListWidget()
        self.factory_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.factory_list.setMaximumHeight(90)
        factory_row.addWidget(self.factory_list, stretch=1)
        factory_btn_col = QVBoxLayout()
        self.btn_add_factory = QPushButton("Add Factory Folder...")
        self.btn_add_factory.clicked.connect(self.on_add_factory_folder)
        self.btn_remove_factory = QPushButton("Remove Selected")
        self.btn_remove_factory.clicked.connect(self.on_remove_factory_folder)
        self.btn_clear_factory = QPushButton("Clear All")
        self.btn_clear_factory.clicked.connect(self.on_clear_factory_folders)
        factory_btn_col.addWidget(self.btn_add_factory)
        factory_btn_col.addWidget(self.btn_remove_factory)
        factory_btn_col.addWidget(self.btn_clear_factory)
        factory_btn_col.addStretch(1)
        factory_row.addLayout(factory_btn_col)
        layout.addLayout(factory_row)

        # --- Customer folders (multiple) ---
        layout.addWidget(QLabel("Customer quotation folder(s):"))
        customer_row = QHBoxLayout()
        self.customer_list = QListWidget()
        self.customer_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.customer_list.setMaximumHeight(90)
        customer_row.addWidget(self.customer_list, stretch=1)
        customer_btn_col = QVBoxLayout()
        self.btn_add_customer = QPushButton("Add Customer Folder...")
        self.btn_add_customer.clicked.connect(self.on_add_customer_folder)
        self.btn_remove_customer = QPushButton("Remove Selected")
        self.btn_remove_customer.clicked.connect(self.on_remove_customer_folder)
        self.btn_clear_customer = QPushButton("Clear All")
        self.btn_clear_customer.clicked.connect(self.on_clear_customer_folders)
        customer_btn_col.addWidget(self.btn_add_customer)
        customer_btn_col.addWidget(self.btn_remove_customer)
        customer_btn_col.addWidget(self.btn_clear_customer)
        customer_btn_col.addStretch(1)
        customer_row.addLayout(customer_btn_col)
        layout.addLayout(customer_row)

        # --- Sales report (optional, local file picker -- never uploaded anywhere) ---
        sales_row = QHBoxLayout()
        self.btn_select_sales_report = QPushButton("Select Sales Report (SOLD history)...")
        self.btn_select_sales_report.clicked.connect(self.on_select_sales_report)
        self.lbl_sales_report = QLabel(self.cfg.sales_report_path or "Not selected (optional -- QUOTED evidence will still be used)")
        self.lbl_sales_report.setStyleSheet("color: #000;" if self.cfg.sales_report_path else "color: #666;")
        self.btn_clear_sales_report = QPushButton("Clear")
        self.btn_clear_sales_report.clicked.connect(self.on_clear_sales_report)
        sales_row.addWidget(self.btn_select_sales_report)
        sales_row.addWidget(self.lbl_sales_report, stretch=1)
        sales_row.addWidget(self.btn_clear_sales_report)
        layout.addLayout(sales_row)

        # --- Action buttons ---
        action_row = QHBoxLayout()
        self.btn_analyse = QPushButton("Analyse")
        self.btn_analyse.setEnabled(False)
        self.btn_analyse.clicked.connect(self.on_analyse)
        self.btn_open_results = QPushButton("Open Results")
        self.btn_open_results.setEnabled(False)
        self.btn_open_results.clicked.connect(self.on_open_results)
        action_row.addWidget(self.btn_analyse)
        action_row.addWidget(self.btn_open_results)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self.progress_detail_label = QLabel("")
        self.progress_detail_label.setVisible(False)
        layout.addWidget(self.progress_detail_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        layout.addWidget(QLabel("Status Log"))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet("font-family: Consolas, monospace; font-size: 11px;")
        layout.addWidget(self.log_view, stretch=1)

        self.setStatusBar(QStatusBar())

        self._log_relay = _LogSignalRelay()
        self._log_relay.message.connect(self._append_log)
        handler = _QtLogHandler(self._log_relay)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
        logging.getLogger().addHandler(handler)
        try:
            fh = logging.FileHandler(self.log_file_path, mode="w", encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
            logging.getLogger().addHandler(fh)
        except OSError as exc:  # noqa: BLE001
            self._append_log(f"WARNING: could not open {self.log_file_path} ({exc})")
        logging.getLogger().setLevel(logging.INFO)
        crash_logger.checkpoint("MainWindow initialized")
        self._append_log("TEST BUILD -- first local validation. Not the final production version.")

    def _append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text)

    def _refresh_analyse_button(self) -> None:
        self.btn_analyse.setEnabled(bool(self.factory_folders and self.customer_folders))

    @_safe_slot
    def on_add_factory_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add a Factory Quotations Folder")
        if not folder:
            return
        if folder in self.factory_folders:
            self._append_log(f"Already added: {folder}")
            return
        self.factory_folders.append(folder)
        self.factory_list.addItem(folder)
        self._append_log(f"Added factory folder: {folder}")
        self._refresh_analyse_button()

    @_safe_slot
    def on_remove_factory_folder(self) -> None:
        for item in self.factory_list.selectedItems():
            folder = item.text()
            self.factory_folders.remove(folder)
            self.factory_list.takeItem(self.factory_list.row(item))
            self._append_log(f"Removed factory folder: {folder}")
        self._refresh_analyse_button()

    @_safe_slot
    def on_clear_factory_folders(self) -> None:
        self.factory_list.clear()
        self.factory_folders.clear()
        self._append_log("Cleared all factory folders.")
        self._refresh_analyse_button()

    @_safe_slot
    def on_add_customer_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add a Customer Quotations Folder")
        if not folder:
            return
        if folder in self.customer_folders:
            self._append_log(f"Already added: {folder}")
            return
        self.customer_folders.append(folder)
        self.customer_list.addItem(folder)
        self._append_log(f"Added customer folder: {folder}")
        self._refresh_analyse_button()

    @_safe_slot
    def on_remove_customer_folder(self) -> None:
        for item in self.customer_list.selectedItems():
            folder = item.text()
            self.customer_folders.remove(folder)
            self.customer_list.takeItem(self.customer_list.row(item))
            self._append_log(f"Removed customer folder: {folder}")
        self._refresh_analyse_button()

    @_safe_slot
    def on_clear_customer_folders(self) -> None:
        self.customer_list.clear()
        self.customer_folders.clear()
        self._append_log("Cleared all customer folders.")
        self._refresh_analyse_button()

    @_safe_slot
    def on_select_sales_report(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select the Mimosa Sales Invoice Report (stays local -- never uploaded)",
            "", "Excel Workbook (*.xlsx *.xlsm)",
        )
        if not path:
            return
        self.cfg.sales_report_path = path
        self.lbl_sales_report.setText(path)
        self.lbl_sales_report.setStyleSheet("color: #000;")
        save_config(self.cfg, self.config_path)
        self._append_log(f"Sales report selected: {path}")

    @_safe_slot
    def on_clear_sales_report(self) -> None:
        self.cfg.sales_report_path = ""
        self.lbl_sales_report.setText("Not selected (optional -- QUOTED evidence will still be used)")
        self.lbl_sales_report.setStyleSheet("color: #666;")
        save_config(self.cfg, self.config_path)
        self._append_log("Sales report cleared.")

    @_safe_slot
    def on_analyse(self) -> None:
        if not (self.factory_folders and self.customer_folders):
            return
        self.btn_analyse.setEnabled(False)
        self.btn_add_factory.setEnabled(False)
        self.btn_remove_factory.setEnabled(False)
        self.btn_clear_factory.setEnabled(False)
        self.btn_add_customer.setEnabled(False)
        self.btn_remove_customer.setEnabled(False)
        self.btn_clear_customer.setEnabled(False)
        self.btn_select_sales_report.setEnabled(False)
        self.btn_open_results.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.progress_detail_label.setVisible(True)
        self.progress_detail_label.setText("Scanning folders...")
        self.summary_label.setText("Analysing...")
        self._append_log("=" * 60)
        self._append_log(f"Starting analysis: {len(self.factory_folders)} factory folder(s), {len(self.customer_folders)} customer folder(s)")
        for f in self.factory_folders:
            self._append_log(f"  factory:  {f}")
        for c in self.customer_folders:
            self._append_log(f"  customer: {c}")
        if self.cfg.sales_report_path:
            self._append_log(f"  sales report: {self.cfg.sales_report_path}")
        crash_logger.checkpoint("User clicked Analyse")

        self.worker = MatchingWorker(list(self.factory_folders), list(self.customer_folders), self.cfg, self.photo_store_dir)
        self.worker.file_progress.connect(self.on_file_progress)
        self.worker.finished_ok.connect(self.on_finished)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()

    @_safe_slot
    def on_file_progress(self, files_done: int, total: int, current_filename: str, elapsed_seconds: float) -> None:
        if total <= 0:
            return
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(files_done)
        eta_text = ""
        if files_done > 0:
            remaining = max(0, total - files_done)
            eta_seconds = int((elapsed_seconds / files_done) * remaining)
            minutes, seconds = divmod(eta_seconds, 60)
            eta_text = f"  |  ETA: {minutes}m {seconds:02d}s" if remaining else "  |  Finishing up..."
        current_text = f"Reading: {current_filename}" if current_filename else "Matching projects..."
        self.progress_detail_label.setText(f"{files_done} / {total} files  |  {current_text}{eta_text}")

    def _reenable_folder_controls(self) -> None:
        self.btn_add_factory.setEnabled(True)
        self.btn_remove_factory.setEnabled(True)
        self.btn_clear_factory.setEnabled(True)
        self.btn_add_customer.setEnabled(True)
        self.btn_remove_customer.setEnabled(True)
        self.btn_clear_customer.setEnabled(True)
        self.btn_select_sales_report.setEnabled(True)
        self.btn_analyse.setEnabled(True)

    @_safe_slot
    def on_finished(self, result: MatchingRunResult) -> None:
        self.result = result
        self.progress_bar.setVisible(False)
        self.progress_detail_label.setVisible(False)
        self._reenable_folder_controls()

        m = result.matching
        total_products = sum(len(pa.comparison_rows) for pa in result.project_analyses)
        auto_selected = sum(1 for pa in result.project_analyses for p in pa.comparison_rows if p.auto_selected)
        self.summary_label.setText(
            f"Confirmed: {len(m.confirmed)}  |  Needs Review: {len(m.needs_review)}  |  "
            f"Unmatched factory: {len(m.unmatched_factory)}  |  Unmatched customer: {len(m.unmatched_customer)}  |  "
            f"Products: {total_products} ({auto_selected} auto-selected)  |  "
            f"SOLD evidence: {result.stats.sales_records_matched_to_factory_code}/{result.stats.sales_records_loaded}  |  "
            f"DB code overlaps flagged: {sum(1 for x in result.db_code_mapping if x.alternative_db_codes)}"
        )
        self._append_log("Analysis complete.")

        default_name = "Matching_Results.xlsx"
        path, _ = QFileDialog.getSaveFileName(self, "Save Matching Results", default_name, "Excel Workbook (*.xlsx)")
        if path:
            out_path = write_matching_results(result, path)
            self.last_saved_path = out_path
            self.btn_open_results.setEnabled(True)
            self._append_log(f"Saved: {out_path}")
            QMessageBox.information(self, "Analysis complete", f"Results saved to:\n{out_path}\n\nUse 'Open Results' to view now.")

    @_safe_slot
    def on_failed(self, message: str, full_traceback: str) -> None:
        self.progress_bar.setVisible(False)
        self.progress_detail_label.setVisible(False)
        self._reenable_folder_controls()
        self._append_log(f"FATAL ERROR: {message}")
        self._append_log(full_traceback)
        QMessageBox.critical(self, "Analysis failed", f"{message}\n\nDetails written to Matching_Log.txt and Matcher_Crash_Log.txt.")

    @_safe_slot
    def on_open_results(self) -> None:
        if self.last_saved_path and self.last_saved_path.exists():
            _open_in_default_app(self.last_saved_path)


def launch_gui(config_path: str = "matcher_config.json") -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(config_path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(launch_gui())
