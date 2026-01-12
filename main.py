"""
DIGIL Reset Inclinometro - Main GUI Application
===============================================
Tool per il reset e la verifica dell'inclinometro sui dispositivi DIGIL.

Fase 1: Reset inclinometro con manutenzione ON/OFF
Fase 2: Verifica che allarme sia false e inclinazioni ~0

Versione: 1.0.0
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
    QTextEdit
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QPixmap

from reset_worker import ResetWorker, ResetResult, ResetStatus, detect_device_type
from verify_worker import VerifyWorker, VerifyResult, VerifyStatus
from data_handler import InputLoader, ResultExporter
from api_client import get_token_manager


# ============================================================
# STILE CSS - TERNA PROFESSIONAL
# ============================================================
TERNA_STYLE = """
QMainWindow { background-color: #FFFFFF; }
QWidget { font-family: 'Segoe UI', Arial, sans-serif; font-size: 13px; }
QLabel#headerTitle { font-size: 22px; font-weight: bold; color: #0066CC; }
QLabel#headerSubtitle { font-size: 11px; color: #666666; }

QGroupBox {
    font-weight: bold; border: 1px solid #CCCCCC; border-radius: 6px;
    margin-top: 12px; padding-top: 10px; background-color: #FAFAFA;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #0066CC; }

QPushButton {
    background-color: #0066CC; color: white; border: none;
    padding: 8px 16px; border-radius: 4px; font-weight: bold; min-width: 100px;
}
QPushButton:hover { background-color: #004C99; }
QPushButton:pressed { background-color: #003366; }
QPushButton:disabled { background-color: #CCCCCC; color: #666666; }
QPushButton#stopButton { background-color: #CC3300; }
QPushButton#stopButton:hover { background-color: #992600; }
QPushButton#exportButton { background-color: #009933; }
QPushButton#exportButton:hover { background-color: #006622; }
QPushButton#secondaryButton { background-color: #FFFFFF; color: #0066CC; border: 2px solid #0066CC; }
QPushButton#secondaryButton:hover { background-color: #E6F2FF; }

QTableWidget {
    border: 1px solid #CCCCCC; border-radius: 4px; gridline-color: #E0E0E0;
    background-color: white; alternate-background-color: #F8FBFF;
}
QTableWidget::item { padding: 5px; }
QTableWidget::item:selected { background-color: #CCE5FF; color: black; }
QHeaderView::section { background-color: #0066CC; color: white; padding: 8px; border: none; font-weight: bold; }

QProgressBar {
    border: 1px solid #CCCCCC; border-radius: 4px; text-align: center;
    background-color: #F0F0F0; height: 25px;
}
QProgressBar::chunk { background-color: #0066CC; border-radius: 3px; }

QSpinBox { border: 1px solid #CCCCCC; border-radius: 4px; padding: 5px; background-color: white; }

QTextEdit#logArea {
    border: 1px solid #CCCCCC; border-radius: 4px; background-color: #1E1E1E;
    color: #CCCCCC; font-family: 'Consolas', 'Courier New', monospace; font-size: 11px;
}

QStatusBar { background-color: #F5F5F5; border-top: 1px solid #CCCCCC; }

QTabWidget::pane { border: 1px solid #CCCCCC; border-radius: 4px; background-color: white; }
QTabBar::tab {
    background-color: #F0F0F0; border: 1px solid #CCCCCC;
    padding: 10px 20px; margin-right: 2px; font-weight: bold;
}
QTabBar::tab:selected { background-color: #0066CC; color: white; }
QTabBar::tab:hover:!selected { background-color: #E6F2FF; }
"""


# ============================================================
# WORKER THREADS
# ============================================================

class ResetThread(QThread):
    """Thread per eseguire la Fase 1 (Reset)"""
    progress_signal = pyqtSignal(object, str)
    device_complete_signal = pyqtSignal(object)
    completed_signal = pyqtSignal(list)
    stats_signal = pyqtSignal(dict)
    
    def __init__(self, device_ids: List[str]):
        super().__init__()
        self.device_ids = device_ids
        self.worker = ResetWorker()
    
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


class VerifyThread(QThread):
    """Thread per eseguire la Fase 2 (Verifica)"""
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


# ============================================================
# MAIN WINDOW
# ============================================================

class MainWindow(QMainWindow):
    """Finestra principale dell'applicazione"""
    
    def __init__(self):
        super().__init__()
        self.input_loader = InputLoader()
        self.exporter = ResultExporter()
        self.reset_thread: Optional[ResetThread] = None
        self.verify_thread: Optional[VerifyThread] = None
        self.reset_results: List[ResetResult] = []
        self.verify_results: List[VerifyResult] = []
        
        self.init_ui()
        self.apply_style()
        self.load_logo()
    
    def init_ui(self):
        """Inizializza l'interfaccia"""
        self.setWindowTitle("DIGIL Reset Inclinometro - Terna IoT Team")
        self.setMinimumSize(1300, 850)
        
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(20, 20, 20, 20)
        
        # Header
        main_layout.addWidget(self.create_header())
        
        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("background-color: #CCCCCC; max-height: 1px;")
        main_layout.addWidget(sep)
        
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
    
    def create_header(self) -> QWidget:
        """Crea l'header con logo e titolo"""
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 10)
        
        self.logo_label = QLabel()
        self.logo_label.setFixedSize(120, 50)
        self.logo_label.setStyleSheet("background-color: #0066CC; border-radius: 8px; color: white; font-size: 24px; font-weight: bold;")
        self.logo_label.setText("T")
        self.logo_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.logo_label)
        
        title_widget = QWidget()
        title_layout = QVBoxLayout(title_widget)
        title_layout.setContentsMargins(15, 0, 0, 0)
        title_layout.setSpacing(2)
        
        title = QLabel("DIGIL Reset Inclinometro")
        title.setObjectName("headerTitle")
        title_layout.addWidget(title)
        
        subtitle = QLabel("Reset e verifica inclinometri dispositivi IoT - Terna S.p.A.")
        subtitle.setObjectName("headerSubtitle")
        title_layout.addWidget(subtitle)
        
        layout.addWidget(title_widget)
        layout.addStretch()
        
        version = QLabel("v1.0.0")
        version.setStyleSheet("color: #999999; font-size: 11px;")
        layout.addWidget(version)
        
        return header
    
    def create_phase1_tab(self) -> QWidget:
        """Crea il tab per la Fase 1 (Reset)"""
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
        self.p1_threads_spin.setRange(1, 100)
        self.p1_threads_spin.setValue(87)
        options_row.addWidget(self.p1_threads_spin)
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
        
        # Tabella
        table_group = QGroupBox("Risultati Reset")
        table_layout = QVBoxLayout(table_group)
        
        self.p1_table = QTableWidget()
        self.p1_table.setColumnCount(7)
        self.p1_table.setHorizontalHeaderLabels(["Stato", "DeviceID", "Tipo", "Manutenzione ON", "Reset Incl.", "Manutenzione OFF", "Timestamp Reset"])
        self.p1_table.setAlternatingRowColors(True)
        self.p1_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.p1_table.setSortingEnabled(True)
        self.p1_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        
        header = self.p1_table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.p1_table.setColumnWidth(0, 60)
        self.p1_table.setColumnWidth(1, 120)
        self.p1_table.setColumnWidth(2, 60)
        self.p1_table.setColumnWidth(3, 120)
        self.p1_table.setColumnWidth(4, 100)
        self.p1_table.setColumnWidth(5, 120)
        
        table_layout.addWidget(self.p1_table)
        layout.addWidget(table_group, stretch=1)
        
        # Log
        log_group = QGroupBox("Log Operazioni")
        log_layout = QVBoxLayout(log_group)
        self.p1_log = QTextEdit()
        self.p1_log.setObjectName("logArea")
        self.p1_log.setReadOnly(True)
        self.p1_log.setMaximumHeight(120)
        log_layout.addWidget(self.p1_log)
        layout.addWidget(log_group)
        
        return tab
    
    def create_phase2_tab(self) -> QWidget:
        """Crea il tab per la Fase 2 (Verifica)"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)
        
        # Controlli
        controls = QGroupBox("Configurazione Verifica")
        controls_layout = QVBoxLayout(controls)
        
        info_row = QHBoxLayout()
        self.p2_info_label = QLabel("⚠️ Esegui prima la Fase 1 per avere dispositivi da verificare")
        self.p2_info_label.setStyleSheet("color: #996600; font-weight: bold;")
        info_row.addWidget(self.p2_info_label)
        info_row.addStretch()
        self.p2_count_label = QLabel("Dispositivi OK da verificare: 0")
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
        
        # Progress
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
        
        # Tabella
        table_group = QGroupBox("Risultati Verifica")
        table_layout = QVBoxLayout(table_group)
        
        self.p2_table = QTableWidget()
        self.p2_table.setColumnCount(10)
        self.p2_table.setHorizontalHeaderLabels(["Stato", "DeviceID", "Tipo", "Allarme", "Inc X", "Inc Y", "TS OK", "Delta", "Reset Time", "Note"])
        self.p2_table.setAlternatingRowColors(True)
        self.p2_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.p2_table.setSortingEnabled(True)
        self.p2_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        
        header = self.p2_table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
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
        
        # Log
        log_group = QGroupBox("Log Verifica")
        log_layout = QVBoxLayout(log_group)
        self.p2_log = QTextEdit()
        self.p2_log.setObjectName("logArea")
        self.p2_log.setReadOnly(True)
        self.p2_log.setMaximumHeight(120)
        log_layout.addWidget(self.p2_log)
        layout.addWidget(log_group)
        
        return tab
    
    def apply_style(self):
        self.setStyleSheet(TERNA_STYLE)
    
    def load_logo(self):
        """Carica il logo Terna dalla cartella assets"""
        # Percorsi possibili per il logo
        script_dir = Path(__file__).parent
        possible_paths = [
            script_dir / "assets" / "logo_terna.png",
            script_dir / "assets" / "logo.png",
            script_dir / "assets" / "terna_logo.png",
            script_dir / "logo_terna.png",
            script_dir / "logo.png",
            Path.cwd() / "assets" / "logo_terna.png",
            Path.cwd() / "assets" / "logo.png",
        ]
        
        for path in possible_paths:
            if path.exists():
                try:
                    pixmap = QPixmap(str(path))
                    if not pixmap.isNull():
                        self.logo_label.setPixmap(pixmap.scaled(120, 50, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                        self.logo_label.setText("")
                        self.logo_label.setStyleSheet("background-color: transparent;")
                        print(f"Logo caricato da: {path}")
                        return
                except Exception as e:
                    print(f"Errore caricamento logo da {path}: {e}")
        
        print("Logo non trovato - usando placeholder")
    
    def log_p1(self, message: str, level: str = "INFO"):
        ts = datetime.now().strftime("%H:%M:%S")
        colors = {"INFO": "#CCCCCC", "OK": "#00CC00", "WARN": "#FFCC00", "ERROR": "#FF6666"}
        self.p1_log.append(f'<span style="color: #666666;">[{ts}]</span> <span style="color: {colors.get(level, "#CCCCCC")};">{message}</span>')
        self.p1_log.verticalScrollBar().setValue(self.p1_log.verticalScrollBar().maximum())
    
    def log_p2(self, message: str, level: str = "INFO"):
        ts = datetime.now().strftime("%H:%M:%S")
        colors = {"INFO": "#CCCCCC", "OK": "#00CC00", "WARN": "#FFCC00", "ERROR": "#FF6666"}
        self.p2_log.append(f'<span style="color: #666666;">[{ts}]</span> <span style="color: {colors.get(level, "#CCCCCC")};">{message}</span>')
        self.p2_log.verticalScrollBar().setValue(self.p2_log.verticalScrollBar().maximum())
    
    # ========== FASE 1 ==========
    
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
            summary = self.input_loader.get_summary()
            master_count = summary.get('master', 0)
            slave_count = summary.get('slave', 0)
            self.log_p1(f"Master: {master_count} | Slave: {slave_count}", "INFO")
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
            f"Avviare il reset inclinometro per {len(device_ids)} dispositivi?\n\n"
            f"Thread paralleli: {self.p1_threads_spin.value()}\n\n"
            "Il processo eseguirà:\n1. Manutenzione ON\n2. Reset inclinometro\n3. Manutenzione OFF",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        
        if reply != QMessageBox.Yes:
            return
        
        tm = get_token_manager()
        success, msg = tm.validate_config()
        if not success:
            QMessageBox.critical(self, "Errore Autenticazione", msg)
            self.log_p1(msg, "ERROR")
            return
        
        self.log_p1("Autenticazione OK", "OK")
        
        self.reset_results = []
        self.p1_table.setRowCount(0)
        self.p1_progress.setMaximum(len(device_ids))
        self.p1_progress.setValue(0)
        
        self.p1_start_btn.setEnabled(False)
        self.p1_stop_btn.setEnabled(True)
        self.p1_export_btn.setEnabled(False)
        self.p1_load_btn.setEnabled(False)
        
        for did in device_ids:
            self.add_reset_row(did)
        
        self.log_p1(f"Avvio reset per {len(device_ids)} dispositivi...", "INFO")
        
        self.reset_thread = ResetThread(device_ids)
        self.reset_thread.progress_signal.connect(self.on_reset_progress)
        self.reset_thread.device_complete_signal.connect(self.on_reset_device_complete)
        self.reset_thread.completed_signal.connect(self.on_reset_completed)
        self.reset_thread.stats_signal.connect(self.on_reset_stats)
        self.reset_thread.start()
    
    def stop_phase1(self):
        if self.reset_thread:
            self.reset_thread.stop()
            self.log_p1("Interruzione richiesta...", "WARN")
            self.p1_stop_btn.setEnabled(False)
    
    def add_reset_row(self, deviceid: str):
        row = self.p1_table.rowCount()
        self.p1_table.insertRow(row)
        
        status_item = QTableWidgetItem("⏳")
        status_item.setTextAlignment(Qt.AlignCenter)
        status_item.setData(Qt.UserRole, deviceid)
        self.p1_table.setItem(row, 0, status_item)
        self.p1_table.setItem(row, 1, QTableWidgetItem(deviceid))
        self.p1_table.setItem(row, 2, QTableWidgetItem(detect_device_type(deviceid)))
        for col in range(3, 7):
            self.p1_table.setItem(row, col, QTableWidgetItem("-"))
    
    def update_reset_row(self, result: ResetResult):
        for row in range(self.p1_table.rowCount()):
            item = self.p1_table.item(row, 0)
            if item and item.data(Qt.UserRole) == result.deviceid:
                if result.status == ResetStatus.OK:
                    status, color = "✅", QColor("#C6EFCE")
                elif result.status == ResetStatus.IN_PROGRESS:
                    status, color = "🔄", QColor("#FFEB9C")
                elif result.status in [ResetStatus.FAILED, ResetStatus.ERROR]:
                    status, color = "❌", QColor("#FFC7CE")
                else:
                    status, color = "⏳", QColor("#FFFFFF")
                
                item.setText(status)
                for col in range(self.p1_table.columnCount()):
                    cell = self.p1_table.item(row, col)
                    if cell:
                        cell.setBackground(color)
                
                self.p1_table.item(row, 3).setText(result.manutenzione_on)
                self.p1_table.item(row, 4).setText(result.reset_inclinometro)
                self.p1_table.item(row, 5).setText(result.manutenzione_off)
                self.p1_table.item(row, 6).setText(result.reset_datetime)
                break
    
    def on_reset_progress(self, result: ResetResult, message: str):
        self.update_reset_row(result)
        self.status_label.setText(f"Reset: {result.deviceid} - {message}")
    
    def on_reset_device_complete(self, result: ResetResult):
        self.reset_results.append(result)
        self.update_reset_row(result)
        self.p1_progress.setValue(len(self.reset_results))
        self.log_p1(f"{result.deviceid}: {result.reset_inclinometro}", "OK" if result.status == ResetStatus.OK else "ERROR")
    
    def on_reset_stats(self, stats: dict):
        self.p1_stats_label.setText(f"OK: {stats['success']} | KO: {stats['failed']} | In corso: {stats['in_progress']}")
    
    def on_reset_completed(self, results: List[ResetResult]):
        self.reset_results = results
        self.p1_start_btn.setEnabled(True)
        self.p1_stop_btn.setEnabled(False)
        self.p1_export_btn.setEnabled(True)
        self.p1_load_btn.setEnabled(True)
        
        ok_count = sum(1 for r in results if r.reset_inclinometro == "OK")
        ko_count = len(results) - ok_count
        
        self.log_p1(f"Fase 1 completata: {ok_count} OK, {ko_count} KO", "OK" if ko_count == 0 else "WARN")
        self.status_label.setText(f"Fase 1 completata: {ok_count} OK, {ko_count} problemi")
        
        ok_results = [r for r in results if r.reset_inclinometro == "OK"]
        if ok_results:
            self.p2_info_label.setText(f"✓ {len(ok_results)} dispositivi pronti per la verifica")
            self.p2_info_label.setStyleSheet("color: #009933; font-weight: bold;")
            self.p2_count_label.setText(f"Dispositivi OK da verificare: {len(ok_results)}")
            self.p2_start_btn.setEnabled(True)
        
        QMessageBox.information(self, "Fase 1 Completata",
            f"Reset completato per {len(results)} dispositivi.\n\n✅ OK: {ok_count}\n❌ Problemi: {ko_count}")
    
    def export_phase1(self):
        if not self.reset_results:
            QMessageBox.warning(self, "Errore", "Nessun risultato da esportare")
            return
        
        default_name = f"Reset_Inclinometro_Fase1_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        file_path, _ = QFileDialog.getSaveFileName(self, "Salva Risultati Fase 1", str(Path.home() / "Downloads" / default_name), "Excel Files (*.xlsx)")
        
        if file_path:
            success, result = self.exporter.export_reset_results(self.reset_results, file_path)
            if success:
                self.log_p1(f"Esportato: {result}", "OK")
                QMessageBox.information(self, "Export Completato", f"File salvato:\n{result}")
            else:
                self.log_p1(result, "ERROR")
                QMessageBox.critical(self, "Errore Export", result)
    
    # ========== FASE 2 ==========
    
    def start_phase2(self):
        ok_results = [r for r in self.reset_results if r.reset_inclinometro == "OK" and r.reset_timestamp]
        if not ok_results:
            QMessageBox.warning(self, "Errore", "Nessun dispositivo con reset OK da verificare")
            return
        
        devices_to_verify = [{"deviceid": r.deviceid, "reset_timestamp": r.reset_timestamp, "tipo": r.tipo} for r in ok_results]
        
        reply = QMessageBox.question(self, "Conferma Verifica",
            f"Avviare la verifica per {len(devices_to_verify)} dispositivi?\n\n"
            "La verifica controllerà:\n• Allarme inclinometro = false\n• Inc X avg ~ 0 (±0.20)\n• Inc Y avg ~ 0 (±0.20)\n• Timestamp dati > timestamp reset",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        
        if reply != QMessageBox.Yes:
            return
        
        self.verify_results = []
        self.p2_table.setRowCount(0)
        self.p2_progress.setMaximum(len(devices_to_verify))
        self.p2_progress.setValue(0)
        
        self.p2_start_btn.setEnabled(False)
        self.p2_stop_btn.setEnabled(True)
        self.p2_export_btn.setEnabled(False)
        
        for device in devices_to_verify:
            self.add_verify_row(device)
        
        self.log_p2(f"Avvio verifica per {len(devices_to_verify)} dispositivi...", "INFO")
        
        self.verify_thread = VerifyThread(devices_to_verify)
        self.verify_thread.progress_signal.connect(self.on_verify_progress)
        self.verify_thread.device_complete_signal.connect(self.on_verify_device_complete)
        self.verify_thread.completed_signal.connect(self.on_verify_completed)
        self.verify_thread.stats_signal.connect(self.on_verify_stats)
        self.verify_thread.start()
    
    def stop_phase2(self):
        if self.verify_thread:
            self.verify_thread.stop()
            self.log_p2("Interruzione richiesta...", "WARN")
            self.p2_stop_btn.setEnabled(False)
    
    def add_verify_row(self, device: dict):
        row = self.p2_table.rowCount()
        self.p2_table.insertRow(row)
        
        status_item = QTableWidgetItem("⏳")
        status_item.setTextAlignment(Qt.AlignCenter)
        status_item.setData(Qt.UserRole, device["deviceid"])
        self.p2_table.setItem(row, 0, status_item)
        self.p2_table.setItem(row, 1, QTableWidgetItem(device["deviceid"]))
        self.p2_table.setItem(row, 2, QTableWidgetItem(device.get("tipo", "")))
        for col in range(3, 10):
            self.p2_table.setItem(row, col, QTableWidgetItem("-"))
        
        if device.get("reset_timestamp"):
            reset_dt = datetime.fromtimestamp(device["reset_timestamp"] / 1000).strftime("%Y-%m-%d %H:%M:%S")
            self.p2_table.item(row, 8).setText(reset_dt)
    
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
                    cell = self.p2_table.item(row, col)
                    if cell:
                        cell.setBackground(color)
                
                # Allarme
                if result.alarm_incl is not None:
                    alarm_text = "false ✓" if not result.alarm_incl else "true ✗"
                    self.p2_table.item(row, 3).setText(alarm_text)
                    self.p2_table.item(row, 3).setBackground(QColor("#C6EFCE") if result.alarm_ok else QColor("#FFC7CE"))
                
                # Inc X
                if result.inc_x_avg is not None:
                    self.p2_table.item(row, 4).setText(f"{result.inc_x_avg:.3f}")
                    self.p2_table.item(row, 4).setBackground(QColor("#C6EFCE") if result.inc_x_ok else QColor("#FFC7CE"))
                
                # Inc Y
                if result.inc_y_avg is not None:
                    self.p2_table.item(row, 5).setText(f"{result.inc_y_avg:.3f}")
                    self.p2_table.item(row, 5).setBackground(QColor("#C6EFCE") if result.inc_y_ok else QColor("#FFC7CE"))
                
                # Timestamp OK
                self.p2_table.item(row, 6).setText("✓" if result.timestamp_valid else "✗")
                self.p2_table.item(row, 6).setBackground(QColor("#C6EFCE") if result.timestamp_valid else QColor("#FFC7CE"))
                
                # Delta
                self.p2_table.item(row, 7).setText(result.timestamp_delta_readable)
                
                # Note
                self.p2_table.item(row, 9).setText(result.error_message)
                break
    
    def on_verify_progress(self, result: VerifyResult, message: str):
        self.update_verify_row(result)
        self.status_label.setText(f"Verifica: {result.deviceid} - {message}")
    
    def on_verify_device_complete(self, result: VerifyResult):
        self.verify_results.append(result)
        self.update_verify_row(result)
        self.p2_progress.setValue(len(self.verify_results))
        self.log_p2(f"{result.deviceid}: {'OK' if result.all_ok else result.error_message}", "OK" if result.all_ok else "WARN")
    
    def on_verify_stats(self, stats: dict):
        self.p2_stats_label.setText(f"Verificati: {stats['verified']} | Problemi: {stats['failed']}")
    
    def on_verify_completed(self, results: List[VerifyResult]):
        self.verify_results = results
        self.p2_start_btn.setEnabled(True)
        self.p2_stop_btn.setEnabled(False)
        self.p2_export_btn.setEnabled(True)
        
        verified_count = sum(1 for r in results if r.all_ok)
        problem_count = len(results) - verified_count
        
        self.log_p2(f"Fase 2 completata: {verified_count} verificati, {problem_count} con problemi", "OK" if problem_count == 0 else "WARN")
        self.status_label.setText(f"Fase 2 completata: {verified_count} OK, {problem_count} problemi")
        
        QMessageBox.information(self, "Fase 2 Completata",
            f"Verifica completata per {len(results)} dispositivi.\n\n✅ Tutti OK: {verified_count}\n⚠️ Con problemi: {problem_count}")
    
    def export_phase2(self):
        if not self.verify_results:
            QMessageBox.warning(self, "Errore", "Nessun risultato da esportare")
            return
        
        default_name = f"Reset_Inclinometro_Fase2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        file_path, _ = QFileDialog.getSaveFileName(self, "Salva Risultati Fase 2", str(Path.home() / "Downloads" / default_name), "Excel Files (*.xlsx)")
        
        if file_path:
            success, result = self.exporter.export_verify_results(self.verify_results, file_path)
            if success:
                self.log_p2(f"Esportato: {result}", "OK")
                QMessageBox.information(self, "Export Completato", f"File salvato:\n{result}")
            else:
                self.log_p2(result, "ERROR")
                QMessageBox.critical(self, "Errore Export", result)
    
    def closeEvent(self, event):
        running = (self.reset_thread and self.reset_thread.isRunning()) or (self.verify_thread and self.verify_thread.isRunning())
        if running:
            reply = QMessageBox.question(self, "Operazioni in corso", "Ci sono operazioni in corso. Vuoi interromperle e uscire?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                if self.reset_thread:
                    self.reset_thread.stop()
                    self.reset_thread.wait(3000)
                if self.verify_thread:
                    self.verify_thread.stop()
                    self.verify_thread.wait(3000)
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    
    app = QApplication(sys.argv)
    app.setApplicationName("DIGIL Reset Inclinometro")
    app.setOrganizationName("Terna")
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()