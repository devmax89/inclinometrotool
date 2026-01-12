"""
DIGIL Reset Inclinometro - Data Handler Module
==============================================
Gestisce il caricamento dei dati input e l'esportazione dei risultati in Excel.
"""

import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import List, Tuple, Optional, Dict
import os

from reset_worker import ResetResult, ResetStatus, detect_device_type
from verify_worker import VerifyResult, VerifyStatus


class InputLoader:
    """Carica i device da testare da file Excel"""
    
    def __init__(self):
        self.file_path: Optional[Path] = None
        self._df: Optional[pd.DataFrame] = None
        self._device_ids: List[str] = []
    
    def load_file(self, file_path: str) -> Tuple[bool, str, int]:
        """
        Carica il file Excel di input.
        Supporta file CON o SENZA header.
        
        Args:
            file_path: Percorso del file Excel
            
        Returns:
            (success, message, device_count)
        """
        path = Path(file_path)
        
        if not path.exists():
            return False, f"File non trovato: {path}", 0
        
        try:
            # Prima prova a leggere con header
            df_with_header = pd.read_excel(path, engine='openpyxl')
            
            # Cerca la colonna deviceid (case-insensitive, varie possibilità)
            deviceid_col = None
            possible_names = ['deviceid', 'device_id', 'clientid', 'client_id', 'id', 'device']
            
            for col in df_with_header.columns:
                col_lower = str(col).lower().strip()
                if col_lower in possible_names:
                    deviceid_col = col
                    break
            
            if deviceid_col is not None:
                # File CON header riconosciuto - usa la colonna trovata
                self._df = df_with_header
                self._device_ids = self._df[deviceid_col].dropna().astype(str).tolist()
                col_info = f"colonna: {deviceid_col}"
            else:
                # Controlla se la prima "colonna" (header) sembra un deviceid
                first_col_name = str(df_with_header.columns[0])
                
                if self._looks_like_deviceid(first_col_name):
                    # File SENZA header - la prima riga è già un deviceid!
                    # Rileggi senza header
                    self._df = pd.read_excel(path, engine='openpyxl', header=None)
                    self._device_ids = self._df[0].dropna().astype(str).tolist()
                    col_info = "senza header"
                else:
                    # File con header ma colonna non riconosciuta - usa prima colonna
                    self._df = df_with_header
                    self._device_ids = self._df[first_col_name].dropna().astype(str).tolist()
                    col_info = f"colonna: {first_col_name}"
            
            # Pulisci i device ID
            self._device_ids = [
                did.strip() for did in self._device_ids 
                if did.strip() and did.strip().lower() != 'nan'
            ]
            
            self.file_path = path
            return True, f"Caricati {len(self._device_ids)} dispositivi ({col_info})", len(self._device_ids)
            
        except Exception as e:
            return False, f"Errore lettura file: {str(e)}", 0
    
    def _looks_like_deviceid(self, value: str) -> bool:
        """
        Verifica se un valore sembra un deviceid DIGIL.
        Pattern tipici: 1121621_0884, 1121525_0103, etc.
        """
        import re
        value_str = str(value).strip()
        # Pattern: 7 cifre + underscore + 4 cifre (es: 1121621_0884)
        pattern = r'^\d{7}_\d{4}$'
        return bool(re.match(pattern, value_str))
    
    def get_device_ids(self) -> List[str]:
        """Restituisce la lista dei device ID"""
        return self._device_ids
    
    def get_summary(self) -> Dict:
        """Restituisce un riepilogo dei dati caricati"""
        if not self._device_ids:
            return {"loaded": False}
        
        # Conta master e slave
        master_count = sum(1 for did in self._device_ids if detect_device_type(did) == "master")
        slave_count = len(self._device_ids) - master_count
        
        return {
            "loaded": True,
            "file": str(self.file_path) if self.file_path else "",
            "total": len(self._device_ids),
            "master": master_count,
            "slave": slave_count
        }


class ResultExporter:
    """Esporta i risultati in file Excel formattati"""
    
    def __init__(self):
        self.output_dir = Path.home() / "Downloads"
        if not self.output_dir.exists():
            self.output_dir = Path.cwd()
    
    def export_reset_results(self, results: List[ResetResult], 
                             output_path: Optional[str] = None) -> Tuple[bool, str]:
        """
        Esporta i risultati della Fase 1 (Reset).
        
        Colonne output:
        - deviceid
        - tipo
        - manutenzione_on
        - reset_inclinometro
        - manutenzione_off
        - reset_timestamp (epoch ms)
        - reset_datetime (human readable)
        """
        if not results:
            return False, "Nessun risultato da esportare"
        
        if not output_path:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = self.output_dir / f"Reset_Inclinometro_Fase1_{timestamp}.xlsx"
        
        try:
            # Prepara i dati
            data = []
            for r in results:
                data.append({
                    "deviceid": r.deviceid,
                    "tipo": r.tipo,
                    "manutenzione_on": r.manutenzione_on,
                    "reset_inclinometro": r.reset_inclinometro,
                    "manutenzione_off": r.manutenzione_off,
                    "reset_timestamp": r.reset_timestamp if r.reset_timestamp else "",
                    "reset_datetime": r.reset_datetime
                })
            
            df = pd.DataFrame(data)
            
            # Scrivi con formattazione
            with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False, sheet_name='Reset Results')
                
                workbook = writer.book
                worksheet = writer.sheets['Reset Results']
                
                # Formati
                header_format = workbook.add_format({
                    'bold': True,
                    'bg_color': '#0066CC',
                    'font_color': 'white',
                    'border': 1,
                    'align': 'center'
                })
                
                ok_format = workbook.add_format({
                    'bg_color': '#C6EFCE',
                    'font_color': '#006100',
                    'border': 1
                })
                
                ko_format = workbook.add_format({
                    'bg_color': '#FFC7CE',
                    'font_color': '#9C0006',
                    'border': 1
                })
                
                # Applica header
                for col_num, value in enumerate(df.columns):
                    worksheet.write(0, col_num, value, header_format)
                
                # Larghezze colonne
                worksheet.set_column(0, 0, 15)  # deviceid
                worksheet.set_column(1, 1, 8)   # tipo
                worksheet.set_column(2, 4, 15)  # manutenzione/reset
                worksheet.set_column(5, 5, 15)  # timestamp
                worksheet.set_column(6, 6, 20)  # datetime
                
                # Formattazione condizionale per reset_inclinometro (colonna D, index 3)
                worksheet.conditional_format(1, 3, len(df), 3, {
                    'type': 'cell',
                    'criteria': '==',
                    'value': '"OK"',
                    'format': ok_format
                })
                worksheet.conditional_format(1, 3, len(df), 3, {
                    'type': 'cell',
                    'criteria': '!=',
                    'value': '"OK"',
                    'format': ko_format
                })
                
                # Filtri e freeze
                worksheet.autofilter(0, 0, len(df), len(df.columns) - 1)
                worksheet.freeze_panes(1, 0)
                
                # === Sheet Riepilogo ===
                ok_count = sum(1 for r in results if r.reset_inclinometro == "OK")
                ko_count = len(results) - ok_count
                
                summary_data = {
                    "Metrica": [
                        "Totale dispositivi",
                        "Reset OK",
                        "Reset KO",
                        "Success Rate",
                        "Data Export"
                    ],
                    "Valore": [
                        len(results),
                        ok_count,
                        ko_count,
                        f"{(ok_count/len(results)*100):.1f}%" if results else "0%",
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    ]
                }
                
                df_summary = pd.DataFrame(summary_data)
                df_summary.to_excel(writer, index=False, sheet_name='Riepilogo')
                
                ws_summary = writer.sheets['Riepilogo']
                for col_num, value in enumerate(df_summary.columns):
                    ws_summary.write(0, col_num, value, header_format)
                ws_summary.set_column(0, 0, 20)
                ws_summary.set_column(1, 1, 25)
            
            return True, str(output_path)
            
        except Exception as e:
            return False, f"Errore export: {str(e)}"
    
    def export_verify_results(self, results: List[VerifyResult],
                               output_path: Optional[str] = None) -> Tuple[bool, str]:
        """
        Esporta i risultati della Fase 2 (Verifica).
        """
        if not results:
            return False, "Nessun risultato da esportare"
        
        if not output_path:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = self.output_dir / f"Reset_Inclinometro_Fase2_{timestamp}.xlsx"
        
        try:
            # Prepara i dati
            data = [r.to_dict() for r in results]
            df = pd.DataFrame(data)
            
            # Riordina colonne
            col_order = [
                "deviceid", "tipo", "all_ok",
                "alarm_incl", "alarm_ok",
                "inc_x_avg", "inc_x_ok",
                "inc_y_avg", "inc_y_ok",
                "timestamp_valid", "timestamp_delta_readable",
                "reset_datetime", "verify_datetime",
                "status", "error_message"
            ]
            df = df[[c for c in col_order if c in df.columns]]
            
            with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False, sheet_name='Verify Results')
                
                workbook = writer.book
                worksheet = writer.sheets['Verify Results']
                
                # Formati
                header_format = workbook.add_format({
                    'bold': True,
                    'bg_color': '#0066CC',
                    'font_color': 'white',
                    'border': 1,
                    'align': 'center'
                })
                
                ok_format = workbook.add_format({
                    'bg_color': '#C6EFCE',
                    'font_color': '#006100',
                    'border': 1
                })
                
                ko_format = workbook.add_format({
                    'bg_color': '#FFC7CE',
                    'font_color': '#9C0006',
                    'border': 1
                })
                
                # Applica header
                for col_num, value in enumerate(df.columns):
                    worksheet.write(0, col_num, value, header_format)
                
                # Larghezze
                worksheet.set_column(0, 0, 15)   # deviceid
                worksheet.set_column(1, 1, 8)    # tipo
                worksheet.set_column(2, 2, 8)    # all_ok
                worksheet.set_column(3, 10, 12)  # check columns
                worksheet.set_column(11, 12, 20) # datetimes
                worksheet.set_column(13, 13, 15) # status
                worksheet.set_column(14, 14, 30) # error
                
                # Formattazione condizionale per all_ok (colonna C, index 2)
                worksheet.conditional_format(1, 2, len(df), 2, {
                    'type': 'cell',
                    'criteria': '==',
                    'value': '"OK"',
                    'format': ok_format
                })
                worksheet.conditional_format(1, 2, len(df), 2, {
                    'type': 'cell',
                    'criteria': '!=',
                    'value': '"OK"',
                    'format': ko_format
                })
                
                worksheet.autofilter(0, 0, len(df), len(df.columns) - 1)
                worksheet.freeze_panes(1, 0)
                
                # === Sheet Riepilogo ===
                verified_count = sum(1 for r in results if r.all_ok)
                
                summary_data = {
                    "Metrica": [
                        "Totale verificati",
                        "Tutti OK",
                        "Con problemi",
                        "Allarme attivo",
                        "Inc X fuori range",
                        "Inc Y fuori range",
                        "Timestamp invalido",
                        "Errori API",
                        "Data Export"
                    ],
                    "Valore": [
                        len(results),
                        verified_count,
                        len(results) - verified_count,
                        sum(1 for r in results if not r.alarm_ok and r.alarm_incl is not None),
                        sum(1 for r in results if not r.inc_x_ok and r.inc_x_avg is not None),
                        sum(1 for r in results if not r.inc_y_ok and r.inc_y_avg is not None),
                        sum(1 for r in results if not r.timestamp_valid),
                        sum(1 for r in results if r.status == VerifyStatus.API_ERROR),
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    ]
                }
                
                df_summary = pd.DataFrame(summary_data)
                df_summary.to_excel(writer, index=False, sheet_name='Riepilogo')
                
                ws_summary = writer.sheets['Riepilogo']
                for col_num, value in enumerate(df_summary.columns):
                    ws_summary.write(0, col_num, value, header_format)
                ws_summary.set_column(0, 0, 25)
                ws_summary.set_column(1, 1, 20)
            
            return True, str(output_path)
            
        except Exception as e:
            return False, f"Errore export: {str(e)}"


if __name__ == "__main__":
    print("Test Data Handler")
    print("=" * 50)
    
    loader = InputLoader()
    print(f"Output dir: {ResultExporter().output_dir}")