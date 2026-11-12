from fastapi import FastAPI, BackgroundTasks, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pandas as pd
import os
import io
import json
import re
import time
import threading
from datetime import datetime
from dotenv import load_dotenv
from typing import Optional
from contextlib import asynccontextmanager

load_dotenv()

SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/1fqp6xsIxYOkF-SWaEUBae1pW7BKu9RMiI7xyjOuvB5o/gviz/tq?tqx=out:csv&sheet=MERAH"
LANDING_SHEET_URL = "https://docs.google.com/spreadsheets/d/1p-QhV_ZgbLqKy77DWtdwCsUVcdOPauA9UMfMeb8c7NQ/gviz/tq?tqx=out:csv&sheet=Sheet1"
TERJANGKIT_JSON_PATH = "negara_terjangkit.json"

CACHE_LANDING_DF = None
CACHE_MERAH_DF = None
DATA_LOCK = threading.Lock()
TERJANGKIT_LOCK = threading.Lock()

# Data bawaan awal (September 2026 sesuai file V1.Daftar Negara Terjangkit)
DEFAULT_TERJANGKIT_2026_09 = {
    "Cambodia": "Flu Burung",
    "Angola": "Yellow Fever",
    "Argentina": "Yellow Fever",
    "Benin": "Yellow Fever",
    "Bolivia": "Yellow Fever",
    "Brazil": "Yellow Fever",
    "Burkina Faso": "Yellow Fever",
    "Burundi": "Yellow Fever",
    "Cameroon": "Yellow Fever",
    "Central African Republic": "Yellow Fever",
    "Chad": "Yellow Fever",
    "Colombia": "Yellow Fever",
    "Costa Rica": "Yellow Fever",
    "Cote d'Ivoire": "Yellow Fever",
    "Democratic Republic of the Congo": "Yellow Fever / Ebola",
    "Ecuador": "Yellow Fever",
    "Equatorial Guinea": "Yellow Fever",
    "Ethiopia": "Yellow Fever",
    "French Guiana": "Yellow Fever",
    "Gabon": "Yellow Fever",
    "Gambia": "Yellow Fever",
    "Ghana": "Yellow Fever",
    "Guinea": "Yellow Fever",
    "Guinea-Bissau": "Yellow Fever",
    "Guyana": "Yellow Fever",
    "Kenya": "Yellow Fever / Ebola",
    "Liberia": "Yellow Fever",
    "Mali": "Yellow Fever",
    "Mauritania": "Yellow Fever",
    "Niger": "Yellow Fever",
    "Nigeria": "Yellow Fever",
    "Panama": "Yellow Fever",
    "Paraguay": "Yellow Fever",
    "Peru": "Yellow Fever",
    "Republic of the Congo": "Yellow Fever",
    "Senegal": "Yellow Fever",
    "Sierra Leone": "Yellow Fever",
    "South Sudan": "Yellow Fever",
    "Sudan": "Yellow Fever",
    "Suriname": "Yellow Fever",
    "Togo": "Yellow Fever",
    "Trinidad and Tobago": "Yellow Fever",
    "Uganda": "Yellow Fever",
    "Venezuela": "Yellow Fever"
}

# Dictionary Alias & Typo Pembersihan Negara
COUNTRY_ALIASES = {
    'philipina': 'Philippines',
    'philippines': 'Philippines',
    'thailan': 'Thailand',
    'thailand': 'Thailand',
    'uea': 'United Arab Emirates',
    'uae': 'United Arab Emirates',
    'hongkong': 'Hong Kong',
    'gbp': 'United Kingdom',
    'uk': 'United Kingdom',
    'united kingdom': 'United Kingdom',
    'usa': 'United States',
    'united states': 'United States',
    'turkey': 'Turkey',
    'turkiye': 'Turkey',
    'czechoslovakia': 'Czech Republic',
    'czech republic': 'Czech Republic'
}


def load_terjangkit_store() -> dict:
    with TERJANGKIT_LOCK:
        if not os.path.exists(TERJANGKIT_JSON_PATH):
            initial_data = {
                "periods": {
                    "2026-09": {
                        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "source": "Default Excel September 2026",
                        "countries": DEFAULT_TERJANGKIT_2026_09
                    }
                }
            }
            with open(TERJANGKIT_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump(initial_data, f, indent=2, ensure_ascii=False)
            return initial_data

        try:
            with open(TERJANGKIT_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "periods" not in data:
                    data = {"periods": {}}
                return data
        except Exception:
            return {"periods": {}}


def save_terjangkit_store(data: dict):
    with TERJANGKIT_LOCK:
        with open(TERJANGKIT_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def get_active_terjangkit_for_date(start_date: Optional[str] = None, end_date: Optional[str] = None):
    store = load_terjangkit_store()
    periods = store.get("periods", {})
    if not periods:
        return "2026-09", DEFAULT_TERJANGKIT_2026_09

    sorted_keys = sorted(periods.keys(), reverse=True)
    target_ym = None

    if end_date:
        try:
            target_ym = pd.to_datetime(end_date).strftime("%Y-%m")
        except Exception:
            pass
    elif start_date:
        try:
            target_ym = pd.to_datetime(start_date).strftime("%Y-%m")
        except Exception:
            pass

    if target_ym and target_ym in periods:
        return target_ym, periods[target_ym].get("countries", {})

    # Fallback ke bulan terdekat sebelumnya atau bulan terbaru yang tersimpan
    if target_ym:
        earlier = [k for k in sorted_keys if k <= target_ym]
        if earlier:
            chosen = earlier[0]
            return chosen, periods[chosen].get("countries", {})

    latest_key = sorted_keys[0]
    return latest_key, periods[latest_key].get("countries", {})


def merge_country_disease(target_dict: dict, country: str, disease: str):
    c_clean = re.sub(r'\s+', ' ', str(country)).strip()
    d_clean = re.sub(r'\s+', ' ', str(disease)).strip()
    if not c_clean or c_clean.lower() in ['nan', '-', 'none', 'nama negara', 'negara']:
        return
    if not d_clean or d_clean.lower() in ['nan', '-', 'none', 'penyakit']:
        d_clean = "Daerah Terjangkit"

    # Cek case-insensitive agar tidak duplikat beda kapital
    existing_key = next((k for k in target_dict.keys() if k.lower() == c_clean.lower()), None)
    if existing_key:
        existing_diseases = [x.strip() for x in target_dict[existing_key].split("/")]
        if not any(d_clean.lower() == ed.lower() for ed in existing_diseases):
            existing_diseases.append(d_clean)
            target_dict[existing_key] = " / ".join(existing_diseases)
    else:
        target_dict[c_clean] = d_clean


def parse_terjangkit_excel(file_bytes: bytes, filename: str) -> dict:
    """
    Membaca file Excel (.xlsx/.xls) atau CSV daftar negara terjangkit.
    Mendukung:
    1) Sheet tabel vertikal 2/3 kolom (No. | Nama Negara | Penyakit)
    2) Sheet pertama dengan tabel berdampingan per penyakit (Flu Burung, Yellow Fever, PENYAKIT EBOLA, dll.)
    """
    result = {}

    if filename.lower().endswith(".csv"):
        df = pd.read_csv(io.BytesIO(file_bytes))
        df.columns = [str(c).strip().lower() for c in df.columns]
        c_col = next((c for c in df.columns if 'negara' in c), None)
        p_col = next((c for c in df.columns if 'penyakit' in c or 'keterangan' in c), None)
        if c_col:
            for _, row in df.iterrows():
                merge_country_disease(result, row[c_col], row[p_col] if p_col else "Daerah Terjangkit")
        return result

    excel_file = pd.ExcelFile(io.BytesIO(file_bytes))

    # PASS 1: Cari sheet yang memiliki kolom 'Nama Negara' dan 'Penyakit' (seperti sheet tabel utama)
    for sheet_name in excel_file.sheet_names:
        raw_df = pd.read_excel(excel_file, sheet_name=sheet_name, header=None)
        for r_idx in range(min(25, len(raw_df))):
            row_vals = [str(v).strip().lower() if pd.notna(v) else "" for v in raw_df.iloc[r_idx].tolist()]
            negara_idx = next((i for i, v in enumerate(row_vals) if v in ['nama negara', 'negara', 'country']), None)
            penyakit_idx = next((i for i, v in enumerate(row_vals) if 'penyakit' in v or 'disease' in v or 'keterangan' in v), None)

            if negara_idx is not None and penyakit_idx is not None:
                for data_r in range(r_idx + 1, len(raw_df)):
                    c_val = raw_df.iat[data_r, negara_idx]
                    p_val = raw_df.iat[data_r, penyakit_idx]
                    if pd.isna(c_val):
                        continue
                    c_str = str(c_val).strip()
                    if c_str.lower() in ['nama negara', 'negara', 'no', 'no.', '']:
                        continue
                    p_str = str(p_val).strip() if pd.notna(p_val) else "Daerah Terjangkit"
                    merge_country_disease(result, c_str, p_str)
                if result:
                    return result

    # PASS 2 (Fallback): Parse blok berdampingan di sheet pertama (Header Penyakit di atas tabel No. | Nama Negara)
    first_df = pd.read_excel(excel_file, sheet_name=excel_file.sheet_names[0], header=None)
    for r_idx in range(len(first_df)):
        for c_idx in range(len(first_df.columns)):
            cell_val = str(first_df.iat[r_idx, c_idx]).strip().lower() if pd.notna(first_df.iat[r_idx, c_idx]) else ""
            if cell_val in ['nama negara', 'negara']:
                # Cari judul penyakit di 1-4 baris di atas kolom ini (atau kolom sebelah kirinya)
                disease_title = "Daerah Terjangkit"
                for back_r in range(1, 5):
                    if r_idx - back_r >= 0:
                        for check_c in [c_idx, max(0, c_idx - 1)]:
                            above_val = first_df.iat[r_idx - back_r, check_c]
                            if pd.notna(above_val):
                                txt = str(above_val).strip()
                                if txt and not txt.lower().startswith("do:") and not txt.lower().startswith("do ") and len(txt) < 60:
                                    disease_title = re.sub(r'^penyakit\s+', '', txt, flags=re.IGNORECASE).strip()
                                    break
                        if disease_title != "Daerah Terjangkit":
                            break

                # Baca daftar negara ke bawah hingga baris kosong
                for data_r in range(r_idx + 1, len(first_df)):
                    val_country = first_df.iat[data_r, c_idx]
                    if pd.isna(val_country) or str(val_country).strip() == "":
                        break
                    merge_country_disease(result, str(val_country), disease_title)

    return result


def process_landing_data(df):
    df.columns = df.columns.str.strip().str.replace('\ufeff', '')
    if 'Tanggal' in df.columns:
        df['parsed_date'] = pd.to_datetime(df['Tanggal'], errors='coerce', dayfirst=True)
    return df


def process_merah_data(df):
    df.columns = df.columns.str.strip().str.replace('\ufeff', '')
    tanggal_col_name = next((c for c in df.columns if 'tanggal' in c.lower() or 'date' in c.lower()), None)
    if tanggal_col_name:
        df['parsed_date'] = pd.to_datetime(df[tanggal_col_name], errors='coerce', dayfirst=True)
        if df['parsed_date'].isna().sum() > len(df) * 0.5:
            df['parsed_date'] = pd.to_datetime(df[tanggal_col_name], errors='coerce', format='mixed')

    if 'Tanggal' in df.columns:
        s = df['Tanggal'].astype(str).str.lower().str.strip()
        indo_m = {'januari': 'jan', 'februari': 'feb', 'maret': 'mar', 'april': 'apr', 'mei': 'may', 'juni': 'jun', 'juli': 'jul', 'agustus': 'aug', 'september': 'sep', 'oktober': 'oct', 'november': 'nov', 'desember': 'dec', 'agu': 'aug', 'okt': 'oct', 'des': 'dec'}
        for id_m, en_m in indo_m.items():
            s = s.str.replace(id_m, en_m, regex=False)
        df['trend_parsed_date'] = pd.to_datetime(s, errors='coerce', dayfirst=True, format='mixed')
        df['trend_year'] = df['trend_parsed_date'].dt.year
        df['trend_month'] = df['trend_parsed_date'].dt.month
    return df


def force_load_all_data():
    global CACHE_LANDING_DF, CACHE_MERAH_DF
    with DATA_LOCK:
        if CACHE_LANDING_DF is not None and CACHE_MERAH_DF is not None:
            return
        try:
            CACHE_LANDING_DF = process_landing_data(pd.read_csv(LANDING_SHEET_URL))
        except Exception:
            CACHE_LANDING_DF = pd.DataFrame()
        try:
            CACHE_MERAH_DF = process_merah_data(pd.read_csv(SHEET_CSV_URL))
        except Exception:
            CACHE_MERAH_DF = pd.DataFrame()


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_terjangkit_store()
    thread = threading.Thread(target=force_load_all_data)
    thread.start()
    yield


app = FastAPI(title="Backend Smartkarkes AI", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_cached_landing_df():
    if CACHE_LANDING_DF is None:
        force_load_all_data()
    return CACHE_LANDING_DF.copy()


def get_cached_merah_df():
    if CACHE_MERAH_DF is None:
        force_load_all_data()
    return CACHE_MERAH_DF.copy()


def update_cache_background():
    global CACHE_LANDING_DF, CACHE_MERAH_DF
    try:
        CACHE_LANDING_DF = process_landing_data(pd.read_csv(LANDING_SHEET_URL))
        CACHE_MERAH_DF = process_merah_data(pd.read_csv(SHEET_CSV_URL))
    except Exception:
        pass


def extract_valid_countries(raw_str: str, valid_countries: set, country_alias_map: dict) -> list:
    if not raw_str or str(raw_str).lower() in ['nan', '-', '']:
        return []

    s = str(raw_str).strip()
    candidates_map = {}

    for vc in valid_countries:
        if vc and str(vc).strip():
            candidates_map[str(vc).strip().lower()] = str(vc).strip()
    for k, v in country_alias_map.items():
        candidates_map[k.lower()] = v

    sorted_keys = sorted(candidates_map.keys(), key=lambda x: len(x), reverse=True)

    s_lower = s.lower()
    delims = [',', ';', '/', '+', '•', '&']
    for d in delims:
        s_lower = s_lower.replace(d, ',')

    parts = [p.strip() for p in s_lower.split(',') if p.strip()]
    extracted = []

    for part in parts:
        if part in candidates_map:
            extracted.append(candidates_map[part])
            continue

        temp_part = part
        found_in_part = []

        for key in sorted_keys:
            if len(key) >= 3 and key in temp_part:
                canonical = candidates_map[key]
                if canonical not in found_in_part:
                    found_in_part.append(canonical)
                temp_part = temp_part.replace(key, " ")

        if found_in_part:
            extracted.extend(found_in_part)
        else:
            extracted.append(part.title())

    res = []
    for c in extracted:
        if c not in res:
            res.append(c)
    return res


# ==============================================================================
# ENDPOINT KELOLA NEGARA TERJANGKIT (BULANAN: UPLOAD EXCEL & INPUT MANUAL)
# ==============================================================================

class ManualTerjangkitRequest(BaseModel):
    periode: str          # Format: "YYYY-MM", misal "2026-09"
    negara: str
    penyakit: str
    mode: str = "merge"   # "merge" (gabung penyakit jika negara sudah ada) atau "overwrite"


class CopyTerjangkitRequest(BaseModel):
    from_periode: str
    to_periode: str


@app.get("/api/terjangkit")
def get_terjangkit_list(periode: Optional[str] = None):
    store = load_terjangkit_store()
    periods = store.get("periods", {})
    sorted_periods = sorted(periods.keys(), reverse=True)

    selected_periode = periode if (periode and periode in periods) else (sorted_periods[0] if sorted_periods else "2026-09")
    period_obj = periods.get(selected_periode, {"updated_at": "-", "source": "-", "countries": {}})
    countries_dict = period_obj.get("countries", {})

    items = [
        {"negara": k, "penyakit": v}
        for k, v in sorted(countries_dict.items(), key=lambda x: x[0].lower())
    ]

    # Sertakan juga referensi daftar negara valid dari landing sheet untuk autocomplete
    known_countries = set(countries_dict.keys())
    df_landing = get_cached_landing_df()
    if not df_landing.empty:
        nc = next((c for c in df_landing.columns if 'negara' in c.lower()), None)
        if nc:
            for val in df_landing[nc].dropna().astype(str).str.strip().unique():
                if val and val.lower() not in ['nan', '-', '']:
                    known_countries.add(val)

    return {
        "selected_periode": selected_periode,
        "available_periods": [
            {
                "periode": p,
                "count": len(periods[p].get("countries", {})),
                "updated_at": periods[p].get("updated_at", "-"),
                "source": periods[p].get("source", "-")
            }
            for p in sorted_periods
        ],
        "updated_at": period_obj.get("updated_at", "-"),
        "source": period_obj.get("source", "-"),
        "countries_map": countries_dict,
        "items": items,
        "known_countries": sorted(list(known_countries))
    }


@app.post("/api/terjangkit/upload")
async def upload_terjangkit_excel(
    file: UploadFile = File(...),
    periode: str = Form(...),
    mode: str = Form("replace")
):
    if not re.match(r"^\d{4}-\d{2}$", periode):
        raise HTTPException(status_code=400, detail="Format periode harus YYYY-MM (contoh: 2026-09)")

    content = await file.read()
    try:
        parsed_map = parse_terjangkit_excel(content, file.filename or "upload.xlsx")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Gagal membaca file Excel: {str(e)}")

    if not parsed_map:
        raise HTTPException(status_code=400, detail="Tidak ditemukan data negara pada file Excel. Pastikan terdapat kolom 'Nama Negara' dan 'Penyakit'.")

    store = load_terjangkit_store()
    periods = store.setdefault("periods", {})

    if mode == "merge" and periode in periods:
        existing_map = periods[periode].get("countries", {}).copy()
        for c, p in parsed_map.items():
            merge_country_disease(existing_map, c, p)
        final_map = existing_map
    else:
        final_map = parsed_map

    periods[periode] = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": f"Upload Excel ({file.filename})",
        "countries": final_map
    }
    save_terjangkit_store(store)

    return {
        "status": "success",
        "message": f"Berhasil menyimpan {len(final_map)} negara terjangkit untuk periode {periode}.",
        "periode": periode,
        "total_countries": len(final_map)
    }


@app.post("/api/terjangkit/manual")
def upsert_terjangkit_manual(req: ManualTerjangkitRequest):
    if not re.match(r"^\d{4}-\d{2}$", req.periode):
        raise HTTPException(status_code=400, detail="Format periode harus YYYY-MM")
    if not req.negara.strip():
        raise HTTPException(status_code=400, detail="Nama negara wajib diisi")

    store = load_terjangkit_store()
    periods = store.setdefault("periods", {})

    if req.periode not in periods:
        periods[req.periode] = {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "Input Manual Petugas BBKK",
            "countries": {}
        }

    countries_map = periods[req.periode]["countries"]
    if req.mode == "overwrite":
        existing_key = next((k for k in countries_map.keys() if k.lower() == req.negara.strip().lower()), None)
        if existing_key:
            del countries_map[existing_key]
        countries_map[req.negara.strip()] = req.penyakit.strip() or "Daerah Terjangkit"
    else:
        merge_country_disease(countries_map, req.negara, req.penyakit)

    periods[req.periode]["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    periods[req.periode]["source"] = "Update Manual Petugas BBKK"
    save_terjangkit_store(store)

    return {
        "status": "success",
        "message": f"Data {req.negara.strip()} berhasil disimpan pada periode {req.periode}."
    }


@app.post("/api/terjangkit/copy")
def copy_terjangkit_period(req: CopyTerjangkitRequest):
    store = load_terjangkit_store()
    periods = store.get("periods", {})
    if req.from_periode not in periods:
        raise HTTPException(status_code=404, detail="Periode sumber tidak ditemukan.")

    copied_countries = periods[req.from_periode].get("countries", {}).copy()
    periods[req.to_periode] = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": f"Salinan dari {req.from_periode}",
        "countries": copied_countries
    }
    save_terjangkit_store(store)
    return {"status": "success", "message": f"Berhasil menyalin {len(copied_countries)} negara ke periode {req.to_periode}."}


@app.delete("/api/terjangkit/manual")
def delete_terjangkit_country(periode: str, negara: str):
    store = load_terjangkit_store()
    periods = store.get("periods", {})
    if periode not in periods:
        raise HTTPException(status_code=404, detail="Periode tidak ditemukan.")

    countries_map = periods[periode].get("countries", {})
    target_key = next((k for k in countries_map.keys() if k.lower() == negara.strip().lower()), None)
    if not target_key:
        raise HTTPException(status_code=404, detail="Negara tidak ditemukan pada periode ini.")

    del countries_map[target_key]
    periods[periode]["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_terjangkit_store(store)

    return {"status": "success", "message": f"{target_key} dihapus dari periode {periode}."}

@app.delete("/api/terjangkit/periode")
def delete_terjangkit_periode(periode: str):
    store = load_terjangkit_store()
    periods = store.get("periods", {})
    if periode not in periods:
        raise HTTPException(status_code=404, detail=f"Periode {periode} tidak ditemukan.")

    del periods[periode]
    save_terjangkit_store(store)
    return {"status": "success", "message": f"Periode {periode} berhasil dihapus permanen."}

# ==============================================================================
# ENDPOINT UTAMA DASHBOARD
# ==============================================================================

@app.get("/api/sync")
def sync_data(background_tasks: BackgroundTasks):
    background_tasks.add_task(update_cache_background)
    return {"status": "success", "message": "Sinkronisasi berjalan di belakang layar."}


@app.get("/api/landing")
def get_landing_stats(start_date: Optional[str] = None, end_date: Optional[str] = None, negara: Optional[str] = None):
    try:
        df_landing = get_cached_landing_df()
        if df_landing.empty:
            return {"error": "Data kosong"}

        if 'parsed_date' in df_landing.columns and start_date and end_date:
            s_date = pd.to_datetime(start_date)
            e_date = pd.to_datetime(end_date) + pd.Timedelta(days=1, microseconds=-1)
            df_landing = df_landing[(df_landing['parsed_date'] >= s_date) & (df_landing['parsed_date'] <= e_date)]

        total_ppln = int(df_landing['Total Isian'].sum()) if 'Total Isian' in df_landing.columns else 0
        merah = int(df_landing['Berisiko'].sum()) if 'Berisiko' in df_landing.columns else 0
        kuning = int(df_landing['Daerah Terjangkit'].sum()) if 'Daerah Terjangkit' in df_landing.columns else 0
        hijau = int(df_landing['Tidak Berisiko'].sum()) if 'Tidak Berisiko' in df_landing.columns else 0

        landing_total_map = {}
        valid_countries = set()
        negara_col = next((c for c in df_landing.columns if 'negara' in c.lower()), None)
        negara_data = []

        if negara_col:
            df_landing[negara_col] = df_landing[negara_col].astype(str).str.strip()
            df_negara = df_landing.groupby(negara_col).agg({'Total Isian': 'sum', 'Berisiko': 'sum', 'Daerah Terjangkit': 'sum', 'Tidak Berisiko': 'sum'}).reset_index()
            df_negara = df_negara.sort_values(by='Total Isian', ascending=False)

            for _, row in df_negara.iterrows():
                n_name = str(row[negara_col])
                t_val = int(row['Total Isian']) if 'Total Isian' in row else 0
                if n_name and n_name.lower() not in ['nan', '-', '']:
                    landing_total_map[n_name.lower()] = t_val
                    valid_countries.add(n_name)
                    negara_data.append({
                        "negara": n_name,
                        "total": t_val,
                        "merah": int(row['Berisiko']) if 'Berisiko' in row else 0,
                        "kuning": int(row['Daerah Terjangkit']) if 'Daerah Terjangkit' in row else 0,
                        "hijau": int(row['Tidak Berisiko']) if 'Tidak Berisiko' in row else 0
                    })

        interpretasi = f"Berdasarkan rekapitulasi data Pelaku Perjalanan Luar Negeri (PPLN), tercatat akumulasi sebanyak **{total_ppln:,}** total kedatangan."

        df_merah = get_cached_merah_df()
        n_col = 'Riwayat perjalanan1' if 'Riwayat perjalanan1' in df_merah.columns else df_merah.columns[3]

        # TREN BULANAN
        tren_2026, tren_2025 = [0]*12, [0]*12
        df_trend = df_merah.copy()
        if negara and negara != "Semua Negara":
            df_trend = df_trend[df_trend[n_col].astype(str).str.contains(negara, case=False, na=False)]

        if 'trend_year' in df_trend.columns:
            df_2026 = df_trend[df_trend['trend_year'] == 2026]
            if not df_2026.empty:
                m_2026 = df_2026.groupby('trend_month').size()
                for m in range(1, 13):
                    tren_2026[m-1] = int(m_2026.get(m, 0))

            df_2025 = df_trend[df_trend['trend_year'] == 2025]
            if not df_2025.empty:
                m_2025 = df_2025.groupby('trend_month').size()
                for m in range(1, 13):
                    tren_2025[m-1] = int(m_2025.get(m, 0))

        # RINCIAN BULANAN (BERGEJALA PER 100k)
        rincian_bulanan_data = []
        df_rincian = df_landing.copy()

        if negara and negara != "Semua Negara" and negara_col:
            df_rincian = df_rincian[df_rincian[negara_col].astype(str).str.strip().str.lower() == negara.lower()]

        if 'parsed_date' in df_rincian.columns and 'Total Isian' in df_rincian.columns and 'Berisiko' in df_rincian.columns:
            df_monthly = df_rincian.groupby(df_rincian['parsed_date'].dt.to_period('M')).agg({'Total Isian': 'sum', 'Berisiko': 'sum'}).reset_index()
            for _, row in df_monthly.iterrows():
                p_date = row['parsed_date']
                if pd.isna(p_date):
                    continue
                tot_is, mer_is = int(row['Total Isian']), int(row['Berisiko'])
                rate_100k = round((mer_is / tot_is * 100000), 2) if tot_is > 0 else 0.0
                pct_val = round((mer_is / tot_is * 100), 2) if tot_is > 0 else 0.0
                rincian_bulanan_data.append({
                    "tahun": str(p_date.year),
                    "bulan": p_date.strftime('%B'),
                    "tahun_bulan": p_date.strftime('%B %Y'),
                    "total_riwayat": tot_is,
                    "bergejala": mer_is,
                    "per_100k": rate_100k,
                    "persen": pct_val
                })

        # TABEL VERIFIKASI (PEMBERSIHAN OTOMATIS)
        verif_data = []
        h_col = next((c for c in df_merah.columns if 'verifikasi' in c.lower() or 'hasil' in c.lower()), None)
        diag_col = next((c for c in df_merah.columns if 'diagnosis' in c.lower()), None)

        if n_col and h_col:
            country_stats = {}
            df_merah_verif = df_merah.copy()
            if 'parsed_date' in df_merah_verif.columns and start_date and end_date:
                s_date = pd.to_datetime(start_date)
                e_date = pd.to_datetime(end_date) + pd.Timedelta(days=1, microseconds=-1)
                df_merah_verif = df_merah_verif[(df_merah_verif['parsed_date'] >= s_date) & (df_merah_verif['parsed_date'] <= e_date)]

            for _, row in df_merah_verif.iterrows():
                raw_countries_str = str(row[n_col])
                hasil = str(row[h_col]).strip() if pd.notna(row[h_col]) else ""
                diag = str(row[diag_col]).strip() if diag_col and pd.notna(row[diag_col]) else ""

                parsed_countries = extract_valid_countries(raw_countries_str, valid_countries, COUNTRY_ALIASES)

                for c in parsed_countries:
                    if not c or c.lower() in ['nan', '-', '']:
                        continue

                    if c not in country_stats:
                        country_stats[c] = {
                            "verif_merah": 0, "terverifikasi": 0,
                            "tidak_respon": 0, "tidak_dilakukan": 0, "diagnoses": {}
                        }

                    country_stats[c]["verif_merah"] += 1

                    hasil_low = hasil.lower()
                    if 'tidak respons' in hasil_low:
                        country_stats[c]["tidak_respon"] += 1
                    elif 'tidak dilakukan' in hasil_low:
                        country_stats[c]["tidak_dilakukan"] += 1
                    else:
                        country_stats[c]["terverifikasi"] += 1

                    if diag and diag.lower() not in ['-', '', 'a', 'nan']:
                        country_stats[c]["diagnoses"][diag] = country_stats[c]["diagnoses"].get(diag, 0) + 1

            for c, stats in country_stats.items():
                tot_riwayat = landing_total_map.get(c.lower(), stats["verif_merah"])

                top_diag = "-"
                if stats["diagnoses"]:
                    sorted_diags = sorted(stats["diagnoses"].items(), key=lambda item: item[1], reverse=True)
                    top_n_diags = sorted_diags[:2]
                    diag_strings = [f"{diag_name} ({count})" for diag_name, count in top_n_diags]
                    top_diag = "; ".join(diag_strings)

                cakupan = round((stats["terverifikasi"] / stats["verif_merah"]) * 100, 2) if stats["verif_merah"] > 0 else 0.0

                verif_data.append({
                    "negara": c,
                    "total_riwayat": tot_riwayat,
                    "verif_merah": stats["verif_merah"],
                    "terverifikasi": stats["terverifikasi"],
                    "tidak_respon": stats["tidak_respon"],
                    "tidak_dilakukan": stats["tidak_dilakukan"],
                    "cakupan_pct": cakupan,
                    "diagnosis": top_diag
                })

        active_terjangkit_periode, active_terjangkit_map = get_active_terjangkit_for_date(start_date, end_date)

        return {
            "total_ppln": total_ppln, "merah": merah, "kuning": kuning, "hijau": hijau,
            "negara": negara_data, "verif": verif_data, "tren_2026": tren_2026,
            "tren_2025": tren_2025, "rincian_bulanan": rincian_bulanan_data,
            "terjangkit_periode": active_terjangkit_periode,
            "terjangkit_map": active_terjangkit_map,
            "interpretasi": interpretasi
        }
    except Exception as e:
        return {"error": str(e)}


class FilterRequest(BaseModel):
    gejala: str | None = None
    diagnosis: str | None = None
    periode: str | None = "semua"
    negara: str | None = "Semua Negara"


@app.post("/api/stats")
def get_filtered_stats(req: FilterRequest):
    df_global = get_cached_merah_df()
    if df_global.empty:
        return {"error": "Data kosong"}

    tanggal_terakhir = "28/10/2026"
    if 'Tanggal' in df_global.columns:
        val_tgl = df_global['Tanggal'].dropna()
        if not val_tgl.empty:
            tanggal_terakhir = str(val_tgl.iloc[-1])

    gejala_col_name = next((c for c in df_global.columns if 'gejala' in c.lower() or 'daftar gejala' in c.lower()), None)
    diag_col_name = next((c for c in df_global.columns if 'diagnosis' in c.lower()), None)

    fixed_top_gejala, fixed_top_diag = [], []
    if gejala_col_name:
        all_g = []
        for val in df_global[gejala_col_name].dropna():
            cleaned_str = str(val).replace('â€¢', ',').replace('•', ',').replace(';', ',')
            for item in cleaned_str.split(','):
                ci = item.strip().strip('"').strip("'").title()
                if ci and len(ci) > 2 and ci != '-':
                    all_g.append(ci)
        fixed_top_gejala = [k for k, v in pd.Series(all_g).value_counts().head(10).items()]

    if diag_col_name:
        d_series = df_global[diag_col_name].dropna().astype(str).str.strip()
        d_series = d_series[~d_series.isin(['-', '', 'A', 'a', 'nan', 'NaN'])]
        fixed_top_diag = [k for k, v in d_series.value_counts().head(10).items()]

    df_filtered = df_global.copy()
    n_col_stats = next((c for c in df_filtered.columns if 'riwayat' in c.lower() or 'negara' in c.lower()), None)
    if not n_col_stats and len(df_filtered.columns) > 3:
        n_col_stats = df_filtered.columns[3]

    if req.negara and req.negara != "Semua Negara":
        if n_col_stats:
            df_filtered = df_filtered[df_filtered[n_col_stats].astype(str).str.contains(req.negara, case=False, na=False)]

    if req.periode and req.periode != "semua" and 'parsed_date' in df_filtered.columns:
        if req.periode == "this_month":
            max_date = df_filtered['parsed_date'].max()
            target_bulan = max_date.month if pd.notna(max_date) else 10
            target_tahun = max_date.year if pd.notna(max_date) else 2026
            df_filtered = df_filtered[(df_filtered['parsed_date'].dt.month == target_bulan) & (df_filtered['parsed_date'].dt.year == target_tahun)]
        elif req.periode.isdigit():
            df_filtered = df_filtered[df_filtered['parsed_date'].dt.year == int(req.periode)]

    if req.gejala and gejala_col_name:
        df_filtered = df_filtered[df_filtered[gejala_col_name].fillna('').str.contains(req.gejala, case=False, na=False)]
    if req.diagnosis and diag_col_name:
        df_filtered = df_filtered[df_filtered[diag_col_name].fillna('').str.contains(req.diagnosis, case=False, na=False)]

    total_merah = len(df_filtered)

    terverifikasi, tidak_respon, tidak_divalidasi = 0, 0, 0
    verif_counts = {}
    h_col = next((c for c in df_filtered.columns if 'verifikasi' in c.lower() or 'hasil' in c.lower()), None)
    if h_col:
        verif_counts = df_filtered[h_col].dropna().value_counts().to_dict()
        tidak_respon = int(verif_counts.get('Penumpang tidak respons', 0))
        tidak_divalidasi = int(verif_counts.get('Tidak dilakukan verifikasi', 0))
        terverifikasi = max(0, int(total_merah - tidak_divalidasi - tidak_respon))

    cakupan_pct = round((terverifikasi / total_merah * 100), 2) if total_merah > 0 else 0.0

    top_gejala_data = []
    if gejala_col_name:
        for g_item in fixed_top_gejala:
            count = int(df_filtered[gejala_col_name].fillna('').str.contains(g_item, case=False, na=False).sum())
            top_gejala_data.append({"val": g_item, "count": count})
        top_gejala_data.sort(key=lambda x: x['count'], reverse=True)

    top_diag_data = []
    if diag_col_name:
        base_total = len(df_filtered) if len(df_filtered) > 0 else 1
        for d_item in fixed_top_diag:
            count = int((df_filtered[diag_col_name].dropna().astype(str).str.strip() == d_item).sum())
            top_diag_data.append({"val": d_item, "count": count, "pct": round((count / base_total) * 100, 2)})
        top_diag_data.sort(key=lambda x: x['count'], reverse=True)

    top_negara_data = []
    if n_col_stats:
        df_landing_ref = get_cached_landing_df()
        valid_set = set()
        if not df_landing_ref.empty:
            nc = next((c for c in df_landing_ref.columns if 'negara' in c.lower()), None)
            if nc:
                valid_set = set(df_landing_ref[nc].dropna().astype(str).str.strip())

        all_n = []
        for val in df_filtered[n_col_stats].dropna():
            extracted = extract_valid_countries(val, valid_set, COUNTRY_ALIASES)
            all_n.extend(extracted)

        fixed_top_negara = pd.Series(all_n).value_counts().head(10)
        for k, v in fixed_top_negara.items():
            top_negara_data.append({"negara": k, "verif_merah": int(v)})

    n_txt = f"dari negara **{req.negara}**" if req.negara and req.negara != "Semua Negara" else "secara keseluruhan"
    pembacaan = f"Berdasarkan pemantauan data real-time Google Sheets {n_txt} untuk periode **{req.periode}**, tercatat akumulasi sebanyak **{total_merah:,} kasus PPLN**."

    return {
        "tanggal_terakhir": tanggal_terakhir, "total_merah": total_merah, "terverifikasi": terverifikasi,
        "tidak_respon": tidak_respon, "tidak_divalidasi": tidak_divalidasi, "cakupan_pct": cakupan_pct,
        "status_tindak_lanjut": verif_counts, "top_gejala": top_gejala_data, "top_diagnosis": top_diag_data,
        "top_negara": top_negara_data,
        "pembacaan": pembacaan, "rekomendasi": []
    }