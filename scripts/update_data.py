"""
Script principale di ingestion agrometeorologica e generazione file ad accesso rapido.
Eseguito ogni ora via GitHub Actions.
"""

import os
import sys
import csv
import json
import math
from datetime import datetime
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cfr_scraper import CFRScraper

DIR_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_DATA = os.path.join(DIR_REPO, "data")
DIR_HISTORY = os.path.join(DIR_DATA, "history")
os.makedirs(DIR_HISTORY, exist_ok=True)

STATIONS_FILE = os.path.join(DIR_REPO, "stations.json")


def calculate_vpd(temperature: Optional[float], humidity: Optional[float]) -> Optional[float]:
    """
    Calcola il Vapor Pressure Deficit (VPD) in kPa usando l'equazione di Tetens / ASCE.
    VPD = es - ea = es * (1 - UR / 100)
    """
    if temperature is None or humidity is None:
        return None
    try:
        t = float(temperature)
        rh = float(humidity)
        es = 0.61078 * math.exp((17.27 * t) / (t + 237.3))
        vpd = es * (1.0 - (rh / 100.0))
        return round(max(0.0, vpd), 3)
    except Exception:
        return None


def calculate_leaf_wetness(humidity: Optional[float], rainfall: float) -> bool:
    """Stima agronomica: pioggia > 0 mm oppure UR >= 90%."""
    if rainfall > 0.0:
        return True
    if humidity is not None and humidity >= 90.0:
        return True
    return False


def load_station_history(csv_path: str) -> Dict[str, Dict]:
    """Carica lo storico esistente della stazione indicizzato per Data_Ora (YYYY-MM-DD HH:MM)."""
    history = {}
    if not os.path.exists(csv_path):
        return history

    with open(csv_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            ts_str = row.get("Data_Ora", "").strip()
            if not ts_str:
                continue
            history[ts_str] = {
                "temperature": float(row["Temperatura_C"].replace(",", ".")) if row.get("Temperatura_C") else None,
                "humidity": float(row["Umidita_Relativa_%"].replace(",", ".")) if row.get("Umidita_Relativa_%") else None,
                "rainfall": float(row["Pioggia_Oraria_mm"].replace(",", ".")) if row.get("Pioggia_Oraria_mm") else 0.0,
                "leaf_wetness": row.get("Foglia_Bagnata", "NO") == "SI",
                "vpd_kpa": float(row["VPD_kPa"].replace(",", ".")) if row.get("VPD_kPa") else None
            }
    return history


def save_station_history(csv_path: str, station_id: str, station_name: str, history: Dict[str, Dict]):
    """Salva lo storico completo ordinato cronologicamente."""
    sorted_ts = sorted(history.keys())
    with open(csv_path, mode="w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["ID_Stazione", "Nome_Stazione", "Data_Ora", "Temperatura_C", "Umidita_Relativa_%", "Pioggia_Oraria_mm", "Foglia_Bagnata", "VPD_kPa"])
        for ts_str in sorted_ts:
            entry = history[ts_str]
            t_str = str(entry["temperature"]).replace(".", ",") if entry["temperature"] is not None else ""
            rh_str = str(entry["humidity"]).replace(".", ",") if entry["humidity"] is not None else ""
            r_str = str(entry["rainfall"]).replace(".", ",")
            wet_str = "SI" if entry["leaf_wetness"] else "NO"
            vpd_str = str(entry["vpd_kpa"]).replace(".", ",") if entry["vpd_kpa"] is not None else ""
            writer.writerow([station_id, station_name, ts_str, t_str, rh_str, r_str, wet_str, vpd_str])


def main():
    print(f"=== AGGIORNAMENTO DATI AGROMETEO RETE CFR TOSCANA ===")
    print(f"Orario esecuzione: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    with open(STATIONS_FILE, encoding="utf-8") as f:
        stations = json.load(f)

    scraper = CFRScraper()

    latest_dict = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stations_count": len(stations),
        "stations": {}
    }

    recent_48h_dict = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stations": {}
    }

    latest_table_rows = []

    for st in stations:
        sid = st["id"]
        sname = st["name"]
        area = st.get("area", "")
        clean_name = sname.replace(" ", "_").replace("(", "").replace(")", "").replace(".", "")

        print(f"[*] Aggiornamento {sid} ({sname})...", end=" ")

        # Scarico dati orari recenti (ultime 36-48h)
        hourly_records = scraper.get_recent_hourly_weather(sid)

        # File storico della stazione
        st_csv = os.path.join(DIR_HISTORY, f"{sid}_{clean_name}.csv")
        history = load_station_history(st_csv)

        # Merge dei nuovi dati
        new_count = 0
        for rec in hourly_records:
            ts_str = rec["timestamp"].strftime("%Y-%m-%d %H:%M")
            t_val = rec["temperature"]
            rh_val = rec["humidity"]
            r_val = rec["rainfall"]
            wet_val = calculate_leaf_wetness(rh_val, r_val)
            vpd_val = calculate_vpd(t_val, rh_val)

            if ts_str not in history:
                new_count += 1

            history[ts_str] = {
                "temperature": t_val,
                "humidity": rh_val,
                "rainfall": r_val,
                "leaf_wetness": wet_val,
                "vpd_kpa": vpd_val
            }

        # Salvataggio storico aggiornato
        save_station_history(st_csv, sid, sname, history)

        # Estrazione ultimo dato per latest.json
        sorted_ts = sorted(history.keys())
        last_ts = sorted_ts[-1] if sorted_ts else None
        last_entry = history[last_ts] if last_ts else None

        if last_entry:
            latest_dict["stations"][sid] = {
                "id": sid,
                "name": sname,
                "area": area,
                "timestamp": last_ts,
                "temperature_c": last_entry["temperature"],
                "humidity_rh": last_entry["humidity"],
                "rainfall_mm": last_entry["rainfall"],
                "leaf_wetness": last_entry["leaf_wetness"],
                "vpd_kpa": last_entry["vpd_kpa"]
            }

            latest_table_rows.append({
                "ID_Stazione": sid,
                "Nome_Stazione": sname,
                "Area_Geografica": area,
                "Ultimo_Timestamp": last_ts,
                "Temperatura_C": last_entry["temperature"],
                "Umidita_Relativa_%": last_entry["humidity"],
                "Pioggia_Oraria_mm": last_entry["rainfall"],
                "Foglia_Bagnata": "SI" if last_entry["leaf_wetness"] else "NO",
                "VPD_kPa": last_entry["vpd_kpa"]
            })

        # Estrazione ultime 48 ore per recent_48h.json
        recent_ts = sorted_ts[-48:] if len(sorted_ts) >= 48 else sorted_ts
        recent_48h_dict["stations"][sid] = [
            {
                "timestamp": ts,
                "temperature_c": history[ts]["temperature"],
                "humidity_rh": history[ts]["humidity"],
                "rainfall_mm": history[ts]["rainfall"],
                "leaf_wetness": history[ts]["leaf_wetness"],
                "vpd_kpa": history[ts]["vpd_kpa"]
            }
            for ts in recent_ts
        ]

        print(f"[OK] (nuove ore aggiunte: {new_count}, totale storico: {len(history)})")

    # 1. Scrittura data/latest.json (accesso ultra-rapido per app VPD e dashboard)
    latest_json_path = os.path.join(DIR_DATA, "latest.json")
    with open(latest_json_path, mode="w", encoding="utf-8") as f:
        json.dump(latest_dict, f, ensure_ascii=False, indent=2)

    # 2. Scrittura data/recent_48h.json (per grafici a breve termine)
    recent_json_path = os.path.join(DIR_DATA, "recent_48h.json")
    with open(recent_json_path, mode="w", encoding="utf-8") as f:
        json.dump(recent_48h_dict, f, ensure_ascii=False, indent=2)

    # 3. Scrittura data/latest.csv (tabella oraria di sintesi)
    latest_csv_path = os.path.join(DIR_DATA, "latest.csv")
    with open(latest_csv_path, mode="w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["ID_Stazione", "Nome_Stazione", "Area_Geografica", "Ultimo_Timestamp", "Temperatura_C", "Umidita_Relativa_%", "Pioggia_Oraria_mm", "Foglia_Bagnata", "VPD_kPa"])
        for r in latest_table_rows:
            w.writerow([
                r["ID_Stazione"],
                r["Nome_Stazione"],
                r["Area_Geografica"],
                r["Ultimo_Timestamp"],
                str(r["Temperatura_C"]).replace(".", ",") if r["Temperatura_C"] is not None else "",
                str(r["Umidita_Relativa_%"]).replace(".", ",") if r["Umidita_Relativa_%"] is not None else "",
                str(r["Pioggia_Oraria_mm"]).replace(".", ","),
                r["Foglia_Bagnata"],
                str(r["VPD_kPa"]).replace(".", ",") if r["VPD_kPa"] is not None else ""
            ])

    print("\n" + "=" * 60)
    print("AGGIORNAMENTO COMPLETATO CON SUCCESSO:")
    print(f"- {latest_json_path} (File rapido per app VPD / Tempo Reale)")
    print(f"- {recent_json_path} (Ultime 48 ore)")
    print(f"- {latest_csv_path} (Tabella CSV live)")
    print(f"- {DIR_HISTORY}/ (Archivi storici delle 11 stazioni)")
    print("=" * 60)


if __name__ == "__main__":
    main()

