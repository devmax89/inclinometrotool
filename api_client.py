"""
DIGIL Reset Inclinometro - API Client Module
=============================================
Gestisce l'autenticazione OAuth2 e le chiamate API verso il backend DIGIL.
"""

import requests
import urllib3
import threading
import time
import os
from datetime import datetime
from typing import Optional, Tuple, Dict, Any
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
        self.cmd_url = os.getenv("CMD_URL")
        self.device_url = os.getenv("DEVICE_URL")
        self.retry_interval = int(os.getenv("RETRY_INTERVAL_SECONDS", "30"))
        
    def _get_headers(self) -> Dict[str, str]:
        """Costruisce gli headers con il token corrente"""
        return {
            "Authorization": f"Bearer {self.token_manager.get_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
    
    def send_command(self, deviceid: str, payload: Dict[str, Any], 
                     max_minutes: int = 10,
                     progress_callback=None) -> Tuple[str, int, Optional[int]]:
        """
        Invia un comando a un dispositivo con retry automatico.
        
        Args:
            deviceid: ID del dispositivo
            payload: Payload del comando
            max_minutes: Timeout massimo in minuti
            progress_callback: Callback per aggiornamenti (deviceid, message, attempt)
            
        Returns:
            (status, attempts, timestamp_success) - timestamp_success è in millisecondi epoch se OK
        """
        url = self.cmd_url.format(deviceid=deviceid)
        start_time = time.time()
        max_seconds = max_minutes * 60
        attempt = 0
        last_error = None
        
        while True:
            attempt += 1
            elapsed = time.time() - start_time
            
            # Check timeout
            if elapsed >= max_seconds:
                return (f"TIMEOUT dopo {attempt} tentativi ({max_minutes} min)", attempt, None)
            
            if progress_callback:
                remaining = int(max_seconds - elapsed)
                progress_callback(deviceid, f"Tentativo {attempt} ({remaining}s rimanenti)", attempt)
            
            try:
                response = requests.post(
                    url,
                    json=payload,
                    headers=self._get_headers(),
                    verify=False,
                    timeout=60
                )
                
                # Token scaduto/invalido
                if response.status_code in [401, 403]:
                    self.token_manager.invalidate()
                    continue
                
                response.raise_for_status()
                
                # Successo! Registra il timestamp in millisecondi (stesso formato API)
                success_timestamp = int(time.time() * 1000)
                return ("OK", attempt, success_timestamp)
                
            except requests.exceptions.Timeout:
                last_error = "Timeout richiesta"
            except requests.exceptions.ConnectionError:
                last_error = "Device irraggiungibile"
            except requests.exceptions.HTTPError as e:
                status_code = e.response.status_code if e.response else "N/A"
                last_error = f"HTTP {status_code}"
            except Exception as e:
                last_error = str(e)
            
            # Attendi prima del prossimo tentativo
            remaining = max_seconds - (time.time() - start_time)
            if remaining > self.retry_interval:
                time.sleep(self.retry_interval)
            elif remaining > 0:
                time.sleep(remaining)
            else:
                break
        
        return (f"FALLITO: {last_error}", attempt, None)
    
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
            
            # Token scaduto
            if response.status_code in [401, 403]:
                self.token_manager.invalidate()
                # Riprova una volta con nuovo token
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
        
        Controlla:
        1. ALG_Digil2_Alm_Incl == false
        2. SENS_Digil2_Inc_X.avg ~ 0 (entro tolleranza)
        3. SENS_Digil2_Inc_Y.avg ~ 0 (entro tolleranza)
        4. Il timestamp dei dati sia DOPO il reset_timestamp
        
        Args:
            deviceid: ID del dispositivo
            reset_timestamp: Timestamp in millisecondi di quando è stato eseguito il reset
            tolerance: Tolleranza per i valori di inclinazione
            
        Returns:
            Dict con i risultati della verifica
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
        
        # Chiama l'API
        success, data, error = self.get_device_data(deviceid)
        
        if not success:
            result["error"] = error
            return result
        
        result["api_success"] = True
        
        # Estrai i dati di diagnosi (allarmi)
        diags = data.get("diags", {})
        alm_incl = diags.get("ALG_Digil2_Alm_Incl", {})
        result["alarm_incl"] = alm_incl.get("value")
        result["alarm_incl_timestamp"] = alm_incl.get("timestamp")
        
        # Estrai le misure di inclinazione
        measures = data.get("measures", {})
        
        inc_x = measures.get("SENS_Digil2_Inc_X", {})
        result["inc_x_avg"] = inc_x.get("avg")
        result["inc_x_timestamp"] = inc_x.get("timestamp")
        
        inc_y = measures.get("SENS_Digil2_Inc_Y", {})
        result["inc_y_avg"] = inc_y.get("avg")
        result["inc_y_timestamp"] = inc_y.get("timestamp")
        
        # Verifica allarme (deve essere false)
        if result["alarm_incl"] is not None:
            result["alarm_ok"] = (result["alarm_incl"] == False)
        
        # Verifica inclinazione X (deve essere ~ 0)
        if result["inc_x_avg"] is not None:
            result["inc_x_ok"] = abs(result["inc_x_avg"]) <= tolerance
        
        # Verifica inclinazione Y (deve essere ~ 0)
        if result["inc_y_avg"] is not None:
            result["inc_y_ok"] = abs(result["inc_y_avg"]) <= tolerance
        
        # Verifica timestamp (i dati devono essere DOPO il reset)
        # Usa il timestamp più recente tra le misure
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
        
        # Tutto OK?
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
    print("Test API Client")
    print("=" * 50)
    
    tm = get_token_manager()
    success, msg = tm.validate_config()
    print(f"Config validation: {success} - {msg}")
    
    if success:
        client = get_api_client()
        print("\nTest get_device_data...")
        success, data, error = client.get_device_data("1121621_0436")
        if success:
            print(f"Device name: {data.get('name')}")
            print(f"Status: {data.get('status')}")
        else:
            print(f"Error: {error}")
