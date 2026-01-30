"""
DIGIL Reset Inclinometro - Reset Worker (Fase 1) v2.0
=====================================================
Gestisce l'esecuzione del reset inclinometro sui dispositivi DIGIL
con verifica tramite commands-log API.

Logica:
1. Maintenance ON → verifica command-log → verifica configuration
2. Reset inclinometro → verifica command-log
3. Maintenance OFF → verifica command-log → verifica configuration
"""

import threading
import time
import os
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional, List, Callable, Dict, Any
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

from api_client import get_api_client, get_token_manager

load_dotenv()


class ResetPhase(Enum):
    """Fasi del processo di reset"""
    IDLE = "idle"
    MAINTENANCE_ON_SENDING = "maint_on_sending"
    MAINTENANCE_ON_CHECKING_LOG = "maint_on_checking_log"
    MAINTENANCE_ON_VERIFYING = "maint_on_verifying"
    RESET_SENDING = "reset_sending"
    RESET_CHECKING_LOG = "reset_checking_log"
    MAINTENANCE_OFF_SENDING = "maint_off_sending"
    MAINTENANCE_OFF_CHECKING_LOG = "maint_off_checking_log"
    MAINTENANCE_OFF_VERIFYING = "maint_off_verifying"
    COMPLETED = "completed"
    ERROR = "error"


class ResetStatus(Enum):
    """Stati possibili per il reset"""
    PENDING = "In attesa"
    IN_PROGRESS = "In corso"
    MAINT_ON = "Maint ON..."
    RESET_CMD = "Reset..."
    MAINT_OFF = "Maint OFF..."
    OK = "OK"
    FAILED = "Fallito"
    ERROR = "Errore"
    INTERRUPTED = "Interrotto"


class MaintenanceState(Enum):
    """Stati della manutenzione"""
    ON = "ON"
    OFF = "OFF"
    UNKNOWN = "UNKNOWN"  # null o non letto


@dataclass
class ResetResult:
    """Risultato del reset di un dispositivo"""
    deviceid: str
    tipo: str = "unknown"
    
    # Fase corrente
    current_phase: ResetPhase = ResetPhase.IDLE
    
    # Stati delle 3 fasi
    manutenzione_on: str = "NON ESEGUITO"
    reset_inclinometro: str = "NON ESEGUITO"
    manutenzione_off: str = "NON ESEGUITO"
    
    # Stato maintenance dal configuration
    maintenance_state: MaintenanceState = MaintenanceState.UNKNOWN
    
    # Timestamp del reset (in millisecondi, stesso formato API)
    reset_timestamp: Optional[int] = None
    
    # Timestamp human-readable
    reset_datetime: str = ""
    
    # Contatori tentativi
    maint_on_attempts: int = 0
    reset_attempts: int = 0
    maint_off_attempts: int = 0
    
    # Log dettagliato delle operazioni
    operation_log: List[str] = field(default_factory=list)
    
    # Errore complessivo
    error_message: str = ""
    
    # Status complessivo
    status: ResetStatus = ResetStatus.PENDING
    
    # Flag per indicare se ha maintenance pending (per cleanup alla chiusura)
    has_maintenance_on_pending: bool = False
    
    def to_dict(self) -> Dict:
        """Converte in dizionario per export"""
        return {
            "deviceid": self.deviceid,
            "tipo": self.tipo,
            "manutenzione_on": self.manutenzione_on,
            "reset_inclinometro": self.reset_inclinometro,
            "manutenzione_off": self.manutenzione_off,
            "maintenance_state": self.maintenance_state.value,
            "reset_timestamp": self.reset_timestamp,
            "reset_datetime": self.reset_datetime,
            "maint_on_attempts": self.maint_on_attempts,
            "reset_attempts": self.reset_attempts,
            "maint_off_attempts": self.maint_off_attempts,
            "error_message": self.error_message
        }
    
    def add_log(self, message: str):
        """Aggiunge un messaggio al log con timestamp"""
        ts = datetime.now().strftime("%H:%M:%S")
        self.operation_log.append(f"[{ts}] {message}")


def detect_device_type(deviceid: str) -> str:
    """
    Rileva automaticamente il tipo di device dal deviceid.
    
    Pattern:
    - 1121525_xxxx → Master
    - 1121621_xxxx → Slave
    """
    deviceid_str = str(deviceid)
    
    if len(deviceid_str) >= 6:
        if "15" in deviceid_str[3:6]:
            return "master"
        elif "16" in deviceid_str[3:6]:
            return "slave"
    
    if "15" in deviceid_str and "16" not in deviceid_str:
        return "master"
    elif "16" in deviceid_str:
        return "slave"
    
    return "slave"


class ResetWorker:
    """
    Worker per eseguire il reset dell'inclinometro su più dispositivi.
    Utilizza verifica tramite commands-log API.
    """
    
    # Payloads dei comandi
    MAINTENANCE_ON_PAYLOAD = {"name": "maintenance", "params": {"status": {"values": ["ON"]}}}
    MAINTENANCE_OFF_PAYLOAD = {"name": "maintenance", "params": {"status": {"values": ["OFF"]}}}
    RESET_INCL_PAYLOAD = {
        "name": "set_value",
        "params": {
            "peripheral": {"values": ["sjb"]},
            "param": {"values": ["COM_Digil2_Conf_Incl_Taratura"]},
            "value": {"values": ["1"]}
        }
    }
    
    # Match per commands-log
    MAINT_ON_MATCH = {"status": "ON"}
    MAINT_OFF_MATCH = {"status": "OFF"}
    RESET_INCL_MATCH = {"param": "COM_Digil2_Conf_Incl_Taratura", "value": "1"}
    
    def __init__(self):
        self.api_client = get_api_client()
        
        # Configurazione
        self.max_threads = int(os.getenv("MAX_THREADS", "25"))
        self.check_interval = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))
        
        # Stato
        self._stop_flag = threading.Event()
        self._results: Dict[str, ResetResult] = {}  # deviceid -> result
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
        
        # Callback per log globale
        self._log_callback: Optional[Callable] = None
    
    def set_log_callback(self, callback: Callable):
        """Imposta il callback per il log globale"""
        self._log_callback = callback
    
    def _global_log(self, message: str, level: str = "INFO"):
        """Log globale"""
        if self._log_callback:
            self._log_callback(message, level)
    
    def stop(self):
        """Ferma l'esecuzione"""
        self._stop_flag.set()
    
    def is_stopped(self) -> bool:
        """Verifica se è stato richiesto lo stop"""
        return self._stop_flag.is_set()
    
    def reset(self):
        """Reset per nuova esecuzione"""
        self._stop_flag.clear()
        with self._results_lock:
            self._results = {}
        with self._stats_lock:
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
    
    def get_devices_with_maintenance_on(self) -> List[str]:
        """Restituisce i deviceid che hanno maintenance ON pending"""
        with self._results_lock:
            return [
                deviceid for deviceid, result in self._results.items()
                if result.has_maintenance_on_pending and result.status != ResetStatus.OK
            ]
    
    def send_maintenance_off_to_pending(self, 
                                         progress_callback: Optional[Callable] = None) -> Dict[str, bool]:
        """
        Invia maintenance OFF a tutti i device con maintenance ON pending.
        Usato alla chiusura dell'applicazione.
        
        Returns:
            Dict[deviceid, success]
        """
        devices = self.get_devices_with_maintenance_on()
        results = {}
        
        for deviceid in devices:
            if self._stop_flag.is_set():
                break
            
            if progress_callback:
                progress_callback(deviceid, "Invio maintenance OFF di cleanup...")
            
            success, error, _ = self.api_client.send_command(deviceid, self.MAINTENANCE_OFF_PAYLOAD)
            results[deviceid] = success
            
            self._global_log(
                f"{deviceid}: Cleanup maintenance OFF - {'OK' if success else error}",
                "OK" if success else "WARN"
            )
        
        return results
    
    def _process_single_device(self, deviceid: str,
                                progress_callback: Optional[Callable] = None) -> ResetResult:
        """
        Processa un singolo dispositivo con la logica completa.
        """
        result = ResetResult(deviceid=deviceid)
        result.tipo = detect_device_type(deviceid)
        result.status = ResetStatus.IN_PROGRESS
        
        with self._results_lock:
            self._results[deviceid] = result
        
        self._update_stats("in_progress")
        
        def update_progress(message: str):
            if progress_callback:
                progress_callback(result, message)
        
        def log_and_update(message: str):
            result.add_log(message)
            update_progress(message)
            self._global_log(f"{deviceid}: {message}")
        
        # ===== CHECK INIZIALE: VERIFICA SE GIA' IN MAINTENANCE =====
        result.current_phase = ResetPhase.MAINTENANCE_ON_VERIFYING
        log_and_update(f"[PRE-CHECK] Verifica stato maintenance attuale...")
        
        success, maint_status, error = self.api_client.get_maintenance_status(deviceid)
        if success:
            log_and_update(f"[PRE-CHECK] maintenanceMode = '{maint_status}'")
            if maint_status == "ON":
                result.maintenance_state = MaintenanceState.ON
                result.manutenzione_on = "GIA' ON"
                result.has_maintenance_on_pending = True
                log_and_update(f"[PRE-CHECK] ✓ Device già in maintenance ON, salto fase 1")
                # Salta direttamente alla fase RESET
            else:
                result.maintenance_state = MaintenanceState.OFF if maint_status == "OFF" else MaintenanceState.UNKNOWN
        else:
            log_and_update(f"[PRE-CHECK] Errore lettura: {error}, procedo normalmente")
        
        # ===== FASE 1: MAINTENANCE ON (solo se non già ON) =====
        if result.manutenzione_on != "GIA' ON":
            result.current_phase = ResetPhase.MAINTENANCE_ON_SENDING
            result.status = ResetStatus.MAINT_ON
            
            maint_on_done = False
            while not self._stop_flag.is_set() and not maint_on_done:
                result.maint_on_attempts += 1
                log_and_update(f"[MAINT ON] Tentativo {result.maint_on_attempts} - Invio comando...")
                
                # 1. Invia comando maintenance ON
                success, error, sent_time = self.api_client.send_command(
                    deviceid, self.MAINTENANCE_ON_PAYLOAD
                )
                
                if not success:
                    log_and_update(f"[MAINT ON] Errore invio: {error}. Riprovo tra 5s...")
                    time.sleep(5)
                    continue
                
                log_and_update(f"[MAINT ON] Comando inviato alle {sent_time.strftime('%H:%M:%S')}, attendo {self.check_interval}s...")
                result.has_maintenance_on_pending = True  # FLAG SETTATO QUI
                
                # 2. Attendi e verifica nel commands-log
                result.current_phase = ResetPhase.MAINTENANCE_ON_CHECKING_LOG
                time.sleep(self.check_interval)
                
                if self._stop_flag.is_set():
                    break
                
                # Ciclo di verifica commands-log
                check_attempts = 0
                while not self._stop_flag.is_set():
                    check_attempts += 1
                    log_and_update(f"[MAINT ON] Verifica commands-log (check #{check_attempts})...")
                    
                    cmd_status = self.api_client.check_command_in_log(
                        deviceid, "maintenance", self.MAINT_ON_MATCH, sent_time
                    )
                    
                    if cmd_status["error"]:
                        log_and_update(f"[MAINT ON] Errore check log: {cmd_status['error']}. Riprovo comando...")
                        break  # Riprova da capo (invio comando)
                    
                    log_and_update(f"[MAINT ON] Log status: {cmd_status['status']} ({cmd_status.get('debug_info', '')})")
                    
                    if cmd_status["status"] == "pending":
                        log_and_update(f"[MAINT ON] Comando in pending, attendo {self.check_interval}s...")
                        time.sleep(self.check_interval)
                        continue
                    
                    if cmd_status["status"] == "sent_ok":
                        log_and_update(f"[MAINT ON] Comando confermato (response={cmd_status['response_status']})")
                        
                        # 3. Verifica lo stato nel configuration
                        result.current_phase = ResetPhase.MAINTENANCE_ON_VERIFYING
                        log_and_update(f"[MAINT ON] Verifica configuration...")
                        
                        success, maint_status, error = self.api_client.get_maintenance_status(deviceid)
                        
                        if success:
                            log_and_update(f"[MAINT ON] Configuration maintenanceMode = '{maint_status}'")
                            if maint_status == "ON":
                                result.maintenance_state = MaintenanceState.ON
                                result.manutenzione_on = "OK"
                                log_and_update(f"[MAINT ON] ✓ Verificato: maintenanceMode=ON")
                                maint_on_done = True  # Esce da tutti i loop
                                break
                            else:
                                result.maintenance_state = MaintenanceState.OFF if maint_status == "OFF" else MaintenanceState.UNKNOWN
                                log_and_update(f"[MAINT ON] Configuration non ancora ON, riprovo...")
                        else:
                            log_and_update(f"[MAINT ON] Errore lettura configuration: {error}. Riprovo...")
                        
                        break  # Riprova da capo
                    
                    elif cmd_status["status"] in ["sent_error", "sent_no_response"]:
                        log_and_update(f"[MAINT ON] Comando fallito (status={cmd_status.get('response_status', 'N/A')}). Riprovo...")
                        break  # Riprova da capo
                    
                    else:  # not_found
                        log_and_update(f"[MAINT ON] Comando non trovato nel log. Debug: {cmd_status.get('debug_info', '')}. Riprovo...")
                        break  # Riprova da capo
        
        if self._stop_flag.is_set():
            result.status = ResetStatus.INTERRUPTED
            result.error_message = "Interrotto dall'utente"
            self._update_stats("in_progress", -1)
            self._update_stats("failed")
            return result
        
        # ===== FASE 2: RESET INCLINOMETRO =====
        result.current_phase = ResetPhase.RESET_SENDING
        result.status = ResetStatus.RESET_CMD
        
        reset_done = False
        while not self._stop_flag.is_set() and not reset_done:
            result.reset_attempts += 1
            log_and_update(f"[RESET] Tentativo {result.reset_attempts} - Invio comando...")
            
            # 4. Invia comando reset
            success, error, sent_time = self.api_client.send_command(
                deviceid, self.RESET_INCL_PAYLOAD
            )
            
            if not success:
                log_and_update(f"[RESET] Errore invio: {error}. Riprovo tra 5s...")
                time.sleep(5)
                continue
            
            # Salva timestamp del reset
            result.reset_timestamp = int(sent_time.timestamp() * 1000)
            result.reset_datetime = sent_time.strftime("%Y-%m-%d %H:%M:%S")
            
            log_and_update(f"[RESET] Comando inviato alle {sent_time.strftime('%H:%M:%S')}, attendo {self.check_interval}s...")
            
            # 5. Attendi e verifica nel commands-log
            result.current_phase = ResetPhase.RESET_CHECKING_LOG
            time.sleep(self.check_interval)
            
            if self._stop_flag.is_set():
                break
            
            # Ciclo di verifica
            check_attempts = 0
            while not self._stop_flag.is_set():
                check_attempts += 1
                log_and_update(f"[RESET] Verifica commands-log (check #{check_attempts})...")
                
                cmd_status = self.api_client.check_command_in_log(
                    deviceid, "set_value", self.RESET_INCL_MATCH, sent_time
                )
                
                if cmd_status["error"]:
                    log_and_update(f"[RESET] Errore check log: {cmd_status['error']}. Riprovo comando...")
                    break
                
                log_and_update(f"[RESET] Log status: {cmd_status['status']} ({cmd_status.get('debug_info', '')})")
                
                if cmd_status["status"] == "pending":
                    log_and_update(f"[RESET] Comando in pending, attendo {self.check_interval}s...")
                    time.sleep(self.check_interval)
                    continue
                
                if cmd_status["status"] == "sent_ok":
                    log_and_update(f"[RESET] ✓ Comando confermato (response={cmd_status['response_status']})")
                    result.reset_inclinometro = "OK"
                    reset_done = True
                    break
                
                elif cmd_status["status"] in ["sent_error", "sent_no_response"]:
                    log_and_update(f"[RESET] Comando fallito. Riprovo...")
                    break
                
                else:  # not_found
                    log_and_update(f"[RESET] Comando non trovato nel log. Debug: {cmd_status.get('debug_info', '')}. Riprovo...")
                    break
        
        if self._stop_flag.is_set():
            result.status = ResetStatus.INTERRUPTED
            result.error_message = "Interrotto dall'utente"
            self._update_stats("in_progress", -1)
            self._update_stats("failed")
            return result
        
        # ===== FASE 3: MAINTENANCE OFF =====
        result.current_phase = ResetPhase.MAINTENANCE_OFF_SENDING
        result.status = ResetStatus.MAINT_OFF
        
        maint_off_done = False
        while not self._stop_flag.is_set() and not maint_off_done:
            result.maint_off_attempts += 1
            log_and_update(f"[MAINT OFF] Tentativo {result.maint_off_attempts} - Invio comando...")
            
            # 6. Invia comando maintenance OFF
            success, error, sent_time = self.api_client.send_command(
                deviceid, self.MAINTENANCE_OFF_PAYLOAD
            )
            
            if not success:
                log_and_update(f"[MAINT OFF] Errore invio: {error}. Riprovo tra 5s...")
                time.sleep(5)
                continue
            
            log_and_update(f"[MAINT OFF] Comando inviato alle {sent_time.strftime('%H:%M:%S')}, attendo {self.check_interval}s...")
            
            # 7. Attendi e verifica nel commands-log
            result.current_phase = ResetPhase.MAINTENANCE_OFF_CHECKING_LOG
            time.sleep(self.check_interval)
            
            if self._stop_flag.is_set():
                break
            
            # Ciclo di verifica
            check_attempts = 0
            while not self._stop_flag.is_set():
                check_attempts += 1
                log_and_update(f"[MAINT OFF] Verifica commands-log (check #{check_attempts})...")
                
                cmd_status = self.api_client.check_command_in_log(
                    deviceid, "maintenance", self.MAINT_OFF_MATCH, sent_time
                )
                
                if cmd_status["error"]:
                    log_and_update(f"[MAINT OFF] Errore check log: {cmd_status['error']}. Riprovo comando...")
                    break
                
                log_and_update(f"[MAINT OFF] Log status: {cmd_status['status']} ({cmd_status.get('debug_info', '')})")
                
                if cmd_status["status"] == "pending":
                    log_and_update(f"[MAINT OFF] Comando in pending, attendo {self.check_interval}s...")
                    time.sleep(self.check_interval)
                    continue
                
                if cmd_status["status"] == "sent_ok":
                    log_and_update(f"[MAINT OFF] Comando confermato (response={cmd_status['response_status']})")
                    
                    # 8. Verifica lo stato nel configuration
                    result.current_phase = ResetPhase.MAINTENANCE_OFF_VERIFYING
                    log_and_update(f"[MAINT OFF] Verifica configuration...")
                    
                    success, maint_status, error = self.api_client.get_maintenance_status(deviceid)
                    
                    if success:
                        log_and_update(f"[MAINT OFF] Configuration maintenanceMode = '{maint_status}'")
                        if maint_status == "OFF":
                            result.maintenance_state = MaintenanceState.OFF
                            result.manutenzione_off = "OK"
                            result.has_maintenance_on_pending = False  # RESET FLAG
                            log_and_update(f"[MAINT OFF] ✓ Verificato: maintenanceMode=OFF")
                            maint_off_done = True
                            break
                        else:
                            result.maintenance_state = MaintenanceState.ON if maint_status == "ON" else MaintenanceState.UNKNOWN
                            log_and_update(f"[MAINT OFF] Configuration non ancora OFF, riprovo...")
                    else:
                        log_and_update(f"[MAINT OFF] Errore lettura configuration: {error}. Riprovo...")
                    
                    break
                
                elif cmd_status["status"] in ["sent_error", "sent_no_response"]:
                    log_and_update(f"[MAINT OFF] Comando fallito. Riprovo...")
                    break
                
                else:
                    log_and_update(f"[MAINT OFF] Comando non trovato nel log. Debug: {cmd_status.get('debug_info', '')}. Riprovo...")
                    break
        
        if self._stop_flag.is_set():
            result.status = ResetStatus.INTERRUPTED
            result.error_message = "Interrotto dall'utente"
            self._update_stats("in_progress", -1)
            self._update_stats("failed")
            return result
        
        # ===== COMPLETATO =====
        result.current_phase = ResetPhase.COMPLETED
        result.status = ResetStatus.OK
        log_and_update(f"✓ RESET COMPLETATO!")
        
        self._update_stats("in_progress", -1)
        self._update_stats("success")
        self._update_stats("completed")
        
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
            self._global_log(f"Errore autenticazione: {msg}", "ERROR")
            for did in device_ids:
                result = ResetResult(deviceid=did)
                result.tipo = detect_device_type(did)
                result.status = ResetStatus.ERROR
                result.error_message = f"Auth error: {msg}"
                with self._results_lock:
                    self._results[did] = result
            
            if completion_callback:
                completion_callback(list(self._results.values()))
            return list(self._results.values())
        
        self._global_log(f"Autenticazione OK. Avvio reset per {len(device_ids)} dispositivi...", "OK")
        
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
                    
                    if device_complete_callback:
                        device_complete_callback(result)
                        
                except Exception as e:
                    deviceid = futures[future]
                    self._global_log(f"{deviceid}: Eccezione - {e}", "ERROR")
                    
                    with self._results_lock:
                        if deviceid in self._results:
                            self._results[deviceid].status = ResetStatus.ERROR
                            self._results[deviceid].error_message = str(e)
                    
                    if device_complete_callback and deviceid in self._results:
                        device_complete_callback(self._results[deviceid])
        
        results = list(self._results.values())
        
        if completion_callback:
            completion_callback(results)
        
        return results
    
    def get_results(self) -> List[ResetResult]:
        """Restituisce i risultati correnti"""
        with self._results_lock:
            return list(self._results.values())
    
    def get_result(self, deviceid: str) -> Optional[ResetResult]:
        """Restituisce il risultato per un device specifico"""
        with self._results_lock:
            return self._results.get(deviceid)
    
    def get_ok_results(self) -> List[ResetResult]:
        """Restituisce solo i risultati con reset OK (per Fase 2)"""
        with self._results_lock:
            return [r for r in self._results.values() if r.reset_inclinometro == "OK"]
    
    def get_stats(self) -> Dict:
        """Restituisce le statistiche correnti"""
        with self._stats_lock:
            return dict(self.stats)


if __name__ == "__main__":
    print("Test ResetWorker v2.0")
    print("=" * 50)
    
    worker = ResetWorker()
    print(f"Max threads: {worker.max_threads}")
    print(f"Check interval: {worker.check_interval}s")
    
    # Test detect_device_type
    print("\nTest detect_device_type:")
    test_ids = ["1121621_0436", "1121525_0103", "1121622_0364"]
    for did in test_ids:
        print(f"  {did} -> {detect_device_type(did)}")
