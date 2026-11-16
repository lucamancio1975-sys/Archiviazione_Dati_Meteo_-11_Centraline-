"""
Script principale di ingestion agrometeorologica e generazione file ad accesso rapido.
Eseguito ogni ora via GitHub Actions.
Protegge lo storico da sovrascritture con dati mancanti, interpola linearmente buchi brevi (<= 3h)
e calcola rigorosamente il VPD (Magnus-Tetens / FAO-56).
"""

import os
import sys
import csv
import json
import math
from datetime import datetime, timedelta
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
    Calcola il Vapor Pressure Deficit (VPD) in kPa usando l'equazione di Tetens / FAO-56.
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


def interpolate_short_gaps(history: Dict[str, Dict], max_gap_hours: int = 3) -> None:
    """
    Inserisce eventuali timestamp orari mancanti per buchi brevi (<= max_gap_hours)
    e interpola linearmente temperatura e umidità relativa, ricalcolando VPD e bagnatura fogliare.
    Buchi più lunghi non vengono riempiti.
    """
    sorted_ts = sorted(history.keys())
    if len(sorted_ts) < 2:
        return

    # 1. Se mancano intere righe orarie per buchi brevi (<= max_gap_hours), crea lo slot vuoto da interpolare
    for idx in range(len(sorted_ts) - 1):
        dt_a = datetime.strptime(sorted_ts[idx], "%Y-%m-%d %H:%M")
        dt_b = datetime.strptime(sorted_ts[idx + 1], "%Y-%m-%d %H:%M")
        diff_h = int(round((dt_b - dt_a).total_seconds() / 3600.0))
        if 1 < diff_h <= (max_gap_hours + 1):
            for step in range(1, diff_h):
                missing_ts = (dt_a + timedelta(hours=step)).strftime("%Y-%m-%d %H:%M")
                if missing_ts not in history:
                    history[missing_ts] = {
                        "temperature": None,
                        "humidity": None,
                        "rainfall": 0.0,
                        "leaf_wetness": False,
                        "vpd_kpa": None
                    }

    sorted_ts = sorted(history.keys())
    if len(sorted_ts) < 3:
        return

    for field in ("temperature", "humidity"):
        n = len(sorted_ts)
        i = 0
        while i < n:
            if history[sorted_ts[i]].get(field) is None:
                start_idx = i
                while i < n and history[sorted_ts[i]].get(field) is None:
                    i += 1
                end_idx = i - 1
                gap_len = end_idx - start_idx + 1
                # Verifica che esistano valori validi prima e dopo e che la distanza temporale reale sia <= max_gap_hours
                if start_idx > 0 and end_idx < n - 1 and gap_len <= max_gap_hours:
                    dt_prev = datetime.strptime(sorted_ts[start_idx - 1], "%Y-%m-%d %H:%M")
                    dt_next = datetime.strptime(sorted_ts[end_idx + 1], "%Y-%m-%d %H:%M")
                    total_hours = (dt_next - dt_prev).total_seconds() / 3600.0
                    if 1.0 < total_hours <= (max_gap_hours + 1):
                        val_prev = float(history[sorted_ts[start_idx - 1]][field])
                        val_next = float(history[sorted_ts[end_idx + 1]][field])
                        for k in range(start_idx, end_idx + 1):
                            dt_cur = datetime.strptime(sorted_ts[k], "%Y-%m-%d %H:%M")
                            frac = ((dt_cur - dt_prev).total_seconds() / 3600.0) / total_hours
                            interp_val = val_prev + frac * (val_next - val_prev)
                            history[sorted_ts[k]][field] = round(interp_val, 2 if field == "temperature" else 1)
            else:
                i += 1

    # Aggiorna VPD e bagnatura fogliare ove necessario
    for ts in sorted_ts:
        entry = history[ts]
        t_val = entry.get("temperature")
        rh_val = entry.get("humidity")
        r_val = entry.get("rainfall", 0.0)
        entry["leaf_wetness"] = calculate_leaf_wetness(rh_val, r_val)
        if entry.get("vpd_kpa") is None and t_val is not None and rh_val is not None:
            entry["vpd_kpa"] = calculate_vpd(t_val, rh_val)


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
            t_raw = row.get("Temperatura_C", "").strip()
            rh_raw = row.get("Umidita_Relativa_%", "").strip()
            r_raw = row.get("Pioggia_Oraria_mm", "").strip()
            vpd_raw = row.get("VPD_kPa", "").strip()

            t_val = float(t_raw.replace(",", ".")) if t_raw else None
            rh_val = float(rh_raw.replace(",", ".")) if rh_raw else None
            r_val = float(r_raw.replace(",", ".")) if r_raw else 0.0
            vpd_val = float(vpd_raw.replace(",", ".")) if vpd_raw else calculate_vpd(t_val, rh_val)

            history[ts_str] = {
                "temperature": t_val,
                "humidity": rh_val,
                "rainfall": r_val,
                "leaf_wetness": row.get("Foglia_Bagnata", "NO") == "SI",
                "vpd_kpa": vpd_val
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

        hourly_records = scraper.get_recent_hourly_weather(sid)

        st_csv = os.path.join(DIR_HISTORY, f"{sid}_{clean_name}.csv")
        history = load_station_history(st_csv)

        new_count = 0
        for rec in hourly_records:
            ts_str = rec["timestamp"].strftime("%Y-%m-%d %H:%M")
            new_t = rec.get("temperature")
            new_rh = rec.get("humidity")
            new_r = rec.get("rainfall", 0.0)
            new_vpd = rec.get("vpd_kpa")

            if ts_str not in history:
                # Inserisce la nuova ora solo se almeno temperatura o umidità o pioggia sono presenti
                if new_t is None and new_rh is None and new_r == 0.0:
                    continue
                new_count += 1
                t_final = new_t
                rh_final = new_rh
                r_final = new_r
            else:
                # Protezione anti-sovrascrittura: preserva i dati già validi se un sensore va in timeout
                prev = history[ts_str]
                t_final = new_t if new_t is not None else prev.get("temperature")
                rh_final = new_rh if new_rh is not None else prev.get("humidity")
                r_final = max(new_r, prev.get("rainfall", 0.0))

            vpd_final = new_vpd if (new_vpd is not None and new_t is not None and new_rh is not None) else calculate_vpd(t_final, rh_final)
            wet_final = calculate_leaf_wetness(rh_final, r_final)

            history[ts_str] = {
                "temperature": t_final,
                "humidity": rh_final,
                "rainfall": r_final,
                "leaf_wetness": wet_final,
                "vpd_kpa": vpd_final
            }

        # Interpolazione lineare di eventuali buchi brevi (<= 3h)
        interpolate_short_gaps(history, max_gap_hours=3)

        save_station_history(st_csv, sid, sname, history)

        sorted_ts = sorted(history.keys())
        # Prendi l'ultimo timestamp che abbia effettivamente T e UR validi per latest.json
        valid_ts = [ts for ts in sorted_ts if history[ts].get("temperature") is not None and history[ts].get("humidity") is not None]
        last_ts = valid_ts[-1] if valid_ts else (sorted_ts[-1] if sorted_ts else None)
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

    latest_json_path = os.path.join(DIR_DATA, "latest.json")
    with open(latest_json_path, mode="w", encoding="utf-8") as f:
        json.dump(latest_dict, f, ensure_ascii=False, indent=2)

    recent_json_path = os.path.join(DIR_DATA, "recent_48h.json")
    with open(recent_json_path, mode="w", encoding="utf-8") as f:
        json.dump(recent_48h_dict, f, ensure_ascii=False, indent=2)

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
    print("AGGIORNAMENTO COMPLETATO CON SUCCESSO")
    print("=" * 60)


if __name__ == "__main__":
    main()
