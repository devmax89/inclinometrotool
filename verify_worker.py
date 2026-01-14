"""
DIGIL Reset Inclinometro - Verify Worker (Fase 2)
=================================================
Gestisce la verifica del reset inclinometro sui dispositivi DIGIL.
Controlla che l'allarme sia false e che i valori di inclinazione siano ~0.
"""

import threading
import time
import os
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, List, Callable, Dict
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

from api_client import get_api_client, get_token_manager

load_dotenv()


class VerifyStatus(Enum):
    """Stati possibili per la verifica"""
    PENDING = "In attesa"
    IN_PROGRESS = "In corso"
    VERIFIED = "Verificato"
    ALARM_ACTIVE = "Allarme attivo"
    INC_X_OUT_OF_RANGE = "Inc X fuori range"
    INC_Y_OUT_OF_RANGE = "Inc Y fuori range"
    TIMESTAMP_INVALID = "Dati vecchi"
    API_ERROR = "Errore API"
    PARTIAL = "Parziale"


@dataclass
class VerifyResult:
    """Risultato della verifica di un dispositivo"""
    deviceid: str
    tipo: str = "unknown"
    
    # Timestamp del reset originale (input)
    reset_timestamp: Optional[int] = None
    
    # Timestamp dei dati dal pacchetto API (quello di ALG_Digil2_Alm_Incl)
    data_timestamp: Optional[int] = None
    data_datetime: str = ""  # Human readable
    
    # Dati dall'API
    alarm_incl: Optional[bool] = None
    alarm_incl_timestamp: Optional[int] = None
    inc_x_avg: Optional[float] = None
    inc_x_timestamp: Optional[int] = None
    inc_y_avg: Optional[float] = None
    inc_y_timestamp: Optional[int] = None
    
    # Risultati check
    alarm_ok: bool = False
    inc_x_ok: bool = False
    inc_y_ok: bool = False
    timestamp_valid: bool = False
    
    # Delta temporale (ms tra reset e dati)
    timestamp_delta_ms: Optional[int] = None
    timestamp_delta_readable: str = ""
    
    # Status complessivo
    status: VerifyStatus = VerifyStatus.PENDING
    all_ok: bool = False
    error_message: str = ""
    
    def to_dict(self) -> Dict:
        """Converte in dizionario per export"""
        return {
            "deviceid": self.deviceid,
            "tipo": self.tipo,
            "reset_timestamp": self.reset_timestamp,
            "data_timestamp": self.data_timestamp,
            "data_datetime": self.data_datetime,
            "alarm_incl": self.alarm_incl,
            "alarm_ok": "OK" if self.alarm_ok else "KO",
            "inc_x_avg": self.inc_x_avg,
            "inc_x_ok": "OK" if self.inc_x_ok else "KO",
            "inc_y_avg": self.inc_y_avg,
            "inc_y_ok": "OK" if self.inc_y_ok else "KO",
            "timestamp_valid": "OK" if self.timestamp_valid else "KO",
            "timestamp_delta_ms": self.timestamp_delta_ms,
            "timestamp_delta_readable": self.timestamp_delta_readable,
            "status": self.status.value,
            "all_ok": "OK" if self.all_ok else "KO",
            "error_message": self.error_message
        }


def ms_to_readable(ms: int) -> str:
    """Converte millisecondi in formato leggibile"""
    if ms is None:
        return ""
    
    seconds = abs(ms) // 1000
    sign = "+" if ms >= 0 else "-"
    
    if seconds < 60:
        return f"{sign}{seconds}s"
    elif seconds < 3600:
        minutes = seconds // 60
        secs = seconds % 60
        return f"{sign}{minutes}m {secs}s"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{sign}{hours}h {minutes}m"


class VerifyWorker:
    """
    Worker per verificare il reset dell'inclinometro.
    """
    
    def __init__(self):
        self.api_client = get_api_client()
        
        # Configurazione
        self.max_threads = int(os.getenv("MAX_THREADS", "87"))
        self.tolerance = float(os.getenv("INCL_TOLERANCE", "0.20"))
        
        # Stato
        self._stop_flag = threading.Event()
        self._results: List[VerifyResult] = []
        self._results_lock = threading.Lock()
        
        # Statistiche
        self.stats = {
            "total": 0,
            "completed": 0,
            "verified": 0,
            "failed": 0,
            "in_progress": 0
        }
        self._stats_lock = threading.Lock()
    
    def stop(self):
        """Ferma l'esecuzione"""
        self._stop_flag.set()
    
    def reset(self):
        """Reset per nuova esecuzione"""
        self._stop_flag.clear()
        self._results = []
        self.stats = {
            "total": 0,
            "completed": 0,
            "verified": 0,
            "failed": 0,
            "in_progress": 0
        }
    
    def _update_stats(self, field: str, delta: int = 1):
        """Aggiorna statistiche thread-safe"""
        with self._stats_lock:
            self.stats[field] += delta
    
    def _verify_single_device(self, deviceid: str, 
                               reset_timestamp: int,
                               tipo: str = "unknown",
                               progress_callback: Optional[Callable] = None) -> VerifyResult:
        """
        Verifica un singolo dispositivo.
        """
        result = VerifyResult(deviceid=deviceid)
        result.tipo = tipo
        result.reset_timestamp = reset_timestamp
        result.status = VerifyStatus.IN_PROGRESS
        
        self._update_stats("in_progress")
        
        if progress_callback:
            progress_callback(result, "Chiamata API...")
        
        # Chiama l'API per verificare
        verify_data = self.api_client.verify_inclinometer_reset(
            deviceid,
            reset_timestamp,
            self.tolerance
        )
        
        # Dati dall'API
        result.alarm_incl = verify_data.get("alarm_incl")
        result.alarm_incl_timestamp = verify_data.get("alarm_incl_timestamp")
        result.inc_x_avg = verify_data.get("inc_x_avg")
        result.inc_x_timestamp = verify_data.get("inc_x_timestamp")
        result.inc_y_avg = verify_data.get("inc_y_avg")
        result.inc_y_timestamp = verify_data.get("inc_y_timestamp")
        
        # Usa il timestamp di ALG_Digil2_Alm_Incl come data_timestamp
        # (in teoria i 3 timestamp dovrebbero essere uguali)
        result.data_timestamp = result.alarm_incl_timestamp
        if result.data_timestamp:
            result.data_datetime = datetime.fromtimestamp(
                result.data_timestamp / 1000
            ).strftime("%Y-%m-%d %H:%M:%S")
        
        # Risultati check
        result.alarm_ok = verify_data.get("alarm_ok", False)
        result.inc_x_ok = verify_data.get("inc_x_ok", False)
        result.inc_y_ok = verify_data.get("inc_y_ok", False)
        result.timestamp_valid = verify_data.get("timestamp_valid", False)
        result.all_ok = verify_data.get("all_ok", False)
        
        # Delta temporale
        result.timestamp_delta_ms = verify_data.get("timestamp_delta_ms")
        if result.timestamp_delta_ms is not None:
            result.timestamp_delta_readable = ms_to_readable(result.timestamp_delta_ms)
        
        # Errore API
        if verify_data.get("error"):
            result.error_message = verify_data["error"]
            result.status = VerifyStatus.API_ERROR
            self._update_stats("failed")
        elif result.all_ok:
            result.status = VerifyStatus.VERIFIED
            self._update_stats("verified")
        else:
            # Determina il motivo del fallimento
            issues = []
            if not result.alarm_ok:
                issues.append("Allarme attivo")
            if not result.inc_x_ok:
                issues.append(f"Inc X={result.inc_x_avg:.3f}")
            if not result.inc_y_ok:
                issues.append(f"Inc Y={result.inc_y_avg:.3f}")
            if not result.timestamp_valid:
                issues.append("Dati vecchi")
            
            result.error_message = "; ".join(issues)
            
            # Status più specifico
            if not result.alarm_ok:
                result.status = VerifyStatus.ALARM_ACTIVE
            elif not result.timestamp_valid:
                result.status = VerifyStatus.TIMESTAMP_INVALID
            elif not result.inc_x_ok:
                result.status = VerifyStatus.INC_X_OUT_OF_RANGE
            elif not result.inc_y_ok:
                result.status = VerifyStatus.INC_Y_OUT_OF_RANGE
            else:
                result.status = VerifyStatus.PARTIAL
            
            self._update_stats("failed")
        
        self._update_stats("in_progress", -1)
        self._update_stats("completed")
        
        if progress_callback:
            status_text = "✓ Verificato" if result.all_ok else f"✗ {result.error_message}"
            progress_callback(result, status_text)
        
        return result
    
    def run(self, devices_to_verify: List[Dict],
            progress_callback: Optional[Callable] = None,
            completion_callback: Optional[Callable] = None,
            device_complete_callback: Optional[Callable] = None) -> List[VerifyResult]:
        """
        Esegue la verifica su tutti i dispositivi.
        
        Args:
            devices_to_verify: Lista di dict con {deviceid, reset_timestamp, tipo}
            progress_callback: Callback per aggiornamenti (result, message)
            completion_callback: Callback al completamento (results)
            device_complete_callback: Callback quando un device è completato (result)
            
        Returns:
            Lista di VerifyResult
        """
        self.reset()
        self.stats["total"] = len(devices_to_verify)
        
        # Valida autenticazione
        tm = get_token_manager()
        success, msg = tm.validate_config()
        if not success:
            # Tutti falliti per errore auth
            for device in devices_to_verify:
                result = VerifyResult(deviceid=device["deviceid"])
                result.tipo = device.get("tipo", "unknown")
                result.reset_timestamp = device.get("reset_timestamp")
                result.status = VerifyStatus.API_ERROR
                result.error_message = f"Auth error: {msg}"
                self._results.append(result)
            
            if completion_callback:
                completion_callback(self._results)
            return self._results
        
        # Esegui in parallelo
        with ThreadPoolExecutor(max_workers=self.max_threads) as executor:
            futures = {}
            for device in devices_to_verify:
                future = executor.submit(
                    self._verify_single_device,
                    device["deviceid"],
                    device["reset_timestamp"],
                    device.get("tipo", "unknown"),
                    progress_callback
                )
                futures[future] = device["deviceid"]
            
            for future in as_completed(futures):
                if self._stop_flag.is_set():
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                
                try:
                    result = future.result()
                    with self._results_lock:
                        self._results.append(result)
                    
                    if device_complete_callback:
                        device_complete_callback(result)
                        
                except Exception as e:
                    deviceid = futures[future]
                    result = VerifyResult(deviceid=deviceid)
                    result.status = VerifyStatus.API_ERROR
                    result.error_message = str(e)
                    
                    with self._results_lock:
                        self._results.append(result)
                    
                    if device_complete_callback:
                        device_complete_callback(result)
        
        if completion_callback:
            completion_callback(self._results)
        
        return self._results
    
    def get_results(self) -> List[VerifyResult]:
        """Restituisce i risultati correnti"""
        with self._results_lock:
            return list(self._results)
    
    def get_stats(self) -> Dict:
        """Restituisce le statistiche correnti"""
        with self._stats_lock:
            return dict(self.stats)


if __name__ == "__main__":
    # Test
    print("Test VerifyWorker")
    print("=" * 50)
    
    worker = VerifyWorker()
    print(f"Max threads: {worker.max_threads}")
    print(f"Tolerance: {worker.tolerance}")
    
    # Test ms_to_readable
    test_ms = [1000, 65000, 3661000, -5000]
    for ms in test_ms:
        print(f"{ms}ms -> {ms_to_readable(ms)}")