"""
DIGIL Reset Inclinometro - API Client Module
=============================================
Gestisce l'autenticazione OAuth2 e le chiamate API verso il backend DIGIL.

v2.0.0 - Aggiunta logica commands-log e configuration check
"""

import requests
import urllib3
import threading
import time
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, Dict, Any, List
from dotenv import load_dotenv

# Disabilita warning SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Carica variabili d'ambiente
load_dotenv()


class TokenManager:
    """
    Gestisce il token OAuth2 con refresh automatico.
    Il token ha durata 300s, viene rinnovato preventivamente.
    """
    
    def __init__(self):
        self.auth_url = os.getenv("AUTH_URL")
        self.client_id = os.getenv("CLIENT_ID")
        self.client_secret = os.getenv("CLIENT_SECRET")
        
        self._token: Optional[str] = None
        self._token_expiry: float = 0
        self._lock = threading.Lock()
        
        # Rinnova 30 secondi prima della scadenza
        self.TOKEN_LIFETIME = 300  # secondi
        self.REFRESH_MARGIN = 30   # secondi
        
    def _is_token_valid(self) -> bool:
        """Verifica se il token è ancora valido"""
        if not self._token:
            return False
        return time.time() < (self._token_expiry - self.REFRESH_MARGIN)
    
    def _fetch_new_token(self) -> str:
        """Ottiene un nuovo token dal server di autenticazione"""
        response = requests.post(
            self.auth_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            verify=False,
            timeout=30
        )
        response.raise_for_status()
        
        token_data = response.json()
        return token_data.get("access_token")
    
    def get_token(self) -> str:
        """
        Restituisce un token valido, rinnovandolo se necessario.
        Thread-safe.
        """
        with self._lock:
            if not self._is_token_valid():
                self._token = self._fetch_new_token()
                self._token_expiry = time.time() + self.TOKEN_LIFETIME
            return self._token
    
    def invalidate(self):
        """Invalida il token corrente (utile dopo un 401/403)"""
        with self._lock:
            self._token = None
            self._token_expiry = 0
    
    def validate_config(self) -> Tuple[bool, str]:
        """
        Valida la configurazione e testa la connessione.
        Returns: (success, message)
        """
        # Verifica variabili d'ambiente
        if not self.auth_url:
            return False, "AUTH_URL non configurato nel file .env"
        if not self.client_id:
            return False, "CLIENT_ID non configurato nel file .env"
        if not self.client_secret:
            return False, "CLIENT_SECRET non configurato nel file .env"
        if self.client_secret == "YOUR_CLIENT_SECRET_HERE":
            return False, "CLIENT_SECRET contiene ancora il placeholder"
        
        # Testa l'autenticazione
        try:
            token = self._fetch_new_token()
            if token:
                self._token = token
                self._token_expiry = time.time() + self.TOKEN_LIFETIME
                return True, "Autenticazione OK"
            else:
                return False, "Token non ricevuto dal server"
        except requests.exceptions.ConnectionError:
            return False, "Impossibile connettersi al server di autenticazione"
        except requests.exceptions.HTTPError as e:
            return False, f"Errore autenticazione: {e.response.status_code}"
        except Exception as e:
            return False, f"Errore: {str(e)}"


class DIGILApiClient:
    """
    Client per le API DIGIL.
    Gestisce i comandi e le query sui dispositivi.
    """
    
    def __init__(self, token_manager: TokenManager):
        self.token_manager = token_manager
        self.base_url = os.getenv("BASE_URL", "https://digil-back-end-onesait.servizi.prv")
        self.cmd_url = os.getenv("CMD_URL", f"{self.base_url}/api/v1/digils/{{deviceid}}/command")
        self.device_url = os.getenv("DEVICE_URL", f"{self.base_url}/api/v1/digils/{{deviceid}}")
        self.config_url = f"{self.base_url}/api/v1/digils/{{deviceid}}/configuration"
        self.commands_log_url = f"{self.base_url}/api/v1/digils/{{deviceid}}/commands-log"
        self.retry_interval = int(os.getenv("RETRY_INTERVAL_SECONDS", "30"))
        
    def _get_headers(self) -> Dict[str, str]:
        """Costruisce gli headers con il token corrente"""
        return {
            "Authorization": f"Bearer {self.token_manager.get_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
    
    def _handle_token_refresh(self, response: requests.Response) -> bool:
        """Gestisce il refresh del token se necessario. Ritorna True se va ritentato."""
        if response.status_code in [401, 403]:
            self.token_manager.invalidate()
            return True
        return False
    
    def send_command(self, deviceid: str, payload: Dict[str, Any], 
                     timeout: int = 60) -> Tuple[bool, str, datetime]:
        """
        Invia un comando a un dispositivo.
        
        Args:
            deviceid: ID del dispositivo
            payload: Payload del comando
            timeout: Timeout in secondi
            
        Returns:
            (success, error_message, sent_time)
        """
        url = self.cmd_url.format(deviceid=deviceid)
        sent_time = datetime.now(timezone.utc)
        
        try:
            response = requests.post(
                url,
                json=payload,
                headers=self._get_headers(),
                verify=False,
                timeout=timeout
            )
            
            # Token scaduto/invalido - riprova una volta
            if self._handle_token_refresh(response):
                sent_time = datetime.now(timezone.utc)
                response = requests.post(
                    url,
                    json=payload,
                    headers=self._get_headers(),
                    verify=False,
                    timeout=timeout
                )
            
            response.raise_for_status()
            return (True, "", sent_time)
            
        except requests.exceptions.Timeout:
            return (False, "Timeout richiesta", sent_time)
        except requests.exceptions.ConnectionError:
            return (False, "Connessione fallita", sent_time)
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else "N/A"
            return (False, f"HTTP {status_code}", sent_time)
        except Exception as e:
            return (False, str(e), sent_time)
    
    def get_commands_log(self, deviceid: str, 
                         start_date: datetime,
                         end_date: Optional[datetime] = None) -> Tuple[bool, Optional[Dict], str]:
        """
        Ottiene il log dei comandi per un dispositivo.
        
        Args:
            deviceid: ID del dispositivo
            start_date: Data inizio ricerca (UTC)
            end_date: Data fine ricerca (UTC), default=now
            
        Returns:
            (success, data, error_message)
            data contiene: {"pendingCommands": [...], "sentCommands": [...]}
        """
        if end_date is None:
            end_date = datetime.now(timezone.utc)
        
        # Formatta le date nel formato richiesto dall'API
        start_str = start_date.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
        end_str = end_date.strftime("%Y-%m-%dT%H:%M:%S.999999999Z")
        
        url = f"{self.commands_log_url.format(deviceid=deviceid)}?startDate={start_str}&endDate={end_str}"
        
        try:
            response = requests.get(
                url,
                headers=self._get_headers(),
                verify=False,
                timeout=30
            )
            
            if self._handle_token_refresh(response):
                response = requests.get(
                    url,
                    headers=self._get_headers(),
                    verify=False,
                    timeout=30
                )
            
            response.raise_for_status()
            return (True, response.json(), "")
            
        except requests.exceptions.Timeout:
            return (False, None, "Timeout")
        except requests.exceptions.ConnectionError:
            return (False, None, "Connessione fallita")
        except requests.exceptions.HTTPError as e:
            return (False, None, f"HTTP {e.response.status_code}")
        except Exception as e:
            return (False, None, str(e))
    
    def get_device_configuration(self, deviceid: str) -> Tuple[bool, Optional[Dict], str]:
        """
        Ottiene la configurazione di un dispositivo.
        
        Args:
            deviceid: ID del dispositivo
            
        Returns:
            (success, data, error_message)
        """
        url = self.config_url.format(deviceid=deviceid)
        
        try:
            response = requests.get(
                url,
                headers=self._get_headers(),
                verify=False,
                timeout=30
            )
            
            if self._handle_token_refresh(response):
                response = requests.get(
                    url,
                    headers=self._get_headers(),
                    verify=False,
                    timeout=30
                )
            
            response.raise_for_status()
            return (True, response.json(), "")
            
        except requests.exceptions.Timeout:
            return (False, None, "Timeout")
        except requests.exceptions.ConnectionError:
            return (False, None, "Connessione fallita")
        except requests.exceptions.HTTPError as e:
            return (False, None, f"HTTP {e.response.status_code}")
        except Exception as e:
            return (False, None, str(e))
    
    def get_maintenance_status(self, deviceid: str) -> Tuple[bool, Optional[str], str]:
        """
        Ottiene lo stato di manutenzione di un dispositivo.
        
        Returns:
            (success, status, error_message)
            status può essere: "ON", "OFF", None (non letto)
        """
        success, data, error = self.get_device_configuration(deviceid)
        
        if not success:
            return (False, None, error)
        
        try:
            application = data.get("application", {})
            maintenance_mode = application.get("maintenanceMode")
            return (True, maintenance_mode, "")
        except Exception as e:
            return (False, None, f"Errore parsing: {e}")
    
    def check_command_in_log(self, deviceid: str, 
                             command_name: str,
                             command_payload_match: Dict[str, Any],
                             sent_after: datetime) -> Dict[str, Any]:
        """
        Verifica lo stato di un comando nel log.
        
        Args:
            deviceid: ID del dispositivo
            command_name: Nome del comando (es. "maintenance", "set_value")
            command_payload_match: Dizionario con valori da matchare nel payload
            sent_after: Il comando deve essere stato inviato DOPO questa data
            
        Returns:
            {
                "found": bool,
                "status": "pending" | "sent_ok" | "sent_error" | "not_found",
                "response_status": str | None,
                "correlation_id": str | None,
                "error": str,
                "debug_info": str
            }
        """
        result = {
            "found": False,
            "status": "not_found",
            "response_status": None,
            "correlation_id": None,
            "error": "",
            "debug_info": ""
        }
        
        # Ottieni il log dei comandi - usa un range più ampio (1 ora prima)
        search_start = sent_after - timedelta(hours=1)
        success, data, error = self.get_commands_log(deviceid, search_start)
        
        if not success:
            result["error"] = error
            return result
        
        # Debug info
        pending_count = len(data.get("pendingCommands", []))
        sent_count = len(data.get("sentCommands", []))
        result["debug_info"] = f"pending={pending_count}, sent={sent_count}"
        
        # Cerca nei pendingCommands
        pending_commands = data.get("pendingCommands", [])
        for cmd in pending_commands:
            if self._command_matches(cmd, command_name, command_payload_match, sent_after):
                result["found"] = True
                result["status"] = "pending"
                result["correlation_id"] = cmd.get("correlationId")
                return result
        
        # Cerca nei sentCommands
        sent_commands = data.get("sentCommands", [])
        for cmd in sent_commands:
            if self._command_matches(cmd, command_name, command_payload_match, sent_after):
                result["found"] = True
                result["correlation_id"] = cmd.get("correlationId")
                
                # Controlla la response
                response = cmd.get("response")
                if response:
                    status_str = str(response.get("status", ""))
                    result["response_status"] = status_str
                    
                    # 200 e 204 sono OK (anche come float "200.0", "204.0")
                    status_clean = status_str.replace(".0", "")
                    if status_clean in ["200", "204"]:
                        result["status"] = "sent_ok"
                    else:
                        result["status"] = "sent_error"
                else:
                    # Comando inviato ma senza response ancora
                    result["status"] = "sent_no_response"
                
                return result
        
        # Non trovato - aggiungi info debug
        result["debug_info"] += f" | cercato: name={command_name}, match={command_payload_match}, after={sent_after.isoformat()}"
        
        return result
    
    def _command_matches(self, cmd: Dict, command_name: str, 
                         payload_match: Dict, sent_after: datetime) -> bool:
        """Verifica se un comando nel log corrisponde ai criteri"""
        import json
        
        # Controlla il nome
        if cmd.get("name") != command_name:
            return False
        
        # Controlla il timestamp
        cmd_time_str = cmd.get("time", "")
        try:
            # Parse del timestamp ISO
            cmd_time = datetime.fromisoformat(cmd_time_str.replace("Z", "+00:00"))
            # Aggiungi un margine di 5 secondi per tolleranza
            margin = timedelta(seconds=5)
            if cmd_time < (sent_after - margin):
                return False
        except Exception as e:
            print(f"DEBUG: Errore parsing timestamp: {e}")
            return False
        
        # Controlla il payload
        cmd_payload_str = cmd.get("payload", "{}")
        try:
            # Il payload può essere una stringa JSON con newlines e spazi
            # Es: "{\n  \"status\" : \"ON\"\n}"
            if isinstance(cmd_payload_str, str):
                # Pulisci la stringa e parsa
                cmd_payload = json.loads(cmd_payload_str)
            else:
                cmd_payload = cmd_payload_str
            
            # Verifica che tutti i valori in payload_match siano presenti
            for key, value in payload_match.items():
                cmd_value = cmd_payload.get(key)
                # Confronto case-insensitive per stringhe
                if isinstance(cmd_value, str) and isinstance(value, str):
                    if cmd_value.upper() != value.upper():
                        return False
                elif cmd_value != value:
                    return False
                    
        except json.JSONDecodeError:
            # Fallback: cerca le stringhe nel payload grezzo
            # Normalizza rimuovendo spazi e newlines
            normalized = cmd_payload_str.replace(" ", "").replace("\n", "").lower()
            for key, value in payload_match.items():
                search_str = f'"{key}":"{value}"'.lower().replace(" ", "")
                if search_str not in normalized:
                    return False
        except Exception as e:
            print(f"DEBUG: Errore matching payload: {e}")
            return False
        
        return True
    
    def get_device_data(self, deviceid: str) -> Tuple[bool, Optional[Dict], str]:
        """
        Ottiene i dati completi di un dispositivo.
        
        Args:
            deviceid: ID del dispositivo
            
        Returns:
            (success, data, error_message)
        """
        url = self.device_url.format(deviceid=deviceid)
        
        try:
            response = requests.get(
                url,
                headers=self._get_headers(),
                verify=False,
                timeout=30
            )
            
            if self._handle_token_refresh(response):
                response = requests.get(
                    url,
                    headers=self._get_headers(),
                    verify=False,
                    timeout=30
                )
            
            response.raise_for_status()
            return (True, response.json(), "")
            
        except requests.exceptions.Timeout:
            return (False, None, "Timeout")
        except requests.exceptions.ConnectionError:
            return (False, None, "Connessione fallita")
        except requests.exceptions.HTTPError as e:
            return (False, None, f"HTTP {e.response.status_code}")
        except Exception as e:
            return (False, None, str(e))
    
    def verify_inclinometer_reset(self, deviceid: str, 
                                   reset_timestamp: int,
                                   tolerance: float = 0.20) -> Dict[str, Any]:
        """
        Verifica che il reset dell'inclinometro sia andato a buon fine.
        (Mantenuto per compatibilità con Fase 2)
        """
        result = {
            "deviceid": deviceid,
            "reset_timestamp": reset_timestamp,
            "verify_timestamp": int(time.time() * 1000),
            "api_success": False,
            "alarm_incl": None,
            "alarm_incl_timestamp": None,
            "inc_x_avg": None,
            "inc_x_timestamp": None,
            "inc_y_avg": None,
            "inc_y_timestamp": None,
            "alarm_ok": False,
            "inc_x_ok": False,
            "inc_y_ok": False,
            "timestamp_valid": False,
            "all_ok": False,
            "error": ""
        }
        
        success, data, error = self.get_device_data(deviceid)
        
        if not success:
            result["error"] = error
            return result
        
        result["api_success"] = True
        
        diags = data.get("diags", {})
        alm_incl = diags.get("ALG_Digil2_Alm_Incl", {})
        result["alarm_incl"] = alm_incl.get("value")
        result["alarm_incl_timestamp"] = alm_incl.get("timestamp")
        
        measures = data.get("measures", {})
        
        inc_x = measures.get("SENS_Digil2_Inc_X", {})
        result["inc_x_avg"] = inc_x.get("avg")
        result["inc_x_timestamp"] = inc_x.get("timestamp")
        
        inc_y = measures.get("SENS_Digil2_Inc_Y", {})
        result["inc_y_avg"] = inc_y.get("avg")
        result["inc_y_timestamp"] = inc_y.get("timestamp")
        
        if result["alarm_incl"] is not None:
            result["alarm_ok"] = (result["alarm_incl"] == False)
        
        if result["inc_x_avg"] is not None:
            result["inc_x_ok"] = abs(result["inc_x_avg"]) <= tolerance
        
        if result["inc_y_avg"] is not None:
            result["inc_y_ok"] = abs(result["inc_y_avg"]) <= tolerance
        
        data_timestamps = [
            result["alarm_incl_timestamp"],
            result["inc_x_timestamp"],
            result["inc_y_timestamp"]
        ]
        valid_timestamps = [t for t in data_timestamps if t is not None]
        
        if valid_timestamps:
            latest_timestamp = max(valid_timestamps)
            result["timestamp_valid"] = latest_timestamp > reset_timestamp
            result["data_timestamp"] = latest_timestamp
            result["timestamp_delta_ms"] = latest_timestamp - reset_timestamp
        
        result["all_ok"] = (
            result["alarm_ok"] and 
            result["inc_x_ok"] and 
            result["inc_y_ok"] and 
            result["timestamp_valid"]
        )
        
        return result


# Istanze globali (singleton pattern)
_token_manager: Optional[TokenManager] = None
_api_client: Optional[DIGILApiClient] = None


def get_token_manager() -> TokenManager:
    """Restituisce l'istanza singleton del TokenManager"""
    global _token_manager
    if _token_manager is None:
        _token_manager = TokenManager()
    return _token_manager


def get_api_client() -> DIGILApiClient:
    """Restituisce l'istanza singleton dell'ApiClient"""
    global _api_client
    if _api_client is None:
        _api_client = DIGILApiClient(get_token_manager())
    return _api_client


if __name__ == "__main__":
    # Test del modulo
    print("Test API Client v2.0")
    print("=" * 50)
    
    tm = get_token_manager()
    success, msg = tm.validate_config()
    print(f"Config validation: {success} - {msg}")
    
    if success:
        client = get_api_client()
        
        # Test get_maintenance_status
        print("\nTest get_maintenance_status...")
        success, status, error = client.get_maintenance_status("1121622_0364")
        if success:
            print(f"Maintenance status: {status}")
        else:
            print(f"Error: {error}")
