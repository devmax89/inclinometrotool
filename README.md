# DIGIL Reset Inclinometro

**Tool per il reset e la verifica degli inclinometri sui dispositivi DIGIL IoT**

Sviluppato per **Terna S.p.A.** - Team IoT

---

## 📋 Descrizione

Questo tool permette di eseguire il reset dell'inclinometro sui dispositivi DIGIL e successivamente verificare che il reset sia andato a buon fine.

### Fase 1: Reset Inclinometro
1. **Manutenzione ON** - Attiva la modalità manutenzione sul device
2. **Reset Inclinometro** - Invia il comando di reset (set_value COM_Digil2_Conf_Incl_Taratura = 1)
3. **Manutenzione OFF** - Disattiva la modalità manutenzione

### Fase 2: Verifica Reset
Verifica che per ogni dispositivo:
- **ALG_Digil2_Alm_Incl** = `false` (allarme disattivato)
- **SENS_Digil2_Inc_X.avg** ~ 0 (tolleranza ±0.20)
- **SENS_Digil2_Inc_Y.avg** ~ 0 (tolleranza ±0.20)
- **Timestamp dati** > **Timestamp reset** (i dati sono stati aggiornati dopo il reset)

---

## 🚀 Installazione

### Prerequisiti
- Python 3.10 o superiore
- Connettività verso le API DIGIL (VPN se necessario)

### Setup

```bash
# Clona o scarica il progetto
cd reset_inclinometro_tool

# Crea ambiente virtuale (consigliato)
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/Mac

# Installa dipendenze
pip install -r requirements.txt
```

### Configurazione

Modifica il file `.env` con le credenziali corrette

---

## 💻 Utilizzo

### Avvio

```bash
python main.py
```

### Workflow

1. **Fase 1 - Reset**
   - Carica il file Excel con la colonna `deviceid`
   - Imposta il numero di thread paralleli
   - Clicca "Avvia Reset"
   - Attendi il completamento
   - Esporta i risultati

2. **Fase 2 - Verifica**
   - Dopo la Fase 1, passa al tab "Fase 2"
   - Clicca "Avvia Verifica"
   - Il tool verificherà automaticamente tutti i device con reset OK
   - Esporta i risultati

### Formato File Input

Il file Excel deve avere una colonna `deviceid`:

| deviceid |
|----------|
| 1121621_0436 |
| 1121525_0103 |
| ... |

### Indicatori di Stato

| Icona | Significato |
|-------|-------------|
| ✅ | Operazione completata con successo |
| ❌ | Operazione fallita |
| 🔄 | Operazione in corso |
| ⏳ | In attesa |

---

## 📦 Build Eseguibile

Per creare un eseguibile standalone:

```bash
pip install pyinstaller
python build_exe.py
```

L'eseguibile sarà in `dist/DIGIL_Reset_Inclinometro.exe`

### Distribuzione

Per distribuire:
1. Copia `DIGIL_Reset_Inclinometro.exe`
2. Copia il file `.env` nella stessa cartella
3. (Opzionale) Crea `assets/logo_terna.png`

---

## 📁 Struttura Progetto

```
reset_inclinometro_tool/
├── main.py                 # GUI principale
├── api_client.py           # Client API e autenticazione
├── reset_worker.py         # Logica Fase 1 (Reset)
├── verify_worker.py        # Logica Fase 2 (Verifica)
├── data_handler.py         # Gestione file Excel
├── build_exe.py            # Script per build .exe
├── requirements.txt        # Dipendenze Python
├── .env                    # Configurazione (NON committare!)
├── .env.example            # Template configurazione
├── README.md               # Questo file
└── assets/
    └── logo_terna.png      # Logo Terna (posiziona qui il file)
```

### Logo Terna
Posiziona il file del logo nella cartella `assets/` con uno di questi nomi:
- `logo_terna.png` (preferito)
- `logo.png`
- `terna_logo.png`

---

## 🔒 Sicurezza

⚠️ **IMPORTANTE**:
- Il file `.env` contiene credenziali sensibili. **NON** condividerlo.
- Aggiungi `.env` al `.gitignore`

---

## 📊 Output Excel

### Fase 1 - Reset Results

| Colonna | Descrizione |
|---------|-------------|
| deviceid | ID del dispositivo |
| tipo | master/slave |
| manutenzione_on | Esito ON |
| reset_inclinometro | Esito reset |
| manutenzione_off | Esito OFF |
| reset_timestamp | Timestamp epoch (ms) |
| reset_datetime | Data/ora leggibile |

### Fase 2 - Verify Results

| Colonna | Descrizione |
|---------|-------------|
| deviceid | ID del dispositivo |
| all_ok | OK se tutto verificato |
| alarm_incl | Valore allarme |
| alarm_ok | OK se false |
| inc_x_avg | Valore inclinazione X |
| inc_x_ok | OK se entro tolleranza |
| inc_y_avg | Valore inclinazione Y |
| inc_y_ok | OK se entro tolleranza |
| timestamp_valid | OK se dati recenti |
| timestamp_delta_readable | Differenza temporale |

---

## 🔧 Troubleshooting

### "Errore Autenticazione"
- Verifica le credenziali nel file `.env`
- Verifica la connettività verso il server di autenticazione

### "Device irraggiungibile"
- I device slave possono essere in sleep mode
- Il tool ritenta automaticamente (20 min per slave, 10 min per master)

### "Timestamp invalido" nella verifica
- I dati del device non sono stati aggiornati dopo il reset
- Attendere che il device invii nuovi dati e ripetere la verifica

---

## 📝 Changelog

### v1.0.0 (2025-01)
- Release iniziale
- Fase 1: Reset inclinometro con manutenzione ON/OFF
- Fase 2: Verifica allarme e valori inclinazione
- GUI professionale stile Terna
- Export risultati in Excel
- Riconoscimento automatico master/slave
- Gestione retry con timeout differenziati