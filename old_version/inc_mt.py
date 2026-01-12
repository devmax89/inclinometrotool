"""
Reset Inclinometro DIGIL - Script con Retry Automatico
======================================================
Esegue il reset dell'inclinometro su dispositivi DIGIL con gestione
automatica dei retry per device Master e Slave.

Configurazione: tutte le credenziali e URL sono nel file .env
"""

import requests
import pandas as pd
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from datetime import datetime
import time
import os
from dotenv import load_dotenv

# Carica variabili d'ambiente dal file .env
load_dotenv()

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# === CONFIGURAZIONE DA .ENV ===
AUTH_URL = os.getenv("AUTH_URL")
CMD_URL = os.getenv("CMD_URL")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")

EXCEL_INPUT = os.getenv("EXCEL_INPUT", "input.xlsx")
EXCEL_OUTPUT = os.getenv("EXCEL_OUTPUT", "risultati.xlsx")
MAX_THREADS = int(os.getenv("MAX_THREADS", "87"))

# === CONFIGURAZIONE RETRY ===
RETRY_INTERVAL_SECONDS = int(os.getenv("RETRY_INTERVAL_SECONDS", "30"))
MAX_RETRY_MINUTES_MASTER = int(os.getenv("MAX_RETRY_MINUTES_MASTER", "10"))
MAX_RETRY_MINUTES_SLAVE = int(os.getenv("MAX_RETRY_MINUTES_SLAVE", "20"))

print_lock = threading.Lock()

# Contatori globali per statistiche
stats = {
    "success": 0,
    "failed": 0,
    "in_progress": 0,
    "total": 0
}
stats_lock = threading.Lock()


def validate_config():
    """Valida che tutte le configurazioni necessarie siano presenti"""
    required = {
        "AUTH_URL": AUTH_URL,
        "CMD_URL": CMD_URL,
        "CLIENT_ID": CLIENT_ID,
        "CLIENT_SECRET": CLIENT_SECRET
    }
    
    missing = [k for k, v in required.items() if not v]
    
    if missing:
        raise ValueError(
            f"Configurazione mancante nel file .env: {', '.join(missing)}\n"
            "Assicurati di aver configurato correttamente il file .env"
        )
    
    # Verifica che CLIENT_SECRET non sia il placeholder
    if CLIENT_SECRET == "YOUR_CLIENT_SECRET_HERE":
        raise ValueError(
            "CLIENT_SECRET non configurato!\n"
            "Modifica il file .env e inserisci il client_secret corretto."
        )


def log(deviceid, message, level="INFO"):
    """Log thread-safe con deviceid e livello"""
    with print_lock:
        ts = datetime.now().strftime("%H:%M:%S")
        icon = {"INFO": "ℹ️", "OK": "✅", "WARN": "⚠️", "ERROR": "❌", "RETRY": "🔄"}.get(level, "")
        print(f"[{ts}] [{deviceid}] {icon} {message}")


def update_stats(field, delta=1):
    """Aggiorna statistiche thread-safe"""
    with stats_lock:
        stats[field] += delta


def print_stats():
    """Stampa statistiche correnti"""
    with stats_lock:
        with print_lock:
            print(f"\n{'='*60}")
            print(f"📊 STATISTICHE: ✅ {stats['success']} OK | ❌ {stats['failed']} FALLITI | ⏳ {stats['in_progress']} IN CORSO | 📋 {stats['total']} TOTALI")
            print(f"{'='*60}\n")


def get_auth_token():
    """
    Ottiene token di autenticazione.
    Le credenziali vengono lette dal file .env
    """
    r = requests.post(
        AUTH_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET
        },
        verify=False,
        timeout=30
    )
    r.raise_for_status()
    return r.json().get("access_token")


def detect_device_type(deviceid):
    """
    Rileva automaticamente il tipo di device dal deviceid.
    
    Pattern:
    - 1121525_xxxx → Master (contiene "15" nella parte centrale)
    - 1121621_xxxx → Slave (contiene "16" nella parte centrale)
    
    Esempi:
    - 1121621_0562 → slave
    - 1121525_0103 → master
    - 1121625_0278 → slave
    - 1121525_0104 → master
    """
    deviceid_str = str(deviceid)
    
    # Controlla se contiene "15" (master) o "16" (slave) nella posizione 4-5
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


def read_excel(path):
    """Legge Excel e valida colonne"""
    df = pd.read_excel(path)
    if "deviceid" not in df.columns:
        raise ValueError("Colonna 'deviceid' mancante")
    return df


def api_call_with_retry(headers, deviceid, payload, action_name, max_minutes, token_refresh_func):
    """
    Esegue chiamata API con retry automatico.
    
    Args:
        headers: Headers HTTP
        deviceid: ID del dispositivo
        payload: Payload JSON
        action_name: Nome azione per logging
        max_minutes: Timeout massimo in minuti
        token_refresh_func: Funzione per refresh token
    
    Returns:
        tuple: (status, tentativi, tempo_totale)
    """
    url = CMD_URL.format(deviceid=deviceid)
    start_time = time.time()
    max_seconds = max_minutes * 60
    attempt = 0
    last_error = None
    current_headers = headers.copy()
    
    while True:
        attempt += 1
        elapsed = time.time() - start_time
        
        # Check timeout
        if elapsed >= max_seconds:
            return (f"TIMEOUT dopo {attempt} tentativi ({max_minutes} min)", attempt, elapsed)
        
        try:
            log(deviceid, f"{action_name} - Tentativo {attempt} (elapsed: {int(elapsed)}s)", "RETRY" if attempt > 1 else "INFO")
            
            r = requests.post(
                url,
                json=payload,
                headers=current_headers,
                verify=False,
                timeout=60
            )
            
            # Se 401/403, prova a refreshare il token
            if r.status_code in [401, 403]:
                log(deviceid, f"Token scaduto, rinnovo...", "WARN")
                try:
                    new_token = token_refresh_func()
                    current_headers["Authorization"] = f"Bearer {new_token}"
                    continue  # Riprova subito con nuovo token
                except Exception as e:
                    last_error = f"Errore refresh token: {e}"
                    time.sleep(RETRY_INTERVAL_SECONDS)
                    continue
            
            r.raise_for_status()
            
            # Successo!
            log(deviceid, f"{action_name} OK dopo {attempt} tentativi ({int(elapsed)}s)", "OK")
            return ("OK", attempt, elapsed)
            
        except requests.exceptions.Timeout:
            last_error = "Timeout richiesta"
            log(deviceid, f"{action_name} - Timeout, riprovo tra {RETRY_INTERVAL_SECONDS}s...", "WARN")
            
        except requests.exceptions.ConnectionError:
            last_error = "Device spento o irraggiungibile"
            log(deviceid, f"{action_name} - Device irraggiungibile, riprovo tra {RETRY_INTERVAL_SECONDS}s...", "WARN")
            
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else "N/A"
            last_error = f"HTTP {status_code}"
            log(deviceid, f"{action_name} - Errore HTTP {status_code}, riprovo tra {RETRY_INTERVAL_SECONDS}s...", "WARN")
            
        except Exception as e:
            last_error = str(e)
            log(deviceid, f"{action_name} - Errore: {last_error}, riprovo tra {RETRY_INTERVAL_SECONDS}s...", "WARN")
        
        # Attendi prima del prossimo tentativo
        remaining = max_seconds - (time.time() - start_time)
        if remaining > RETRY_INTERVAL_SECONDS:
            time.sleep(RETRY_INTERVAL_SECONDS)
        elif remaining > 0:
            time.sleep(remaining)
        else:
            break
    
    return (f"FALLITO: {last_error} (dopo {attempt} tentativi)", attempt, time.time() - start_time)


def process_device(deviceid):
    """
    Processa un singolo device con retry.
    Il tipo (master/slave) viene rilevato automaticamente dal deviceid.
    
    Args:
        deviceid: ID del dispositivo
    """
    update_stats("in_progress")
    
    # Rileva automaticamente il tipo dal deviceid
    device_type = detect_device_type(deviceid)
    max_retry_minutes = MAX_RETRY_MINUTES_MASTER if device_type == "master" else MAX_RETRY_MINUTES_SLAVE
    
    result = {
        "deviceid": deviceid,
        "tipo": device_type,
        "manutenzione_on": "NON ESEGUITO",
        "manutenzione_on_tentativi": 0,
        "reset_inclinometro": "NON ESEGUITO",
        "reset_inclinometro_tentativi": 0,
        "manutenzione_off": "NON ESEGUITO",
        "manutenzione_off_tentativi": 0,
        "tempo_totale_secondi": 0
    }
    
    start_total = time.time()
    
    log(deviceid, f"Inizio elaborazione ({device_type.upper()}, timeout: {max_retry_minutes} min)", "INFO")

    # 1) Autenticazione iniziale
    try:
        token = get_auth_token()
        log(deviceid, "Autenticazione OK", "OK")
    except Exception as e:
        err = str(e)
        log(deviceid, f"ERRORE autenticazione: {err}", "ERROR")
        result["manutenzione_on"] = f"ERRORE AUTH: {err}"
        result["reset_inclinometro"] = "SKIPPED"
        result["manutenzione_off"] = "SKIPPED"
        update_stats("in_progress", -1)
        update_stats("failed")
        return result

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # 2) Manutenzione ON (con retry)
    status, attempts, elapsed = api_call_with_retry(
        headers,
        deviceid,
        {"name": "maintenance", "params": {"status": {"values": ["ON"]}}},
        "Manutenzione ON",
        max_retry_minutes,
        get_auth_token
    )
    result["manutenzione_on"] = status
    result["manutenzione_on_tentativi"] = attempts
    
    # Se manutenzione ON fallisce, skippa il resto
    if status != "OK":
        result["reset_inclinometro"] = "SKIPPED (manutenzione ON fallita)"
        result["manutenzione_off"] = "SKIPPED"
        result["tempo_totale_secondi"] = int(time.time() - start_total)
        update_stats("in_progress", -1)
        update_stats("failed")
        log(deviceid, f"Elaborazione FALLITA - Manutenzione ON non riuscita", "ERROR")
        return result

    # 3) Reset inclinometro (con retry)
    status, attempts, elapsed = api_call_with_retry(
        headers,
        deviceid,
        {
            "name": "set_value",
            "params": {
                "peripheral": {"values": ["sjb"]},
                "param": {"values": ["COM_Digil2_Conf_Incl_Taratura"]},
                "value": {"values": ["1"]}
            }
        },
        "Reset inclinometro",
        max_retry_minutes,
        get_auth_token
    )
    result["reset_inclinometro"] = status
    result["reset_inclinometro_tentativi"] = attempts

    # 4) Manutenzione OFF (sempre tentata, con retry)
    status, attempts, elapsed = api_call_with_retry(
        headers,
        deviceid,
        {"name": "maintenance", "params": {"status": {"values": ["OFF"]}}},
        "Manutenzione OFF",
        max_retry_minutes,
        get_auth_token
    )
    result["manutenzione_off"] = status
    result["manutenzione_off_tentativi"] = attempts

    result["tempo_totale_secondi"] = int(time.time() - start_total)
    
    # Aggiorna statistiche
    update_stats("in_progress", -1)
    if result["reset_inclinometro"] == "OK":
        update_stats("success")
        log(deviceid, f"Elaborazione COMPLETATA in {result['tempo_totale_secondi']}s", "OK")
    else:
        update_stats("failed")
        log(deviceid, f"Elaborazione FALLITA dopo {result['tempo_totale_secondi']}s", "ERROR")
    
    return result


def main():
    # Valida configurazione prima di tutto
    try:
        validate_config()
    except ValueError as e:
        print(f"\n❌ ERRORE CONFIGURAZIONE:\n{e}\n")
        return
    
    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║         RESET INCLINOMETRO DIGIL - CON RETRY AUTOMATICO          ║
╠══════════════════════════════════════════════════════════════════╣
║  Master: timeout {MAX_RETRY_MINUTES_MASTER} min | Slave: timeout {MAX_RETRY_MINUTES_SLAVE} min              ║
║  Retry ogni {RETRY_INTERVAL_SECONDS}s | Max {MAX_THREADS} thread paralleli                       ║
╚══════════════════════════════════════════════════════════════════╝
""")
    
    # Verifica esistenza file input
    if not os.path.exists(EXCEL_INPUT):
        print(f"❌ File input non trovato: {EXCEL_INPUT}")
        return
    
    df = read_excel(EXCEL_INPUT)
    stats["total"] = len(df)
    
    # Conta master e slave
    master_count = sum(1 for _, row in df.iterrows() if detect_device_type(row["deviceid"]) == "master")
    slave_count = len(df) - master_count
    
    log("MAIN", f"Caricati {len(df)} device da {EXCEL_INPUT}", "INFO")
    log("MAIN", f"Rilevati: {master_count} Master (timeout {MAX_RETRY_MINUTES_MASTER}min) | {slave_count} Slave (timeout {MAX_RETRY_MINUTES_SLAVE}min)", "INFO")
    print_stats()

    results = []

    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = {
            executor.submit(process_device, row["deviceid"]): row["deviceid"]
            for _, row in df.iterrows()
        }

        for future in as_completed(futures):
            deviceid = futures[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                log(deviceid, f"Errore critico: {e}", "ERROR")
                results.append({
                    "deviceid": deviceid,
                    "tipo": "unknown",
                    "manutenzione_on": f"ERRORE CRITICO: {e}",
                    "reset_inclinometro": "SKIPPED",
                    "manutenzione_off": "SKIPPED"
                })
            
            # Stampa statistiche ogni 10 device completati
            if len(results) % 10 == 0:
                print_stats()

    # Salva risultati
    result_df = pd.DataFrame(results)
    
    # Riordina colonne
    cols_order = [
        "deviceid", "tipo",
        "manutenzione_on", "manutenzione_on_tentativi",
        "reset_inclinometro", "reset_inclinometro_tentativi",
        "manutenzione_off", "manutenzione_off_tentativi",
        "tempo_totale_secondi"
    ]
    result_df = result_df[[c for c in cols_order if c in result_df.columns]]
    
    result_df.to_excel(EXCEL_OUTPUT, index=False)
    
    print("\n")
    print_stats()
    log("MAIN", f"Elaborazione completata! File generato: {EXCEL_OUTPUT}", "OK")
    
    # Separa device OK e FALLITI
    devices_ok = [r["deviceid"] for r in results if r.get("reset_inclinometro") == "OK"]
    devices_failed = [r["deviceid"] for r in results if r.get("reset_inclinometro") != "OK"]
    
    # Riepilogo finale
    success_rate = (stats["success"] / stats["total"] * 100) if stats["total"] > 0 else 0
    
    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║                        RIEPILOGO FINALE                          ║
╠══════════════════════════════════════════════════════════════════╣
║  Device processati: {stats['total']:>5}                                       ║
║  Successi:          {stats['success']:>5} ({success_rate:.1f}%)                                 ║
║  Falliti:           {stats['failed']:>5}                                       ║
╚══════════════════════════════════════════════════════════════════╝
""")
    
    # Lista device OK
    print("✅ DEVICE OK ({}):\n".format(len(devices_ok)))
    if devices_ok:
        for did in sorted(devices_ok):
            tipo = detect_device_type(did)
            print(f"   {did} ({tipo})")
    else:
        print("   Nessuno")
    
    # Lista device FALLITI
    print("\n❌ DEVICE FALLITI ({}):\n".format(len(devices_failed)))
    if devices_failed:
        for did in sorted(devices_failed):
            tipo = detect_device_type(did)
            # Trova il motivo del fallimento
            device_result = next((r for r in results if r["deviceid"] == did), {})
            motivo = device_result.get("reset_inclinometro", "N/A")
            print(f"   {did} ({tipo}) - {motivo}")
    else:
        print("   Nessuno")
    
    print("\n" + "="*66)


if __name__ == "__main__":
    main()