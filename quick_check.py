"""
DIGIL Reset Inclinometro - Quick Check Utilities
================================================
Utility per controlli rapidi su maintenance status e command queue.
"""

import threading
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional, List, Dict, Callable, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
from pathlib import Path

from api_client import get_api_client, get_token_manager


@dataclass
class MaintenanceStatusResult:
    """Risultato del check status maintenance"""
    deviceid: str
    status: str = "UNKNOWN"  # ON, OFF, UNKNOWN, ERROR
    error: str = ""
    
    def to_dict(self) -> Dict:
        return {
            "deviceid": self.deviceid,
            "maintenance_status": self.status,
            "error": self.error
        }


@dataclass 
class CommandQueueResult:
    """Risultato del check command queue"""
    deviceid: str
    pending_commands: List[str] = None
    sent_commands: List[str] = None
    error: str = ""
    
    def __post_init__(self):
        if self.pending_commands is None:
            self.pending_commands = []
        if self.sent_commands is None:
            self.sent_commands = []
    
    def to_dict(self) -> Dict:
        result = {"deviceid": self.deviceid}
        
        # Pending commands (fino a 5)
        for i in range(5):
            if i < len(self.pending_commands):
                result[f"pending_{i+1}"] = self.pending_commands[i]
            else:
                result[f"pending_{i+1}"] = "N/A"
        
        # Sent commands (fino a 10)
        for i in range(10):
            if i < len(self.sent_commands):
                result[f"sent_{i+1}"] = self.sent_commands[i]
            else:
                result[f"sent_{i+1}"] = "N/A"
        
        result["error"] = self.error
        return result


class QuickCheckWorker:
    """
    Worker per eseguire check rapidi su più dispositivi.
    """
    
    def __init__(self):
        self.api_client = get_api_client()
        self.max_threads = 30
        self._stop_flag = threading.Event()
    
    def stop(self):
        self._stop_flag.set()
    
    def reset(self):
        self._stop_flag.clear()
    
    def _format_command(self, cmd: Dict) -> str:
        """Formatta un comando per la visualizzazione"""
        name = cmd.get("name", "unknown")
        payload_str = cmd.get("payload", "{}")
        
        # Estrai info dal payload
        try:
            import json
            payload = json.loads(payload_str) if isinstance(payload_str, str) else payload_str
            
            if name == "maintenance":
                status = payload.get("status", "?")
                return f"maintenance {status}"
            elif name == "set_value":
                param = payload.get("param", "?")
                if "Incl_Taratura" in param:
                    return "reset_inclinometro"
                return f"set_value {param}"
            else:
                return name
        except:
            return name
    
    def check_maintenance_status(self, device_ids: List[str],
                                  progress_callback: Optional[Callable] = None) -> List[MaintenanceStatusResult]:
        """
        Verifica lo stato maintenance per una lista di device.
        
        Args:
            device_ids: Lista di deviceid
            progress_callback: Callback (deviceid, index, total)
            
        Returns:
            Lista di MaintenanceStatusResult
        """
        self.reset()
        results = []
        total = len(device_ids)
        
        def check_single(deviceid: str, index: int) -> MaintenanceStatusResult:
            result = MaintenanceStatusResult(deviceid=deviceid)
            
            if self._stop_flag.is_set():
                result.status = "INTERRUPTED"
                return result
            
            if progress_callback:
                progress_callback(deviceid, index, total)
            
            success, status, error = self.api_client.get_maintenance_status(deviceid)
            
            if success:
                if status == "ON":
                    result.status = "ON"
                elif status == "OFF":
                    result.status = "OFF"
                elif status is None:
                    result.status = "NULL"
                else:
                    result.status = str(status)
            else:
                result.status = "ERROR"
                result.error = error
            
            return result
        
        with ThreadPoolExecutor(max_workers=self.max_threads) as executor:
            futures = {
                executor.submit(check_single, did, i): did 
                for i, did in enumerate(device_ids)
            }
            
            for future in as_completed(futures):
                if self._stop_flag.is_set():
                    break
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    deviceid = futures[future]
                    results.append(MaintenanceStatusResult(
                        deviceid=deviceid,
                        status="ERROR",
                        error=str(e)
                    ))
        
        # Ordina per deviceid
        results.sort(key=lambda r: r.deviceid)
        return results
    
    def check_command_queue(self, device_ids: List[str],
                            hours_back: int = 24,
                            progress_callback: Optional[Callable] = None) -> List[CommandQueueResult]:
        """
        Verifica la command queue per una lista di device.
        
        Args:
            device_ids: Lista di deviceid
            hours_back: Quante ore indietro cercare
            progress_callback: Callback (deviceid, index, total)
            
        Returns:
            Lista di CommandQueueResult
        """
        self.reset()
        results = []
        total = len(device_ids)
        
        start_date = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        
        def check_single(deviceid: str, index: int) -> CommandQueueResult:
            result = CommandQueueResult(deviceid=deviceid)
            
            if self._stop_flag.is_set():
                result.error = "INTERRUPTED"
                return result
            
            if progress_callback:
                progress_callback(deviceid, index, total)
            
            success, data, error = self.api_client.get_commands_log(deviceid, start_date)
            
            if success:
                # Pending commands
                pending = data.get("pendingCommands", [])
                result.pending_commands = [self._format_command(cmd) for cmd in pending]
                
                # Sent commands (più recenti prima)
                sent = data.get("sentCommands", [])
                result.sent_commands = [self._format_command(cmd) for cmd in sent]
            else:
                result.error = error
            
            return result
        
        with ThreadPoolExecutor(max_workers=self.max_threads) as executor:
            futures = {
                executor.submit(check_single, did, i): did 
                for i, did in enumerate(device_ids)
            }
            
            for future in as_completed(futures):
                if self._stop_flag.is_set():
                    break
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    deviceid = futures[future]
                    results.append(CommandQueueResult(
                        deviceid=deviceid,
                        error=str(e)
                    ))
        
        # Ordina per deviceid
        results.sort(key=lambda r: r.deviceid)
        return results
    
    @staticmethod
    def export_maintenance_status(results: List[MaintenanceStatusResult], 
                                   output_path: str) -> tuple[bool, str]:
        """Esporta i risultati del check status in Excel"""
        try:
            data = [r.to_dict() for r in results]
            df = pd.DataFrame(data)
            
            # Rinomina colonne per chiarezza
            df.columns = ["DeviceID", "Maintenance Status", "Error"]
            
            with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False, sheet_name='Maintenance Status')
                
                workbook = writer.book
                worksheet = writer.sheets['Maintenance Status']
                
                # Formati
                header_format = workbook.add_format({
                    'bold': True, 'bg_color': '#0066CC', 'font_color': 'white',
                    'border': 1, 'align': 'center'
                })
                on_format = workbook.add_format({'bg_color': '#FFC7CE', 'font_color': '#9C0006'})
                off_format = workbook.add_format({'bg_color': '#C6EFCE', 'font_color': '#006100'})
                null_format = workbook.add_format({'bg_color': '#FFEB9C', 'font_color': '#9C6500'})
                
                # Header
                for col, value in enumerate(df.columns):
                    worksheet.write(0, col, value, header_format)
                
                # Larghezze
                worksheet.set_column(0, 0, 15)
                worksheet.set_column(1, 1, 20)
                worksheet.set_column(2, 2, 30)
                
                # Formattazione condizionale
                worksheet.conditional_format(1, 1, len(df), 1, {
                    'type': 'cell', 'criteria': '==', 'value': '"ON"', 'format': on_format
                })
                worksheet.conditional_format(1, 1, len(df), 1, {
                    'type': 'cell', 'criteria': '==', 'value': '"OFF"', 'format': off_format
                })
                worksheet.conditional_format(1, 1, len(df), 1, {
                    'type': 'cell', 'criteria': '==', 'value': '"NULL"', 'format': null_format
                })
                
                worksheet.autofilter(0, 0, len(df), len(df.columns) - 1)
                worksheet.freeze_panes(1, 0)
                
                # Riepilogo
                on_count = sum(1 for r in results if r.status == "ON")
                off_count = sum(1 for r in results if r.status == "OFF")
                null_count = sum(1 for r in results if r.status == "NULL")
                error_count = sum(1 for r in results if r.status == "ERROR")
                
                summary = pd.DataFrame({
                    "Stato": ["ON", "OFF", "NULL", "ERROR", "Totale"],
                    "Conteggio": [on_count, off_count, null_count, error_count, len(results)]
                })
                summary.to_excel(writer, index=False, sheet_name='Riepilogo')
                
                ws_summary = writer.sheets['Riepilogo']
                for col, value in enumerate(summary.columns):
                    ws_summary.write(0, col, value, header_format)
                ws_summary.set_column(0, 0, 15)
                ws_summary.set_column(1, 1, 15)
            
            return True, output_path
        except Exception as e:
            return False, str(e)
    
    @staticmethod
    def export_command_queue(results: List[CommandQueueResult],
                              output_path: str) -> tuple[bool, str]:
        """Esporta i risultati del check queue in Excel"""
        try:
            data = [r.to_dict() for r in results]
            df = pd.DataFrame(data)
            
            with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False, sheet_name='Command Queue')
                
                workbook = writer.book
                worksheet = writer.sheets['Command Queue']
                
                header_format = workbook.add_format({
                    'bold': True, 'bg_color': '#0066CC', 'font_color': 'white',
                    'border': 1, 'align': 'center'
                })
                pending_format = workbook.add_format({'bg_color': '#FFEB9C'})
                
                # Header
                for col, value in enumerate(df.columns):
                    worksheet.write(0, col, value, header_format)
                
                # Larghezze
                worksheet.set_column(0, 0, 15)  # deviceid
                worksheet.set_column(1, 5, 18)  # pending
                worksheet.set_column(6, 15, 18)  # sent
                worksheet.set_column(16, 16, 30)  # error
                
                # Colora celle pending che non sono N/A
                for row in range(1, len(df) + 1):
                    for col in range(1, 6):  # Colonne pending
                        cell_value = df.iloc[row-1, col] if col < len(df.columns) else "N/A"
                        if cell_value != "N/A":
                            worksheet.write(row, col, cell_value, pending_format)
                
                worksheet.autofilter(0, 0, len(df), len(df.columns) - 1)
                worksheet.freeze_panes(1, 0)
                
                # Riepilogo
                with_pending = sum(1 for r in results if len(r.pending_commands) > 0)
                with_sent = sum(1 for r in results if len(r.sent_commands) > 0)
                with_errors = sum(1 for r in results if r.error)
                
                summary = pd.DataFrame({
                    "Metrica": ["Con comandi pending", "Con comandi inviati", "Con errori", "Totale"],
                    "Conteggio": [with_pending, with_sent, with_errors, len(results)]
                })
                summary.to_excel(writer, index=False, sheet_name='Riepilogo')
                
                ws_summary = writer.sheets['Riepilogo']
                for col, value in enumerate(summary.columns):
                    ws_summary.write(0, col, value, header_format)
            
            return True, output_path
        except Exception as e:
            return False, str(e)


if __name__ == "__main__":
    print("Test QuickCheckWorker")
    print("=" * 50)
    
    worker = QuickCheckWorker()
    print(f"Max threads: {worker.max_threads}")
