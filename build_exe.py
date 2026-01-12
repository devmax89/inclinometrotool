"""
Build script per creare l'eseguibile Windows
============================================
Usa PyInstaller per creare un .exe standalone

Utilizzo:
    python build_exe.py

Output:
    dist/DIGIL_Reset_Inclinometro.exe
"""

import subprocess
import sys
import os
from pathlib import Path


def build():
    """Esegue il build dell'applicazione"""
    
    script_dir = Path(__file__).parent
    
    # Assicurati che le directory esistano
    (script_dir / "assets").mkdir(exist_ok=True)
    
    # Crea .env.example se non esiste
    env_example = script_dir / ".env.example"
    if not env_example.exists():
        with open(env_example, 'w') as f:
            f.write("""# ===========================================
# RESET INCLINOMETRO DIGIL - CONFIGURAZIONE
# ===========================================

# === URL API ===
AUTH_URL=https://rh-sso.apps.clusterzac.opencs.servizi.prv/auth/realms/DigilV2/protocol/openid-connect/token
CMD_URL=https://digil-back-end-onesait.servizi.prv/api/v1/digils/{deviceid}/command
DEVICE_URL=https://digil-back-end-onesait.servizi.prv/api/v1/digils/{deviceid}

# === CREDENZIALI AUTENTICAZIONE ===
CLIENT_ID=application
CLIENT_SECRET=YOUR_CLIENT_SECRET_HERE

# === CONFIGURAZIONE RETRY ===
RETRY_INTERVAL_SECONDS=30
MAX_RETRY_MINUTES_MASTER=10
MAX_RETRY_MINUTES_SLAVE=20

# === CONFIGURAZIONE THREAD ===
MAX_THREADS=87

# === TOLLERANZA INCLINOMETRO ===
INCL_TOLERANCE=0.20
""")
    
    # Opzioni PyInstaller
    pyinstaller_args = [
        sys.executable, '-m', 'PyInstaller',
        '--name=DIGIL_Reset_Inclinometro',
        '--onefile',
        '--windowed',
        '--clean',
        
        # Aggiungi file .env
        f'--add-data={script_dir / ".env"};.',
        
        # Moduli nascosti
        '--hidden-import=PyQt5.sip',
        '--hidden-import=pandas',
        '--hidden-import=openpyxl',
        '--hidden-import=xlsxwriter',
        '--hidden-import=requests',
        
        '--noupx',
        
        str(script_dir / 'main.py'),
    ]
    
    print("=" * 60)
    print("DIGIL Reset Inclinometro - Build Eseguibile")
    print("=" * 60)
    print()
    
    result = subprocess.run(pyinstaller_args, cwd=script_dir)
    
    if result.returncode == 0:
        print()
        print("=" * 60)
        print("BUILD COMPLETATO CON SUCCESSO!")
        print("=" * 60)
        print()
        print(f"Eseguibile: {script_dir / 'dist' / 'DIGIL_Reset_Inclinometro.exe'}")
        print()
        print("IMPORTANTE:")
        print("1. Copia il file .env nella stessa directory dell'exe")
        print("2. (Opzionale) Copia il logo in assets/logo_terna.png")
        print()
    else:
        print()
        print("ERRORE durante il build!")
        sys.exit(1)


if __name__ == "__main__":
    build()
