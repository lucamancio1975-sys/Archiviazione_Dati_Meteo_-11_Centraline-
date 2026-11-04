# Data Hub Agrometeorologico CFR Toscana (11 Stazioni)

Repository privata e autonoma per il monitoraggio, l'elaborazione e l'archiviazione continua 24/7 dei parametri agrometeorologici rilevati dalla rete ufficiale del **CFR Toscana** (Centro Funzionale Regionale).

Il sistema interroga direttamente i dati telemetrici sorgente del server regionale, calcola i parametri agronomici derivati (**Vapor Pressure Deficit - VPD** e **Bagnatura Fogliare**) e aggiorna i database a intervalli orari regolari tramite **GitHub Actions**.

---

## 📡 Rete Monitorata (11 Centraline)

| ID Stazione | Nome Stazione | Area Geografica | Sensori Attivi |
| :--- | :--- | :--- | :---: |
| `TOS11000008` | **Braccagni** | Grosseto Nord / Maremma | Pluvio, Termo, Igro |
| `TOS11000006` | **Capalbio** | Maremma Sud / Bassa Toscana | Pluvio, Termo, Igro |
| `TOS11000038` | **Ottavo** | Castiglion Fiorentino / Valdichiana / Arezzo | Pluvio, Termo, Igro |
| `TOS03002459` | **Follonica** | Maremma Nord / Costa | Pluvio, Termo, Igro |
| `TOS03003079` | **Manciano** | Colline del Fiora / Maremma | Pluvio, Termo, Igro |
| `TOS11000051` | **Montenero** | Amiata / Val d'Orcia | Pluvio, Termo, Igro |
| `TOS11000014` | **Pitigliano** | Area del Tufo / Maremma | Pluvio, Termo, Igro |
| `TOS11000005` | **Rispescia** | Grosseto Sud / Parco Maremma | Pluvio, Termo, Igro |
| `TOS03003099` | **S. Donato** | Orbetello / Costa d'Argento | Pluvio, Termo, Igro |
| `TOS11000042` | **Stiacciole** | Grosseto Est | Pluvio, Termo, Igro |
| `TOS11000041` | **Massa Marittima Valpiana** | Colline Metallifere / Maremma | Pluvio, Termo, Igro |

---

## ⚡ Struttura Dati per Interrogazione Ultra-Rapida

I file sono strutturati per consentire il parsing istantaneo da parte di applicazioni esterne (es. app per il VPD, DSS fitopatologici, dashboard Streamlit, app mobile):

### 1. `data/latest.json` (Consigliato per Tempo Reale & App VPD)
Contiene l'ultimissima rilevazione oraria consolidata di ciascuna delle 11 stazioni con tutti i parametri già pre-calcolati (tempo di lettura < 50ms):
```json
{
  "updated_at": "2026-11-04 15:00:00",
  "stations_count": 11,
  "stations": {
    "TOS03003099": {
      "id": "TOS03003099",
      "name": "S. Donato",
      "area": "Orbetello / Costa d'Argento",
      "timestamp": "2026-11-04 15:00",
      "temperature_c": 19.45,
      "humidity_rh": 51.5,
      "rainfall_mm": 0.0,
      "leaf_wetness": false,
      "vpd_kpa": 1.096
    }
  }
}
```

### 2. `data/recent_48h.json`
Array orario completo delle ultime 48 ore di tutte le stazioni, ottimizzato per grafici dinamici e trend a breve termine.

### 3. `data/latest.csv`
Tabella riassuntiva istantanea (una riga per stazione) per software o fogli di calcolo che leggono formati tabellari.

### 4. `data/history/{ID_STAZIONE}_{Nome}.csv`
Archivio storico permanente orario indicizzato, aggiornato progressivamente senza duplicati.

---

## 💻 Come Interrogare i Dati da un'altra Applicazione

Poiché la repository è privata, le altre app possono scaricare i dati passando il Personal Access Token (PAT) con permesso di sola lettura:

### Esempio in Python:
```python
import requests

GITHUB_TOKEN = "tuo_github_pat_qui"
REPO_OWNER = "lucamancio1975-sys"
REPO_NAME = "Archiviazione_Dati_Meteo_-11_Centraline-"

# URL del file data/latest.json
url = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/main/data/latest.json"
headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"}

response = requests.get(url, headers=headers)
data = response.json()

# Esempio: lettura temperatura e VPD di San Donato per la tua app
san_donato = data["stations"]["TOS03003099"]
print(f"Temperatura: {san_donato['temperature_c']} °C")
print(f"Umidità: {san_donato['humidity_rh']} %")
print(f"VPD: {san_donato['vpd_kpa']} kPa")
```

### Esempio in JavaScript / Node / Web:
```javascript
const response = await fetch("https://raw.githubusercontent.com/lucamancio1975-sys/Archiviazione_Dati_Meteo_-11_Centraline-/main/data/latest.json", {
  headers: {
    "Authorization": "Bearer tuo_github_pat_qui"
  }
});
const data = await response.json();
console.log(data.stations["TOS03003099"]);
```

---

## ⏱️ Automazione GitHub Actions
Il workflow `.github/workflows/hourly_meteo_monitor.yml` gira **24 ore su 24, 7 giorni su 7 al minuto :30 di ogni ora**.  
La scelta del minuto :30 compensa la latenza fisiologica di trasmissione radio del CFR (10–15 min), garantendo che tutti i 4 quarti d'ora dell'ora precedente siano consolidati prima dell'archiviazione.

