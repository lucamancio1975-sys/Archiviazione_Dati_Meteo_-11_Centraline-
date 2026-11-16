"""
Modulo di acquisizione automatica dati dal Centro Funzionale Regionale (CFR) Toscana.
Estrae direttamente i dati numerici grezzi incorporati nelle pagine di dettaglio del CFR
senza necessità di elaborazione delle immagini o computer vision.
Include retry automatico su timeout/errori transitori e calcolo sincrono del VPD senza valori fittizi.
"""

import urllib.request
import ssl
import re
import time
import math
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple


def compute_instant_vpd(t: float, rh: float) -> float:
    """Calcola il VPD istantaneo (kPa) secondo l'equazione di Magnus-Tetens / FAO-56."""
    es = 0.61078 * math.exp((17.27 * t) / (t + 237.3))
    return max(0.0, es * (1.0 - (rh / 100.0)))


class CFRScraper:
    BASE_URL = "https://www.cfr.toscana.it/monitoraggio/dettaglio.php"
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"

    def __init__(self):
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE
        self._pattern = re.compile(r'new Array\("([^"]*)","([^"]*)","([^"]*)","([^"]*)"\)')

    def _fetch_records(self, station_id: str, sensor_type: str, max_retries: int = 3) -> List[Tuple[str, str, float]]:
        url = f"{self.BASE_URL}?id={station_id}&title=&type={sensor_type}"
        for attempt in range(1, max_retries + 1):
            req = urllib.request.Request(url, headers={"User-Agent": self.USER_AGENT})
            try:
                with urllib.request.urlopen(req, context=self.ctx, timeout=15) as resp:
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
                    if results:
                        return results
            except Exception as e:
                if attempt == max_retries:
                    print(f"[CFRScraper] Errore fetch ({station_id}, {sensor_type}) dopo {max_retries} tentativi: {e}")
                else:
                    time.sleep(2 * attempt)
        return []

    def get_recent_hourly_weather(self, station_id: str) -> List[Dict]:
        """
        Scarica pioggia, temperatura e umidità per le ultime 48 ore
        aggregandoli su base oraria senza MAI inserire valori di default inventati (es. 15°C o 80%).
        Calcola anche il VPD sincrono sui campioni quartorari.
        """
        raw_rain = self._fetch_records(station_id, "pluvio")
        raw_temp = self._fetch_records(station_id, "termo")
        raw_rh = self._fetch_records(station_id, "igro")

        data_by_hour: Dict[datetime, Dict] = {}
        temp_by_15m: Dict[datetime, float] = {}
        rh_by_15m: Dict[datetime, float] = {}

        def parse_dt(s: str) -> Optional[datetime]:
            try:
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
                data_by_hour[h_dt] = {"rainfall": 0.0, "rain_count": 0, "temp_samples": [], "rh_samples": [], "vpd_samples": []}
            data_by_hour[h_dt]["rainfall"] += max(0.0, val)
            data_by_hour[h_dt]["rain_count"] += 1

        # Temperatura: campioni dell'ora
        for _, dt_str, val in raw_temp:
            dt = parse_dt(dt_str)
            if not dt:
                continue
            # Scarta eventuali letture fisicamente impossibili per la Toscana (-25°C .. +50°C)
            if not (-25.0 <= val <= 50.0):
                continue
            temp_by_15m[dt] = val
            h_dt = dt.replace(minute=0, second=0)
            if h_dt not in data_by_hour:
                data_by_hour[h_dt] = {"rainfall": 0.0, "rain_count": 0, "temp_samples": [], "rh_samples": [], "vpd_samples": []}
            data_by_hour[h_dt]["temp_samples"].append(val)

        # Umidità: campioni dell'ora
        for _, dt_str, val in raw_rh:
            dt = parse_dt(dt_str)
            if not dt:
                continue
            if not (1.0 <= val <= 100.0):
                continue
            rh_by_15m[dt] = val
            h_dt = dt.replace(minute=0, second=0)
            if h_dt not in data_by_hour:
                data_by_hour[h_dt] = {"rainfall": 0.0, "rain_count": 0, "temp_samples": [], "rh_samples": [], "vpd_samples": []}
            data_by_hour[h_dt]["rh_samples"].append(val)

        # Calcolo VPD quartorario sincrono (T e UR allo stesso identico quarto d'ora)
        for dt_15, t_15 in temp_by_15m.items():
            if dt_15 in rh_by_15m:
                rh_15 = rh_by_15m[dt_15]
                h_dt = dt_15.replace(minute=0, second=0)
                if h_dt in data_by_hour:
                    data_by_hour[h_dt]["vpd_samples"].append(compute_instant_vpd(t_15, rh_15))

        # Costruzione lista oraria finale ordinata (SENZA fallback fittizi!)
        records = []
        for h_dt in sorted(data_by_hour.keys()):
            entry = data_by_hour[h_dt]
            t = round(sum(entry["temp_samples"]) / len(entry["temp_samples"]), 2) if entry["temp_samples"] else None
            rh = round(sum(entry["rh_samples"]) / len(entry["rh_samples"]), 1) if entry["rh_samples"] else None
            rain = round(entry["rainfall"], 2)

            if entry["vpd_samples"]:
                vpd = round(sum(entry["vpd_samples"]) / len(entry["vpd_samples"]), 3)
            elif t is not None and rh is not None:
                vpd = round(compute_instant_vpd(t, rh), 3)
            else:
                vpd = None

            records.append({
                "timestamp": h_dt,
                "temperature": t,
                "humidity": rh,
                "rainfall": rain,
                "vpd_kpa": vpd
            })

        return records

    def get_monthly_rainfall(self, station_id: str) -> List[Dict]:
        """
        Scarica il riepilogo pluviometrico giornaliero degli ultimi 30 giorni (pluvio_men).
        Il sensore CFR 'pluvio_men' restituisce la CUMULATA PROGRESSIVA del periodo.
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
                if delta < 0:
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
    orari = scraper.get_recent_hourly_weather(st_id)
    print(f"\nDati orari consolidati ultime 48h: {len(orari)} ore disponibili.")
    for row in orari[-5:]:
        print(f"    {row['timestamp']} | T: {row['temperature']}°C | UR: {row['humidity']}% | Rain: {row['rainfall']} mm | VPD: {row['vpd_kpa']} kPa")
