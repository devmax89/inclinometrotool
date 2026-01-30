"""
DIGIL Reset Inclinometro - Main GUI Application v2.0
====================================================
Tool per il reset e la verifica dell'inclinometro sui dispositivi DIGIL.

v2.0.0:
- Nuova logica con verifica tramite commands-log API
- Log dettagliato consultabile durante l'esecuzione
- File separato per i reset completati
- Cleanup maintenance OFF alla chiusura
"""

import sys
import os
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QProgressBar,
    QSpinBox, QGroupBox, QFileDialog, QMessageBox, QTabWidget,
    QHeaderView, QAbstractItemView, QStatusBar, QFrame,
    QTextEdit, QSplitter
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QPixmap

from reset_worker import ResetWorker, ResetResult, ResetStatus, MaintenanceState, detect_device_type
from verify_worker import VerifyWorker, VerifyResult, VerifyStatus
from data_handler import InputLoader, Phase2InputLoader, ResultExporter
from api_client import get_token_manager


TERNA_STYLE = """
QMainWindow { background-color: #FFFFFF; }
QWidget { font-family: 'Segoe UI', Arial, sans-serif; font-size: 13px; }
QLabel#headerTitle { font-size: 22px; font-weight: bold; color: #0066CC; }
QLabel#headerSubtitle { font-size: 11px; color: #666666; }
QGroupBox { font-weight: bold; border: 1px solid #CCCCCC; border-radius: 6px; margin-top: 12px; padding-top: 10px; background-color: #FAFAFA; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #0066CC; }
QPushButton { background-color: #0066CC; color: white; border: none; padding: 8px 16px; border-radius: 4px; font-weight: bold; min-width: 100px; }
QPushButton:hover { background-color: #004C99; }
QPushButton:disabled { background-color: #CCCCCC; color: #666666; }
QPushButton#stopButton { background-color: #CC3300; }
QPushButton#stopButton:hover { background-color: #992600; }
QPushButton#exportButton { background-color: #009933; }
QPushButton#exportButton:hover { background-color: #006622; }
QPushButton#secondaryButton { background-color: #FFFFFF; color: #0066CC; border: 2px solid #0066CC; }
QPushButton#secondaryButton:hover { background-color: #E6F2FF; }
QTableWidget { border: 1px solid #CCCCCC; border-radius: 4px; gridline-color: #E0E0E0; background-color: white; alternate-background-color: #F8FBFF; }
QTableWidget::item { padding: 5px; }
QTableWidget::item:selected { background-color: #CCE5FF; color: black; }
QHeaderView::section { background-color: #0066CC; color: white; padding: 8px; border: none; font-weight: bold; }
QProgressBar { border: 1px solid #CCCCCC; border-radius: 4px; text-align: center; background-color: #F0F0F0; height: 25px; }
QProgressBar::chunk { background-color: #0066CC; border-radius: 3px; }
QSpinBox { border: 1px solid #CCCCCC; border-radius: 4px; padding: 5px; background-color: white; }
QTextEdit#logArea { border: 1px solid #CCCCCC; border-radius: 4px; background-color: #1E1E1E; color: #CCCCCC; font-family: 'Consolas', monospace; font-size: 11px; }
QTextEdit#resetLogArea { border: 1px solid #CCCCCC; border-radius: 4px; background-color: #0A2A0A; color: #00FF00; font-family: 'Consolas', monospace; font-size: 11px; }
QStatusBar { background-color: #F5F5F5; border-top: 1px solid #CCCCCC; }
QTabWidget::pane { border: 1px solid #CCCCCC; border-radius: 4px; background-color: white; }
QTabBar::tab { background-color: #F0F0F0; border: 1px solid #CCCCCC; padding: 10px 20px; margin-right: 2px; font-weight: bold; }
QTabBar::tab:selected { background-color: #0066CC; color: white; }
QTabBar::tab:hover:!selected { background-color: #E6F2FF; }
"""


class ResetThread(QThread):
    progress_signal = pyqtSignal(object, str)
    device_complete_signal = pyqtSignal(object)
    completed_signal = pyqtSignal(list)
    stats_signal = pyqtSignal(dict)
    log_signal = pyqtSignal(str, str)
    
    def __init__(self, device_ids: List[str]):
        super().__init__()
        self.device_ids = device_ids
        self.worker = ResetWorker()
        self.worker.set_log_callback(self._on_log)
    
    def _on_log(self, message: str, level: str = "INFO"):
        self.log_signal.emit(message, level)
    
    def run(self):
        def on_progress(result, message):
            self.progress_signal.emit(result, message)
            self.stats_signal.emit(self.worker.get_stats())
        
        def on_device_complete(result):
            self.device_complete_signal.emit(result)
            self.stats_signal.emit(self.worker.get_stats())
        
        def on_complete(results):
            self.completed_signal.emit(results)
        
        self.worker.run(self.device_ids, on_progress, on_complete, on_device_complete)
    
    def stop(self):
        self.worker.stop()
    
    def get_devices_with_maintenance_on(self) -> List[str]:
        return self.worker.get_devices_with_maintenance_on()
    
    def send_maintenance_off_to_pending(self, progress_callback=None) -> Dict[str, bool]:
        return self.worker.send_maintenance_off_to_pending(progress_callback)


class VerifyThread(QThread):
    progress_signal = pyqtSignal(object, str)
    device_complete_signal = pyqtSignal(object)
    completed_signal = pyqtSignal(list)
    stats_signal = pyqtSignal(dict)
    
    def __init__(self, devices_to_verify: List[Dict]):
        super().__init__()
        self.devices_to_verify = devices_to_verify
        self.worker = VerifyWorker()
    
    def run(self):
        def on_progress(result, message):
            self.progress_signal.emit(result, message)
            self.stats_signal.emit(self.worker.get_stats())
        
        def on_device_complete(result):
            self.device_complete_signal.emit(result)
            self.stats_signal.emit(self.worker.get_stats())
        
        def on_complete(results):
            self.completed_signal.emit(results)
        
        self.worker.run(self.devices_to_verify, on_progress, on_complete, on_device_complete)
    
    def stop(self):
        self.worker.stop()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.input_loader = InputLoader()
        self.phase2_loader = Phase2InputLoader()
        self.exporter = ResultExporter()
        self.reset_thread: Optional[ResetThread] = None
        self.verify_thread: Optional[VerifyThread] = None
        self.reset_results: List[ResetResult] = []
        self.verify_results: List[VerifyResult] = []
        self.reset_log_file: Optional[Path] = None
        
        self.init_ui()
        self.setStyleSheet(TERNA_STYLE)
    
    def init_ui(self):
        self.setWindowTitle("DIGIL Reset Inclinometro v2.0 - Terna IoT Team")
        self.setMinimumSize(1400, 900)
        
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(20, 20, 20, 20)
        
        # Header
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 10)
        
        self.logo_label = QLabel("T")
        self.logo_label.setFixedSize(120, 50)
        self.logo_label.setStyleSheet("background-color: #0066CC; border-radius: 8px; color: white; font-size: 24px; font-weight: bold;")
        self.logo_label.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(self.logo_label)
        
        title_widget = QWidget()
        title_layout = QVBoxLayout(title_widget)
        title_layout.setContentsMargins(15, 0, 0, 0)
        title = QLabel("DIGIL Reset Inclinometro")
        title.setObjectName("headerTitle")
        title_layout.addWidget(title)
        subtitle = QLabel("Reset e verifica inclinometri - Terna S.p.A.")
        subtitle.setObjectName("headerSubtitle")
        title_layout.addWidget(subtitle)
        header_layout.addWidget(title_widget)
        header_layout.addStretch()
        
        version = QLabel("v2.0.0")
        version.setStyleSheet("color: #999999;")
        header_layout.addWidget(version)
        main_layout.addWidget(header)
        
        # Tab Widget
        self.tab_widget = QTabWidget()
        self.tab_widget.addTab(self.create_phase1_tab(), "📥 Fase 1: Reset Inclinometro")
        self.tab_widget.addTab(self.create_phase2_tab(), "🔍 Fase 2: Verifica Reset")
        main_layout.addWidget(self.tab_widget, stretch=1)
        
        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_label = QLabel("Pronto - Carica un file per iniziare")
        self.status_bar.addWidget(self.status_label, stretch=1)
    
    def create_phase1_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)
        
        # Controlli
        controls = QGroupBox("Configurazione Reset")
        controls_layout = QVBoxLayout(controls)
        
        file_row = QHBoxLayout()
        file_row.addWidget(QLabel("📁 File Input:"))
        self.p1_file_label = QLabel("Nessun file caricato")
        self.p1_file_label.setStyleSheet("color: #666666; font-style: italic;")
        file_row.addWidget(self.p1_file_label, stretch=1)
        self.p1_load_btn = QPushButton("Carica File")
        self.p1_load_btn.setObjectName("secondaryButton")
        self.p1_load_btn.clicked.connect(self.load_input_file)
        file_row.addWidget(self.p1_load_btn)
        controls_layout.addLayout(file_row)
        
        options_row = QHBoxLayout()
        options_row.addWidget(QLabel("Thread paralleli:"))
        self.p1_threads_spin = QSpinBox()
        self.p1_threads_spin.setRange(1, 50)
        self.p1_threads_spin.setValue(25)
        options_row.addWidget(self.p1_threads_spin)
        options_row.addSpacing(30)
        options_row.addWidget(QLabel("Intervallo check (sec):"))
        self.p1_interval_spin = QSpinBox()
        self.p1_interval_spin.setRange(30, 300)
        self.p1_interval_spin.setValue(60)
        options_row.addWidget(self.p1_interval_spin)
        options_row.addStretch()
        
        self.p1_start_btn = QPushButton("▶ Avvia Reset")
        self.p1_start_btn.clicked.connect(self.start_phase1)
        self.p1_start_btn.setEnabled(False)
        options_row.addWidget(self.p1_start_btn)
        
        self.p1_stop_btn = QPushButton("■ Stop")
        self.p1_stop_btn.setObjectName("stopButton")
        self.p1_stop_btn.clicked.connect(self.stop_phase1)
        self.p1_stop_btn.setEnabled(False)
        options_row.addWidget(self.p1_stop_btn)
        controls_layout.addLayout(options_row)
        
        info = QLabel("ℹ️ Modalità con verifica commands-log")
        info.setStyleSheet("color: #0066CC; font-style: italic;")
        controls_layout.addWidget(info)
        layout.addWidget(controls)
        
        # Progress
        progress_widget = QWidget()
        progress_layout = QHBoxLayout(progress_widget)
        progress_layout.setContentsMargins(0, 5, 0, 5)
        self.p1_progress = QProgressBar()
        self.p1_progress.setFormat("%v / %m (%p%)")
        progress_layout.addWidget(self.p1_progress, stretch=3)
        self.p1_stats_label = QLabel("OK: 0 | KO: 0 | In corso: 0")
        self.p1_stats_label.setStyleSheet("color: #666666; margin-left: 20px;")
        progress_layout.addWidget(self.p1_stats_label)
        self.p1_export_btn = QPushButton("📥 Esporta Excel")
        self.p1_export_btn.setObjectName("exportButton")
        self.p1_export_btn.clicked.connect(self.export_phase1)
        self.p1_export_btn.setEnabled(False)
        progress_layout.addWidget(self.p1_export_btn)
        layout.addWidget(progress_widget)
        
        # Splitter
        splitter = QSplitter(Qt.Vertical)
        
        # Tabella
        table_group = QGroupBox("Risultati Reset")
        table_layout = QVBoxLayout(table_group)
        self.p1_table = QTableWidget()
        self.p1_table.setColumnCount(8)
        self.p1_table.setHorizontalHeaderLabels(["Stato", "DeviceID", "Tipo", "Maint ON", "Reset", "Maint OFF", "Maint State", "Timestamp"])
        self.p1_table.setAlternatingRowColors(True)
        self.p1_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.p1_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.p1_table.horizontalHeader()
        header.setStretchLastSection(True)
        self.p1_table.setColumnWidth(0, 70)
        self.p1_table.setColumnWidth(1, 120)
        self.p1_table.setColumnWidth(2, 60)
        self.p1_table.setColumnWidth(3, 80)
        self.p1_table.setColumnWidth(4, 80)
        self.p1_table.setColumnWidth(5, 80)
        self.p1_table.setColumnWidth(6, 80)
        table_layout.addWidget(self.p1_table)
        splitter.addWidget(table_group)
        
        # Log
        log_widget = QWidget()
        log_layout = QHBoxLayout(log_widget)
        log_layout.setSpacing(10)
        
        general_log_group = QGroupBox("Log Operazioni")
        general_log_layout = QVBoxLayout(general_log_group)
        self.p1_log = QTextEdit()
        self.p1_log.setObjectName("logArea")
        self.p1_log.setReadOnly(True)
        general_log_layout.addWidget(self.p1_log)
        log_layout.addWidget(general_log_group, stretch=1)
        
        reset_log_group = QGroupBox("Reset Completati")
        reset_log_layout = QVBoxLayout(reset_log_group)
        self.p1_reset_log = QTextEdit()
        self.p1_reset_log.setObjectName("resetLogArea")
        self.p1_reset_log.setReadOnly(True)
        reset_log_layout.addWidget(self.p1_reset_log)
        log_layout.addWidget(reset_log_group, stretch=1)
        
        splitter.addWidget(log_widget)
        splitter.setSizes([500, 200])
        layout.addWidget(splitter, stretch=1)
        
        return tab
    
    def create_phase2_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)
        
        controls = QGroupBox("Configurazione Verifica")
        controls_layout = QVBoxLayout(controls)
        
        file_row = QHBoxLayout()
        file_row.addWidget(QLabel("📁 File Input (output Fase 1):"))
        self.p2_file_label = QLabel("Nessun file caricato")
        self.p2_file_label.setStyleSheet("color: #666666; font-style: italic;")
        file_row.addWidget(self.p2_file_label, stretch=1)
        self.p2_load_btn = QPushButton("Carica File")
        self.p2_load_btn.setObjectName("secondaryButton")
        self.p2_load_btn.clicked.connect(self.load_phase2_file)
        file_row.addWidget(self.p2_load_btn)
        controls_layout.addLayout(file_row)
        
        info_row = QHBoxLayout()
        self.p2_info_label = QLabel("ℹ️ Carica il file Excel prodotto dalla Fase 1")
        self.p2_info_label.setStyleSheet("color: #666666;")
        info_row.addWidget(self.p2_info_label)
        info_row.addStretch()
        self.p2_count_label = QLabel("Dispositivi da verificare: 0")
        info_row.addWidget(self.p2_count_label)
        controls_layout.addLayout(info_row)
        
        actions_row = QHBoxLayout()
        actions_row.addStretch()
        self.p2_start_btn = QPushButton("🔍 Avvia Verifica")
        self.p2_start_btn.clicked.connect(self.start_phase2)
        self.p2_start_btn.setEnabled(False)
        actions_row.addWidget(self.p2_start_btn)
        self.p2_stop_btn = QPushButton("■ Stop")
        self.p2_stop_btn.setObjectName("stopButton")
        self.p2_stop_btn.clicked.connect(self.stop_phase2)
        self.p2_stop_btn.setEnabled(False)
        actions_row.addWidget(self.p2_stop_btn)
        controls_layout.addLayout(actions_row)
        layout.addWidget(controls)
        
        progress_widget = QWidget()
        progress_layout = QHBoxLayout(progress_widget)
        progress_layout.setContentsMargins(0, 5, 0, 5)
        self.p2_progress = QProgressBar()
        self.p2_progress.setFormat("%v / %m (%p%)")
        progress_layout.addWidget(self.p2_progress, stretch=3)
        self.p2_stats_label = QLabel("Verificati: 0 | Problemi: 0")
        self.p2_stats_label.setStyleSheet("color: #666666; margin-left: 20px;")
        progress_layout.addWidget(self.p2_stats_label)
        self.p2_export_btn = QPushButton("📥 Esporta Excel")
        self.p2_export_btn.setObjectName("exportButton")
        self.p2_export_btn.clicked.connect(self.export_phase2)
        self.p2_export_btn.setEnabled(False)
        progress_layout.addWidget(self.p2_export_btn)
        layout.addWidget(progress_widget)
        
        table_group = QGroupBox("Risultati Verifica")
        table_layout = QVBoxLayout(table_group)
        self.p2_table = QTableWidget()
        self.p2_table.setColumnCount(10)
        self.p2_table.setHorizontalHeaderLabels(["Stato", "DeviceID", "Tipo", "Allarme", "Inc X", "Inc Y", "TS OK", "Delta", "Data Time", "Note"])
        self.p2_table.setAlternatingRowColors(True)
        self.p2_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.p2_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.p2_table.horizontalHeader()
        header.setStretchLastSection(True)
        self.p2_table.setColumnWidth(0, 60)
        self.p2_table.setColumnWidth(1, 120)
        self.p2_table.setColumnWidth(2, 60)
        self.p2_table.setColumnWidth(3, 70)
        self.p2_table.setColumnWidth(4, 80)
        self.p2_table.setColumnWidth(5, 80)
        self.p2_table.setColumnWidth(6, 60)
        self.p2_table.setColumnWidth(7, 80)
        self.p2_table.setColumnWidth(8, 140)
        table_layout.addWidget(self.p2_table)
        layout.addWidget(table_group, stretch=1)
        
        log_group = QGroupBox("Log Verifica")
        log_layout = QVBoxLayout(log_group)
        self.p2_log = QTextEdit()
        self.p2_log.setObjectName("logArea")
        self.p2_log.setReadOnly(True)
        self.p2_log.setMaximumHeight(150)
        log_layout.addWidget(self.p2_log)
        layout.addWidget(log_group)
        
        return tab
    
    def log_p1(self, message: str, level: str = "INFO"):
        ts = datetime.now().strftime("%H:%M:%S")
        colors = {"INFO": "#CCCCCC", "OK": "#00FF00", "WARN": "#FFCC00", "ERROR": "#FF6666"}
        self.p1_log.append(f'<span style="color: #666666;">[{ts}]</span> <span style="color: {colors.get(level, "#CCCCCC")};">{message}</span>')
        self.p1_log.verticalScrollBar().setValue(self.p1_log.verticalScrollBar().maximum())
    
    def log_reset_completed(self, deviceid: str, timestamp: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{deviceid},{timestamp}"
        self.p1_reset_log.append(f"[{ts}] {line}")
        self.p1_reset_log.verticalScrollBar().setValue(self.p1_reset_log.verticalScrollBar().maximum())
        if self.reset_log_file:
            try:
                with open(self.reset_log_file, "a", encoding="utf-8") as f:
                    f.write(f"{line}\n")
            except Exception as e:
                self.log_p1(f"Errore scrittura file reset: {e}", "ERROR")
    
    def log_p2(self, message: str, level: str = "INFO"):
        ts = datetime.now().strftime("%H:%M:%S")
        colors = {"INFO": "#CCCCCC", "OK": "#00FF00", "WARN": "#FFCC00", "ERROR": "#FF6666"}
        self.p2_log.append(f'<span style="color: #666666;">[{ts}]</span> <span style="color: {colors.get(level, "#CCCCCC")};">{message}</span>')
        self.p2_log.verticalScrollBar().setValue(self.p2_log.verticalScrollBar().maximum())
    
    def load_input_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Seleziona File Input", "", "Excel Files (*.xlsx *.xls);;All Files (*)")
        if not file_path:
            return
        success, msg, count = self.input_loader.load_file(file_path)
        if success:
            self.p1_file_label.setText(f"✓ {Path(file_path).name} ({count} dispositivi)")
            self.p1_file_label.setStyleSheet("color: #009933;")
            self.p1_start_btn.setEnabled(True)
            self.log_p1(f"File caricato: {count} dispositivi", "OK")
            self.status_label.setText(f"File caricato: {count} dispositivi pronti")
        else:
            QMessageBox.warning(self, "Errore", msg)
            self.log_p1(msg, "ERROR")
    
    def start_phase1(self):
        device_ids = self.input_loader.get_device_ids()
        if not device_ids:
            QMessageBox.warning(self, "Errore", "Nessun dispositivo da processare")
            return
        
        reply = QMessageBox.question(self, "Conferma Reset",
            f"Avviare il reset per {len(device_ids)} dispositivi?\n\nThread: {self.p1_threads_spin.value()}\nIntervallo: {self.p1_interval_spin.value()}s",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if reply != QMessageBox.Yes:
            return
        
        tm = get_token_manager()
        success, msg = tm.validate_config()
        if not success:
            QMessageBox.critical(self, "Errore Autenticazione", msg)
            return
        
        self.log_p1("Autenticazione OK", "OK")
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.reset_log_file = Path.home() / "Downloads" / f"reset_completati_{timestamp}.txt"
        try:
            with open(self.reset_log_file, "w", encoding="utf-8") as f:
                f.write("deviceid,reset_timestamp\n")
            self.log_p1(f"File reset: {self.reset_log_file}")
        except Exception as e:
            self.log_p1(f"Errore creazione file: {e}", "ERROR")
        
        self.reset_results = []
        self.p1_table.setRowCount(0)
        self.p1_progress.setMaximum(len(device_ids))
        self.p1_progress.setValue(0)
        self.p1_log.clear()
        self.p1_reset_log.clear()
        
        self.p1_start_btn.setEnabled(False)
        self.p1_stop_btn.setEnabled(True)
        self.p1_export_btn.setEnabled(False)
        self.p1_load_btn.setEnabled(False)
        
        for did in device_ids:
            row = self.p1_table.rowCount()
            self.p1_table.insertRow(row)
            status_item = QTableWidgetItem("⏳")
            status_item.setTextAlignment(Qt.AlignCenter)
            status_item.setData(Qt.UserRole, did)
            self.p1_table.setItem(row, 0, status_item)
            self.p1_table.setItem(row, 1, QTableWidgetItem(did))
            self.p1_table.setItem(row, 2, QTableWidgetItem(detect_device_type(did)))
            for col in range(3, 8):
                self.p1_table.setItem(row, col, QTableWidgetItem("-"))
        
        self.log_p1(f"Avvio reset per {len(device_ids)} dispositivi...")
        
        self.reset_thread = ResetThread(device_ids)
        self.reset_thread.worker.max_threads = self.p1_threads_spin.value()
        self.reset_thread.worker.check_interval = self.p1_interval_spin.value()
        self.reset_thread.progress_signal.connect(self.on_reset_progress)
        self.reset_thread.device_complete_signal.connect(self.on_reset_device_complete)
        self.reset_thread.completed_signal.connect(self.on_reset_completed)
        self.reset_thread.stats_signal.connect(self.on_reset_stats)
        self.reset_thread.log_signal.connect(self.log_p1)
        self.reset_thread.start()
    
    def stop_phase1(self):
        if self.reset_thread:
            self.log_p1("Arresto richiesto, attendi...", "WARN")
            self.p1_stop_btn.setEnabled(False)
            self.status_label.setText("Arresto in corso...")
            QApplication.processEvents()
            
            # Ferma il worker
            self.reset_thread.stop()
            self.reset_thread.wait(5000)  # Attendi max 5 secondi
            
            # Controlla se ci sono device con maintenance ON
            devices_with_maint_on = self.reset_thread.get_devices_with_maintenance_on()
            
            if devices_with_maint_on:
                self.log_p1(f"{len(devices_with_maint_on)} dispositivi hanno maintenance ON", "WARN")
                
                reply = QMessageBox.question(self, "Cleanup Maintenance",
                    f"⚠️ {len(devices_with_maint_on)} dispositivi hanno ancora la maintenance attiva.\n\n"
                    "Inviare comando maintenance OFF a questi dispositivi?\n\n"
                    "• Sì = Invia maintenance OFF\n"
                    "• No = Lascia come sono",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                
                if reply == QMessageBox.Yes:
                    self.status_label.setText(f"Cleanup: invio maintenance OFF a {len(devices_with_maint_on)} dispositivi...")
                    QApplication.processEvents()
                    
                    results = self.reset_thread.send_maintenance_off_to_pending()
                    ok = sum(1 for s in results.values() if s)
                    
                    self.log_p1(f"Cleanup completato: {ok}/{len(results)} OK", "OK" if ok == len(results) else "WARN")
            
            # Riabilita controlli
            self.p1_start_btn.setEnabled(True)
            self.p1_load_btn.setEnabled(True)
            self.p1_threads_spin.setEnabled(True)
            self.p1_interval_spin.setEnabled(True)
            self.p1_export_btn.setEnabled(len(self.reset_results) > 0)
            
            self.log_p1("Esecuzione interrotta", "WARN")
            self.status_label.setText("Esecuzione interrotta")
    
    def update_reset_row(self, result: ResetResult):
        for row in range(self.p1_table.rowCount()):
            item = self.p1_table.item(row, 0)
            if item and item.data(Qt.UserRole) == result.deviceid:
                if result.status == ResetStatus.OK:
                    status, color = "✅", QColor("#C6EFCE")
                elif result.status in [ResetStatus.IN_PROGRESS, ResetStatus.MAINT_ON, ResetStatus.RESET_CMD, ResetStatus.MAINT_OFF]:
                    status, color = "🔄", QColor("#FFEB9C")
                elif result.status == ResetStatus.INTERRUPTED:
                    status, color = "⏹️", QColor("#F4CCCC")
                else:
                    status, color = "❌", QColor("#FFC7CE")
                
                item.setText(status)
                for col in range(self.p1_table.columnCount()):
                    if self.p1_table.item(row, col):
                        self.p1_table.item(row, col).setBackground(color)
                
                self.p1_table.item(row, 3).setText(result.manutenzione_on)
                self.p1_table.item(row, 4).setText(result.reset_inclinometro)
                self.p1_table.item(row, 5).setText(result.manutenzione_off)
                self.p1_table.item(row, 6).setText(result.maintenance_state.value)
                self.p1_table.item(row, 7).setText(result.reset_datetime)
                break
    
    def on_reset_progress(self, result: ResetResult, message: str):
        self.update_reset_row(result)
        self.status_label.setText(f"{result.deviceid}: {message}")
    
    def on_reset_device_complete(self, result: ResetResult):
        self.reset_results.append(result)
        self.update_reset_row(result)
        completed = len([r for r in self.reset_results if r.status in [ResetStatus.OK, ResetStatus.FAILED, ResetStatus.ERROR, ResetStatus.INTERRUPTED]])
        self.p1_progress.setValue(completed)
        if result.status == ResetStatus.OK and result.reset_timestamp:
            self.log_reset_completed(result.deviceid, str(result.reset_timestamp))
    
    def on_reset_stats(self, stats: dict):
        self.p1_stats_label.setText(f"OK: {stats['success']} | KO: {stats['failed']} | In corso: {stats['in_progress']}")
    
    def on_reset_completed(self, results: List[ResetResult]):
        self.reset_results = results
        self.p1_start_btn.setEnabled(True)
        self.p1_stop_btn.setEnabled(False)
        self.p1_export_btn.setEnabled(True)
        self.p1_load_btn.setEnabled(True)
        
        ok_count = sum(1 for r in results if r.status == ResetStatus.OK)
        ko_count = len(results) - ok_count
        self.log_p1(f"Completato: {ok_count} OK, {ko_count} KO", "OK" if ko_count == 0 else "WARN")
        self.status_label.setText(f"Fase 1 completata: {ok_count} OK, {ko_count} problemi")
        QMessageBox.information(self, "Fase 1 Completata", f"Reset completato.\n\n✅ OK: {ok_count}\n❌ Problemi: {ko_count}\n\nFile: {self.reset_log_file}")
    
    def export_phase1(self):
        if not self.reset_results:
            return
        default_name = f"Reset_Fase1_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        file_path, _ = QFileDialog.getSaveFileName(self, "Salva", str(Path.home() / "Downloads" / default_name), "Excel (*.xlsx)")
        if file_path:
            success, result = self.exporter.export_reset_results(self.reset_results, file_path)
            if success:
                QMessageBox.information(self, "OK", f"Salvato: {result}")
            else:
                QMessageBox.critical(self, "Errore", result)
    
    def load_phase2_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Seleziona File", "", "Excel (*.xlsx *.xls)")
        if not file_path:
            return
        success, msg, count = self.phase2_loader.load_file(file_path)
        if success:
            self.p2_file_label.setText(f"✓ {Path(file_path).name}")
            self.p2_file_label.setStyleSheet("color: #009933;")
            self.p2_start_btn.setEnabled(count > 0)
            self.p2_count_label.setText(f"Dispositivi: {count}")
            self.log_p2(f"Caricati: {msg}", "OK")
        else:
            QMessageBox.warning(self, "Errore", msg)
    
    def start_phase2(self):
        devices = self.phase2_loader.get_devices()
        if not devices:
            return
        
        tm = get_token_manager()
        success, msg = tm.validate_config()
        if not success:
            QMessageBox.critical(self, "Errore", msg)
            return
        
        self.verify_results = []
        self.p2_table.setRowCount(0)
        self.p2_progress.setMaximum(len(devices))
        self.p2_progress.setValue(0)
        
        self.p2_start_btn.setEnabled(False)
        self.p2_stop_btn.setEnabled(True)
        self.p2_export_btn.setEnabled(False)
        
        for device in devices:
            row = self.p2_table.rowCount()
            self.p2_table.insertRow(row)
            status_item = QTableWidgetItem("⏳")
            status_item.setData(Qt.UserRole, device["deviceid"])
            self.p2_table.setItem(row, 0, status_item)
            self.p2_table.setItem(row, 1, QTableWidgetItem(device["deviceid"]))
            self.p2_table.setItem(row, 2, QTableWidgetItem(device.get("tipo", "")))
            for col in range(3, 10):
                self.p2_table.setItem(row, col, QTableWidgetItem("-"))
        
        self.verify_thread = VerifyThread(devices)
        self.verify_thread.progress_signal.connect(self.on_verify_progress)
        self.verify_thread.device_complete_signal.connect(self.on_verify_device_complete)
        self.verify_thread.completed_signal.connect(self.on_verify_completed)
        self.verify_thread.stats_signal.connect(self.on_verify_stats)
        self.verify_thread.start()
    
    def stop_phase2(self):
        if self.verify_thread:
            self.verify_thread.stop()
            self.p2_stop_btn.setEnabled(False)
    
    def update_verify_row(self, result: VerifyResult):
        for row in range(self.p2_table.rowCount()):
            item = self.p2_table.item(row, 0)
            if item and item.data(Qt.UserRole) == result.deviceid:
                if result.all_ok:
                    status, color = "✅", QColor("#C6EFCE")
                elif result.status == VerifyStatus.IN_PROGRESS:
                    status, color = "🔄", QColor("#FFEB9C")
                else:
                    status, color = "❌", QColor("#FFC7CE")
                
                item.setText(status)
                for col in range(self.p2_table.columnCount()):
                    if self.p2_table.item(row, col):
                        self.p2_table.item(row, col).setBackground(color)
                
                if result.alarm_incl is not None:
                    self.p2_table.item(row, 3).setText("false ✓" if not result.alarm_incl else "true ✗")
                if result.inc_x_avg is not None:
                    self.p2_table.item(row, 4).setText(f"{result.inc_x_avg:.3f}")
                if result.inc_y_avg is not None:
                    self.p2_table.item(row, 5).setText(f"{result.inc_y_avg:.3f}")
                self.p2_table.item(row, 6).setText("✓" if result.timestamp_valid else "✗")
                self.p2_table.item(row, 7).setText(result.timestamp_delta_readable)
                self.p2_table.item(row, 8).setText(result.data_datetime or "-")
                self.p2_table.item(row, 9).setText(result.error_message)
                break
    
    def on_verify_progress(self, result: VerifyResult, message: str):
        self.update_verify_row(result)
        self.status_label.setText(f"{result.deviceid}: {message}")
    
    def on_verify_device_complete(self, result: VerifyResult):
        self.verify_results.append(result)
        self.update_verify_row(result)
        self.p2_progress.setValue(len(self.verify_results))
        self.log_p2(f"{result.deviceid}: {'OK' if result.all_ok else result.error_message}", "OK" if result.all_ok else "WARN")
    
    def on_verify_stats(self, stats: dict):
        self.p2_stats_label.setText(f"OK: {stats['verified']} | Problemi: {stats['failed']}")
    
    def on_verify_completed(self, results: List[VerifyResult]):
        self.verify_results = results
        self.p2_start_btn.setEnabled(True)
        self.p2_stop_btn.setEnabled(False)
        self.p2_export_btn.setEnabled(True)
        self.p2_load_btn.setEnabled(True)
        
        ok = sum(1 for r in results if r.all_ok)
        ko = len(results) - ok
        self.log_p2(f"Completato: {ok} OK, {ko} problemi", "OK" if ko == 0 else "WARN")
        QMessageBox.information(self, "Completato", f"✅ OK: {ok}\n⚠️ Problemi: {ko}")
    
    def export_phase2(self):
        if not self.verify_results:
            return
        default_name = f"Verify_Fase2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        file_path, _ = QFileDialog.getSaveFileName(self, "Salva", str(Path.home() / "Downloads" / default_name), "Excel (*.xlsx)")
        if file_path:
            success, result = self.exporter.export_verify_results(self.verify_results, file_path)
            if success:
                QMessageBox.information(self, "OK", f"Salvato: {result}")
            else:
                QMessageBox.critical(self, "Errore", result)
    
    def closeEvent(self, event):
        running = (self.reset_thread and self.reset_thread.isRunning()) or (self.verify_thread and self.verify_thread.isRunning())
        
        if running:
            # Prima ferma i thread
            if self.reset_thread and self.reset_thread.isRunning():
                self.reset_thread.stop()
            if self.verify_thread and self.verify_thread.isRunning():
                self.verify_thread.stop()
            
            # Aspetta un po' che si fermino
            self.status_label.setText("Arresto in corso...")
            QApplication.processEvents()
            
            if self.reset_thread:
                self.reset_thread.wait(3000)
            if self.verify_thread:
                self.verify_thread.wait(3000)
            
            # Ora controlla se ci sono device con maintenance ON
            devices_with_maint_on = []
            if self.reset_thread:
                devices_with_maint_on = self.reset_thread.get_devices_with_maintenance_on()
            
            self.log_p1(f"Chiusura: {len(devices_with_maint_on)} dispositivi con maintenance ON", "WARN" if devices_with_maint_on else "INFO")
            
            if devices_with_maint_on:
                reply = QMessageBox.question(self, "Cleanup Maintenance",
                    f"⚠️ {len(devices_with_maint_on)} dispositivi hanno ancora la maintenance attiva.\n\n"
                    f"Dispositivi: {', '.join(devices_with_maint_on[:5])}{'...' if len(devices_with_maint_on) > 5 else ''}\n\n"
                    "Inviare comando maintenance OFF a questi dispositivi?\n\n"
                    "• Sì = Invia maintenance OFF e chiudi\n"
                    "• No = Chiudi senza cleanup (potrebbero restare in manutenzione)\n"
                    "• Annulla = Torna all'applicazione",
                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Yes)
                
                if reply == QMessageBox.Cancel:
                    # Riabilita i controlli
                    self.p1_start_btn.setEnabled(True)
                    self.p1_load_btn.setEnabled(True)
                    self.status_label.setText("Chiusura annullata")
                    event.ignore()
                    return
                
                if reply == QMessageBox.Yes:
                    self.status_label.setText(f"Invio maintenance OFF a {len(devices_with_maint_on)} dispositivi...")
                    QApplication.processEvents()
                    
                    results = self.reset_thread.send_maintenance_off_to_pending()
                    ok = sum(1 for s in results.values() if s)
                    fail = len(results) - ok
                    
                    msg = f"Cleanup completato:\n✅ OK: {ok}\n❌ Falliti: {fail}"
                    if fail > 0:
                        failed_devices = [d for d, s in results.items() if not s]
                        msg += f"\n\nDispositivi falliti:\n{', '.join(failed_devices[:10])}"
                    
                    QMessageBox.information(self, "Cleanup Completato", msg)
            
            event.accept()
        else:
            event.accept()


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("DIGIL Reset Inclinometro")
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
