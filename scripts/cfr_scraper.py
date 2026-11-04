"""
Modulo di acquisizione automatica dati dal Centro Funzionale Regionale (CFR) Toscana.
Estrae direttamente i dati numerici grezzi incorporati nelle pagine di dettaglio del CFR
senza necessità di elaborazione delle immagini o computer vision.
"""

import urllib.request
import ssl
import re
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple


class CFRScraper:
    BASE_URL = "https://www.cfr.toscana.it/monitoraggio/dettaglio.php"
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"

    def __init__(self):
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE
        self._pattern = re.compile(r'new Array\("([^"]*)","([^"]*)","([^"]*)","([^"]*)"\)')

    def _fetch_records(self, station_id: str, sensor_type: str) -> List[Tuple[str, str, float]]:
        url = f"{self.BASE_URL}?id={station_id}&title=&type={sensor_type}"
        req = urllib.request.Request(url, headers={"User-Agent": self.USER_AGENT})
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=12) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                raw_matches = self._pattern.findall(html)
                results = []
                for m in raw_matches:
                    dt_str = m[1].strip()
                    # Per la pioggia quartoraria (pluvio):
                    # m[2] è la cumulata mobile (linea blu), m[3] è la pioggia incrementale reale del quarto d'ora (barre ocra)
                    if sensor_type == "pluvio":
                        val_str = m[3].strip()
                        val = float(val_str) if val_str else 0.0
                    else:
                        val_str = m[2].strip()
                        try:
                            val = float(val_str)
                        except ValueError:
                            continue
                    results.append((m[0], dt_str, val))
                return results
        except Exception as e:
            print(f"[CFRScraper] Errore fetch ({station_id}, {sensor_type}): {e}")
            return []

    def get_recent_hourly_weather(self, station_id: str) -> List[Dict]:
        """
        Scarica pioggia, temperatura e umidità per le ultime 48 ore
        aggregandoli su base oraria per il motore DSS.
        """
        raw_rain = self._fetch_records(station_id, "pluvio")
        raw_temp = self._fetch_records(station_id, "termo")
        raw_rh = self._fetch_records(station_id, "igro")

        # Parsing orario: aggregazione campioni 15-minutali
        # dt format: '01/11/2026 00.15'
        data_by_hour: Dict[datetime, Dict] = {}

        def parse_dt(s: str) -> Optional[datetime]:
            try:
                # Gestione formato con punto 'DD/MM/YYYY HH.MM'
                clean_s = s.replace(".", ":")
                return datetime.strptime(clean_s, "%d/%m/%Y %H:%M")
            except Exception:
                return None

        # Pioggia: sommiamo i 4 scatti da 15 minuti dell'ora
        for _, dt_str, val in raw_rain:
            dt = parse_dt(dt_str)
            if not dt:
                continue
            h_dt = dt.replace(minute=0, second=0)
            if h_dt not in data_by_hour:
                data_by_hour[h_dt] = {"rainfall": 0.0, "temp_samples": [], "rh_samples": []}
            data_by_hour[h_dt]["rainfall"] += val

        # Temperatura: mediamo i campioni dell'ora
        for _, dt_str, val in raw_temp:
            dt = parse_dt(dt_str)
            if not dt:
                continue
            h_dt = dt.replace(minute=0, second=0)
            if h_dt not in data_by_hour:
                data_by_hour[h_dt] = {"rainfall": 0.0, "temp_samples": [], "rh_samples": []}
            data_by_hour[h_dt]["temp_samples"].append(val)

        # Umidità: mediamo i campioni dell'ora
        for _, dt_str, val in raw_rh:
            dt = parse_dt(dt_str)
            if not dt:
                continue
            h_dt = dt.replace(minute=0, second=0)
            if h_dt not in data_by_hour:
                data_by_hour[h_dt] = {"rainfall": 0.0, "temp_samples": [], "rh_samples": []}
            data_by_hour[h_dt]["rh_samples"].append(val)

        # Costruzione lista oraria finale ordinata
        records = []
        for h_dt in sorted(data_by_hour.keys()):
            entry = data_by_hour[h_dt]
            t = sum(entry["temp_samples"]) / len(entry["temp_samples"]) if entry["temp_samples"] else 15.0
            rh = sum(entry["rh_samples"]) / len(entry["rh_samples"]) if entry["rh_samples"] else 80.0
            rain = entry["rainfall"]
            records.append({
                "timestamp": h_dt,
                "temperature": round(t, 2),
                "humidity": round(rh, 1),
                "rainfall": round(rain, 2)
            })

        return records

    def get_monthly_rainfall(self, station_id: str) -> List[Dict]:
        """
        Scarica il riepilogo pluviometrico giornaliero degli ultimi 30 giorni (pluvio_men).
        Il sensore CFR 'pluvio_men' restituisce la CUMULATA PROGRESSIVA del periodo.
        Questa funzione calcola:
          - 'rain_daily_mm': la pioggia effettivamente caduta nel singolo giorno (delta rispetto al giorno precedente)
          - 'rain_cumulative_mm': la cumulata progressiva esposta dal CFR
        """
        raw = self._fetch_records(station_id, "pluvio_men")
        if not raw:
            return []

        parsed = []
        for _, dt_str, val in raw:
            try:
                d = datetime.strptime(dt_str, "%d/%m/%Y").date()
                parsed.append((d, float(val)))
            except Exception:
                continue

        parsed.sort(key=lambda x: x[0])
        out = []
        prev_cum = 0.0
        for i, (d, cum) in enumerate(parsed):
            if i == 0:
                daily = cum
            else:
                delta = round(cum - prev_cum, 1)
                if delta < 0:  # eventuale reset ciclo cumulata
                    delta = cum
                daily = max(0.0, delta)
            prev_cum = cum
            out.append({
                "date": d,
                "rain_daily_mm": daily,
                "rain_cumulative_mm": cum
            })
        return out


if __name__ == "__main__":
    st_id = "TOS03003099"
    scraper = CFRScraper()
    print(f"=== TEST SCRAPER CFR SU STAZIONE {st_id} ===")
    
    mensili = scraper.get_monthly_rainfall(st_id)
    print(f"\n[1] Pluviometria ultimi 30 giorni: {len(mensili)} giorni estratti.")
    for row in mensili[-5:]:
        print(f"    Data: {row['date']} -> Pioggia: {row['rain_daily_mm']} mm")
        
    orari = scraper.get_recent_hourly_weather(st_id)
    print(f"\n[2] Dati orari consolidati ultime 48h: {len(orari)} ore disponibili.")
    for row in orari[-5:]:
        print(f"    {row['timestamp']} | T: {row['temperature']:4.1f}°C | UR: {row['humidity']:4.1f}% | Rain: {row['rainfall']:4.1f} mm")

