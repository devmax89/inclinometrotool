"""
DIGIL Reset Inclinometro - Reset Worker (Fase 1)
================================================
Gestisce l'esecuzione del reset inclinometro sui dispositivi DIGIL.
"""

import threading
import time
import os
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Callable, Dict
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

from api_client import get_api_client, get_token_manager

load_dotenv()


class ResetStatus(Enum):
    """Stati possibili per il reset"""
    PENDING = "In attesa"
    IN_PROGRESS = "In corso"
    OK = "OK"
    FAILED = "Fallito"
    SKIPPED = "Skipped"
    ERROR = "Errore"


@dataclass
class ResetResult:
    """Risultato del reset di un dispositivo"""
    deviceid: str
    tipo: str = "unknown"
    
    # Stati delle 3 fasi
    manutenzione_on: str = "NON ESEGUITO"
    reset_inclinometro: str = "NON ESEGUITO"
    manutenzione_off: str = "NON ESEGUITO"
    
    # Timestamp del reset (in millisecondi, stesso formato API)
    reset_timestamp: Optional[int] = None
    
    # Timestamp human-readable
    reset_datetime: str = ""
    
    # Errori
    error_message: str = ""
    
    # Status complessivo
    status: ResetStatus = ResetStatus.PENDING
    
    def to_dict(self) -> Dict:
        """Converte in dizionario per export"""
        return {
            "deviceid": self.deviceid,
            "tipo": self.tipo,
            "manutenzione_on": self.manutenzione_on,
            "reset_inclinometro": self.reset_inclinometro,
            "manutenzione_off": self.manutenzione_off,
            "reset_timestamp": self.reset_timestamp,
            "reset_datetime": self.reset_datetime,
            "error_message": self.error_message
        }


def detect_device_type(deviceid: str) -> str:
    """
    Rileva automaticamente il tipo di device dal deviceid.
    
    Pattern:
    - 1121525_xxxx → Master (contiene "15" nella parte centrale)
    - 1121621_xxxx → Slave (contiene "16" nella parte centrale)
    """
    deviceid_str = str(deviceid)
    
    # Controlla se contiene "15" (master) o "16" (slave) nella posizione 4-5
    if len(deviceid_str) >= 6:
        if "15" in deviceid_str[3:6]:
            return "master"
        elif "16" in deviceid_str[3:6]:
            return "slave"
    
    # Fallback: cerca ovunque nel deviceid
    if "15" in deviceid_str and "16" not in deviceid_str:
        return "master"
    elif "16" in deviceid_str:
        return "slave"
    
    # Default a slave (più conservativo, timeout più lungo)
    return "slave"


class ResetWorker:
    """
    Worker per eseguire il reset dell'inclinometro su più dispositivi.
    """
    
    def __init__(self):
        self.api_client = get_api_client()
        
        # Configurazione da .env
        self.max_threads = int(os.getenv("MAX_THREADS", "87"))
        self.master_timeout = int(os.getenv("MAX_RETRY_MINUTES_MASTER", "10"))
        self.slave_timeout = int(os.getenv("MAX_RETRY_MINUTES_SLAVE", "20"))
        
        # Stato
        self._stop_flag = threading.Event()
        self._results: List[ResetResult] = []
        self._results_lock = threading.Lock()
        
        # Statistiche
        self.stats = {
            "total": 0,
            "completed": 0,
            "success": 0,
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
            "success": 0,
            "failed": 0,
            "in_progress": 0
        }
    
    def _update_stats(self, field: str, delta: int = 1):
        """Aggiorna statistiche thread-safe"""
        with self._stats_lock:
            self.stats[field] += delta
    
    def _process_single_device(self, deviceid: str,
                                progress_callback: Optional[Callable] = None) -> ResetResult:
        """
        Processa un singolo dispositivo.
        
        Fasi:
        1. Manutenzione ON
        2. Reset inclinometro (set_value)
        3. Manutenzione OFF
        """
        result = ResetResult(deviceid=deviceid)
        result.tipo = detect_device_type(deviceid)
        result.status = ResetStatus.IN_PROGRESS
        
        # Timeout basato sul tipo
        max_minutes = self.master_timeout if result.tipo == "master" else self.slave_timeout
        
        self._update_stats("in_progress")
        
        def local_progress(did, msg, attempt):
            if progress_callback:
                progress_callback(result, msg)
        
        # === FASE 1: Manutenzione ON ===
        if progress_callback:
            progress_callback(result, "Manutenzione ON...")
        
        status, attempts, _ = self.api_client.send_command(
            deviceid,
            {"name": "maintenance", "params": {"status": {"values": ["ON"]}}},
            max_minutes=max_minutes,
            progress_callback=local_progress
        )
        result.manutenzione_on = status
        
        if status != "OK":
            result.reset_inclinometro = "SKIPPED"
            result.manutenzione_off = "SKIPPED"
            result.error_message = f"Manutenzione ON fallita: {status}"
            result.status = ResetStatus.FAILED
            self._update_stats("in_progress", -1)
            self._update_stats("failed")
            self._update_stats("completed")
            return result
        
        # === FASE 2: Reset Inclinometro ===
        if self._stop_flag.is_set():
            result.reset_inclinometro = "INTERROTTO"
            result.manutenzione_off = "SKIPPED"
            result.status = ResetStatus.FAILED
            self._update_stats("in_progress", -1)
            self._update_stats("failed")
            self._update_stats("completed")
            return result
        
        if progress_callback:
            progress_callback(result, "Reset inclinometro...")
        
        status, attempts, timestamp = self.api_client.send_command(
            deviceid,
            {
                "name": "set_value",
                "params": {
                    "peripheral": {"values": ["sjb"]},
                    "param": {"values": ["COM_Digil2_Conf_Incl_Taratura"]},
                    "value": {"values": ["1"]}
                }
            },
            max_minutes=max_minutes,
            progress_callback=local_progress
        )
        result.reset_inclinometro = status
        
        # Se OK, salva il timestamp
        if status == "OK" and timestamp:
            result.reset_timestamp = timestamp
            result.reset_datetime = datetime.fromtimestamp(timestamp / 1000).strftime("%Y-%m-%d %H:%M:%S")
        
        # === FASE 3: Manutenzione OFF (sempre tentata) ===
        if progress_callback:
            progress_callback(result, "Manutenzione OFF...")
        
        status, attempts, _ = self.api_client.send_command(
            deviceid,
            {"name": "maintenance", "params": {"status": {"values": ["OFF"]}}},
            max_minutes=max_minutes,
            progress_callback=local_progress
        )
        result.manutenzione_off = status
        
        # Determina status finale
        if result.reset_inclinometro == "OK":
            result.status = ResetStatus.OK
            self._update_stats("success")
        else:
            result.status = ResetStatus.FAILED
            result.error_message = f"Reset fallito: {result.reset_inclinometro}"
            self._update_stats("failed")
        
        self._update_stats("in_progress", -1)
        self._update_stats("completed")
        
        if progress_callback:
            status_text = "✓ Completato" if result.status == ResetStatus.OK else f"✗ {result.error_message}"
            progress_callback(result, status_text)
        
        return result
    
    def run(self, device_ids: List[str],
            progress_callback: Optional[Callable] = None,
            completion_callback: Optional[Callable] = None,
            device_complete_callback: Optional[Callable] = None) -> List[ResetResult]:
        """
        Esegue il reset su tutti i dispositivi.
        
        Args:
            device_ids: Lista di deviceid da processare
            progress_callback: Callback per aggiornamenti (result, message)
            completion_callback: Callback al completamento (results)
            device_complete_callback: Callback quando un device è completato (result)
            
        Returns:
            Lista di ResetResult
        """
        self.reset()
        self.stats["total"] = len(device_ids)
        
        # Valida autenticazione
        tm = get_token_manager()
        success, msg = tm.validate_config()
        if not success:
            # Tutti falliti per errore auth
            for did in device_ids:
                result = ResetResult(deviceid=did)
                result.tipo = detect_device_type(did)
                result.status = ResetStatus.ERROR
                result.error_message = f"Auth error: {msg}"
                result.manutenzione_on = "AUTH_ERROR"
                result.reset_inclinometro = "SKIPPED"
                result.manutenzione_off = "SKIPPED"
                self._results.append(result)
            
            if completion_callback:
                completion_callback(self._results)
            return self._results
        
        # Esegui in parallelo
        with ThreadPoolExecutor(max_workers=self.max_threads) as executor:
            futures = {
                executor.submit(
                    self._process_single_device, 
                    did, 
                    progress_callback
                ): did for did in device_ids
            }
            
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
                    result = ResetResult(deviceid=deviceid)
                    result.tipo = detect_device_type(deviceid)
                    result.status = ResetStatus.ERROR
                    result.error_message = str(e)
                    
                    with self._results_lock:
                        self._results.append(result)
                    
                    if device_complete_callback:
                        device_complete_callback(result)
        
        if completion_callback:
            completion_callback(self._results)
        
        return self._results
    
    def get_results(self) -> List[ResetResult]:
        """Restituisce i risultati correnti"""
        with self._results_lock:
            return list(self._results)
    
    def get_ok_results(self) -> List[ResetResult]:
        """Restituisce solo i risultati con reset OK (per Fase 2)"""
        with self._results_lock:
            return [r for r in self._results if r.reset_inclinometro == "OK"]
    
    def get_stats(self) -> Dict:
        """Restituisce le statistiche correnti"""
        with self._stats_lock:
            return dict(self.stats)


if __name__ == "__main__":
    # Test
    print("Test ResetWorker")
    print("=" * 50)
    
    worker = ResetWorker()
    print(f"Max threads: {worker.max_threads}")
    print(f"Master timeout: {worker.master_timeout} min")
    print(f"Slave timeout: {worker.slave_timeout} min")
    
    # Test detect_device_type
    test_ids = ["1121621_0436", "1121525_0103", "1121622_0399"]
    for did in test_ids:
        print(f"{did} -> {detect_device_type(did)}")
