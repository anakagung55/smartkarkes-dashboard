from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pandas as pd
import os
import time
import threading
from dotenv import load_dotenv
from typing import Optional
from contextlib import asynccontextmanager

load_dotenv()

SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/1fqp6xsIxYOkF-SWaEUBae1pW7BKu9RMiI7xyjOuvB5o/gviz/tq?tqx=out:csv&sheet=MERAH"
LANDING_SHEET_URL = "https://docs.google.com/spreadsheets/d/1p-QhV_ZgbLqKy77DWtdwCsUVcdOPauA9UMfMeb8c7NQ/gviz/tq?tqx=out:csv&sheet=Sheet1"

CACHE_LANDING_DF = None
CACHE_MERAH_DF = None
DATA_LOCK = threading.Lock()

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
        if CACHE_LANDING_DF is not None and CACHE_MERAH_DF is not None: return
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
    if CACHE_LANDING_DF is None: force_load_all_data()
    return CACHE_LANDING_DF.copy()

def get_cached_merah_df():
    if CACHE_MERAH_DF is None: force_load_all_data()
    return CACHE_MERAH_DF.copy()

def update_cache_background():
    global CACHE_LANDING_DF, CACHE_MERAH_DF
    try:
        CACHE_LANDING_DF = process_landing_data(pd.read_csv(LANDING_SHEET_URL))
        CACHE_MERAH_DF = process_merah_data(pd.read_csv(SHEET_CSV_URL))
    except Exception as e:
        pass

@app.get("/api/sync")
def sync_data(background_tasks: BackgroundTasks):
    background_tasks.add_task(update_cache_background)
    return {"status": "success", "message": "Sinkronisasi berjalan di belakang layar."}

@app.get("/api/landing")
def get_landing_stats(start_date: Optional[str] = None, end_date: Optional[str] = None, negara: Optional[str] = None):
    try:
        df_landing = get_cached_landing_df()
        if df_landing.empty: return {"error": "Data kosong"}

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
                    negara_data.append({"negara": n_name, "total": t_val, "merah": int(row['Berisiko']) if 'Berisiko' in row else 0, "kuning": int(row['Daerah Terjangkit']) if 'Daerah Terjangkit' in row else 0, "hijau": int(row['Tidak Berisiko']) if 'Tidak Berisiko' in row else 0})

        top_negara_name = negara_data[0]['negara'] if negara_data else '-'
        interpretasi = f"Berdasarkan rekapitulasi data Pelaku Perjalanan Luar Negeri (PPLN), tercatat akumulasi sebanyak **{total_ppln:,}** total kedatangan."

        df_merah = get_cached_merah_df()
        n_col = 'Riwayat perjalanan1' if 'Riwayat perjalanan1' in df_merah.columns else df_merah.columns[3]
        
        # PERBAIKAN: TREN BULANAN
        tren_2026, tren_2025 = [0]*12, [0]*12
        df_trend = df_merah.copy()
        if negara and negara != "Semua Negara":
            df_trend = df_trend[df_trend[n_col].astype(str).str.contains(negara, case=False, na=False)]

        if 'trend_year' in df_trend.columns:
            df_2026 = df_trend[df_trend['trend_year'] == 2026]
            if not df_2026.empty:
                m_2026 = df_2026.groupby('trend_month').size()
                for m in range(1, 13): tren_2026[m-1] = int(m_2026.get(m, 0))

            df_2025 = df_trend[df_trend['trend_year'] == 2025]
            if not df_2025.empty:
                m_2025 = df_2025.groupby('trend_month').size()
                for m in range(1, 13): tren_2025[m-1] = int(m_2025.get(m, 0))

        # PERBAIKAN FATAL: RINCIAN BULANAN (BERGEJALA PER 100k)
        rincian_bulanan_data = []
        df_rincian = df_landing.copy()
        
        if negara and negara != "Semua Negara" and negara_col:
            # Gunakan exact match & ubah paksa ke String agar tidak gagal jika ada cell kosong
            df_rincian = df_rincian[df_rincian[negara_col].astype(str).str.strip().str.lower() == negara.lower()]

        if 'parsed_date' in df_rincian.columns and 'Total Isian' in df_rincian.columns and 'Berisiko' in df_rincian.columns:
            df_monthly = df_rincian.groupby(df_rincian['parsed_date'].dt.to_period('M')).agg({'Total Isian': 'sum', 'Berisiko': 'sum'}).reset_index()
            for _, row in df_monthly.iterrows():
                p_date = row['parsed_date']
                if pd.isna(p_date): continue
                tot_is, mer_is = int(row['Total Isian']), int(row['Berisiko'])
                rate_100k = round((mer_is / tot_is * 100000), 2) if tot_is > 0 else 0.0
                pct_val = round((mer_is / tot_is * 100), 2) if tot_is > 0 else 0.0
                rincian_bulanan_data.append({"tahun": str(p_date.year), "bulan": p_date.strftime('%B'), "tahun_bulan": p_date.strftime('%B %Y'), "total_riwayat": tot_is, "bergejala": mer_is, "per_100k": rate_100k, "persen": pct_val})

        # +++ TAMBAHKAN BLOK INI UNTUK MENGHITUNG DATA TABEL VERIFIKASI +++
        verif_data = []
        h_col = next((c for c in df_merah.columns if 'verifikasi' in c.lower() or 'hasil' in c.lower()), None)
        diag_col = next((c for c in df_merah.columns if 'diagnosis' in c.lower()), None)
        
        if n_col and h_col:
            country_stats = {}
            # Filter df_merah untuk tabel verifikasi agar sesuai rentang tanggal (jika ada)
            df_merah_verif = df_merah.copy()
            if 'parsed_date' in df_merah_verif.columns and start_date and end_date:
                s_date = pd.to_datetime(start_date)
                e_date = pd.to_datetime(end_date) + pd.Timedelta(days=1, microseconds=-1)
                df_merah_verif = df_merah_verif[(df_merah_verif['parsed_date'] >= s_date) & (df_merah_verif['parsed_date'] <= e_date)]

            for _, row in df_merah_verif.iterrows():
                countries_str = str(row[n_col]).replace(';', ',').replace('•', ',')
                hasil = str(row[h_col]).strip() if pd.notna(row[h_col]) else ""
                diag = str(row[diag_col]).strip() if diag_col and pd.notna(row[diag_col]) else ""
                
                for c in countries_str.split(','):
                    c = c.strip().title()
                    if not c or c.lower() in ['nan', '-', '']: continue
                        
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
                # Ambil total_riwayat dari dictionary landing_total_map yg sudah kamu buat di atas
                tot_riwayat = landing_total_map.get(c.lower(), stats["verif_merah"])
                
                # --- PERBAIKAN FORMAT DIAGNOSIS ---
                top_diag = "-"
                if stats["diagnoses"]:
                    # Urutkan diagnosis berdasarkan jumlah (terbanyak ke sedikit)
                    sorted_diags = sorted(stats["diagnoses"].items(), key=lambda item: item[1], reverse=True)
                    
                    # Ambil 2 diagnosis teratas untuk ditampilkan (bisa disesuaikan jumlahnya)
                    top_n_diags = sorted_diags[:2] 
                    
                    # Format menjadi string: "ISPA (287); Cough (70)"
                    diag_strings = [f"{diag_name} ({count})" for diag_name, count in top_n_diags]
                    top_diag = "; ".join(diag_strings)
                # ----------------------------------
                    
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
        # +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

        return {
            "total_ppln": total_ppln, "merah": merah, "kuning": kuning, "hijau": hijau,
            "negara": negara_data, "verif": verif_data, "tren_2026": tren_2026,
            "tren_2025": tren_2025, "rincian_bulanan": rincian_bulanan_data, "interpretasi": interpretasi
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
    if df_global.empty: return {"error": "Data kosong"}

    tanggal_terakhir = "28/10/2026"
    if 'Tanggal' in df_global.columns:
        val_tgl = df_global['Tanggal'].dropna()
        if not val_tgl.empty: tanggal_terakhir = str(val_tgl.iloc[-1])

    gejala_col_name = next((c for c in df_global.columns if 'gejala' in c.lower() or 'daftar gejala' in c.lower()), None)
    diag_col_name = next((c for c in df_global.columns if 'diagnosis' in c.lower()), None)

    fixed_top_gejala, fixed_top_diag = [], []
    if gejala_col_name:
        all_g = []
        for val in df_global[gejala_col_name].dropna():
            cleaned_str = str(val).replace('â€¢', ',').replace('•', ',').replace(';', ',')
            for item in cleaned_str.split(','):
                ci = item.strip().strip('"').strip("'").title()
                if ci and len(ci) > 2 and ci != '-': all_g.append(ci)
        fixed_top_gejala = [k for k, v in pd.Series(all_g).value_counts().head(10).items()]

    if diag_col_name:
        d_series = df_global[diag_col_name].dropna().astype(str).str.strip()
        d_series = d_series[~d_series.isin(['-', '', 'A', 'a', 'nan', 'NaN'])]
        fixed_top_diag = [k for k, v in d_series.value_counts().head(10).items()]

    df_filtered = df_global.copy()

    # PERBAIKAN FATAL: FILTER DASHBOARD STATS (BAWAH)
    # Keluarkan deklarasi n_col_stats agar bisa dipakai untuk menghitung Top Negara nanti
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

    top_g_name = top_gejala_data[0]['val'] if top_gejala_data and top_gejala_data[0]['count'] > 0 else 'Tidak ada'
    top_d_name = top_diag_data[0]['val'] if top_diag_data and top_diag_data[0]['count'] > 0 else 'Tidak ada'

    # +++ TAMBAHKAN BLOK INI UNTUK MENGHITUNG TOP 10 NEGARA DINAMIS +++
    top_negara_data = []
    if n_col_stats:
        all_n = []
        for val in df_filtered[n_col_stats].dropna():
            cleaned_str = str(val).replace(';', ',') # Pisahkan jika ada koma/titik koma
            for item in cleaned_str.split(','):
                ci = item.strip().title()
                if ci and len(ci) > 2 and ci != '-': 
                    all_n.append(ci)
        
        # Ambil 10 teratas
        fixed_top_negara = pd.Series(all_n).value_counts().head(10)
        for k, v in fixed_top_negara.items():
            top_negara_data.append({"negara": k, "verif_merah": int(v)})
    # ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

    n_txt = f"dari negara **{req.negara}**" if req.negara and req.negara != "Semua Negara" else "secara keseluruhan"
    pembacaan = f"Berdasarkan pemantauan data real-time Google Sheets {n_txt} untuk periode **{req.periode}**, tercatat akumulasi sebanyak **{total_merah:,} kasus PPLN**."

    return {
        "tanggal_terakhir": tanggal_terakhir, "total_merah": total_merah, "terverifikasi": terverifikasi,
        "tidak_respon": tidak_respon, "tidak_divalidasi": tidak_divalidasi, "cakupan_pct": cakupan_pct,
        "status_tindak_lanjut": verif_counts, "top_gejala": top_gejala_data, "top_diagnosis": top_diag_data,
        "top_negara": top_negara_data, # <--- PASTIKAN KEY INI DITAMBAHKAN
        "pembacaan": pembacaan, "rekomendasi": []
    }