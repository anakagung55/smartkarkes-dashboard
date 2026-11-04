from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pandas as pd
import os
from dotenv import load_dotenv
from typing import Optional

load_dotenv()

app = FastAPI(title="Backend Smartkarkes AI - Realtime Sheets")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/1fqp6xsIxYOkF-SWaEUBae1pW7BKu9RMiI7xyjOuvB5o/gviz/tq?tqx=out:csv&sheet=MERAH"
LANDING_SHEET_URL = "https://docs.google.com/spreadsheets/d/1p-QhV_ZgbLqKy77DWtdwCsUVcdOPauA9UMfMeb8c7NQ/gviz/tq?tqx=out:csv&sheet=Sheet1"

CACHE_LANDING_DF = None

def get_cached_landing_df(force_reload: bool = False):
    global CACHE_LANDING_DF
    if CACHE_LANDING_DF is None or force_reload:
        try:
            print("🔄 Sedang mendownload ulang data dari Google Sheets (Landing)...")
            df = pd.read_csv(LANDING_SHEET_URL)
            df.columns = df.columns.str.strip().str.replace('\ufeff', '')
            CACHE_LANDING_DF = df
        except Exception as e:
            print(f"⚠️ Gagal load sheets: {e}")
            if os.path.exists("data/landing.csv"):
                CACHE_LANDING_DF = pd.read_csv("data/landing.csv")
                CACHE_LANDING_DF.columns = CACHE_LANDING_DF.columns.str.strip().str.replace('\ufeff', '')
            else:
                CACHE_LANDING_DF = pd.DataFrame()
    return CACHE_LANDING_DF.copy()

def load_data_from_sheets():
    try:
        df = pd.read_csv(SHEET_CSV_URL)
        df.columns = df.columns.str.strip().str.replace('\ufeff', '')
        return df
    except Exception as e:
        print(f"⚠️ Gagal memuat data merah dari Google Sheets: {e}")
        if os.path.exists("data/data_merah.csv"):
            df = pd.read_csv("data/data_merah.csv")
            df.columns = df.columns.str.strip().str.replace('\ufeff', '')
            return df
        return pd.DataFrame()

@app.get("/api/sync")
def sync_data():
    df = get_cached_landing_df(force_reload=True)
    return {"status": "success", "total_rows": len(df)}

@app.get("/api/landing")
def get_landing_stats(start_date: Optional[str] = None, end_date: Optional[str] = None, negara: Optional[str] = None):
    try:
        df_landing = get_cached_landing_df()
        if df_landing.empty:
            return {"error": "Data kosong"}

        if 'Tanggal' in df_landing.columns:
            df_landing['parsed_date'] = pd.to_datetime(df_landing['Tanggal'], errors='coerce', dayfirst=True)
            if start_date and end_date:
                s_date = pd.to_datetime(start_date).normalize()
                e_date = pd.to_datetime(end_date).normalize()
                df_landing = df_landing[
                    (df_landing['parsed_date'].dt.normalize() >= s_date) & 
                    (df_landing['parsed_date'].dt.normalize() <= e_date)
                ]

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
            df_negara = df_landing.groupby(negara_col).agg({
                'Total Isian': 'sum',
                'Berisiko': 'sum',
                'Daerah Terjangkit': 'sum',
                'Tidak Berisiko': 'sum'
            }).reset_index()
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

        top_negara_name = negara_data[0]['negara'] if negara_data else '-'
        interpretasi = (
            f"Berdasarkan rekapitulasi data Pelaku Perjalanan Luar Negeri (PPLN), tercatat akumulasi sebanyak **{total_ppln:,}** "
            f"total kedatangan. Mayoritas penumpang berada pada status **Hijau / Tidak Berisiko** "
            f"sebanyak **{hijau:,}** orang. Namun, terdapat indikasi **{merah:,} penumpang berstatus Merah (Berisiko)** yang "
            f"memerlukan verifikasi lanjutan, serta **{kuning:,}** penumpang dari area Daerah Terjangkit (Kuning). "
            f"Negara penyumbang kedatangan terbesar saat ini adalah **{top_negara_name}**."
        )

        sorted_valid_countries = sorted(list(valid_countries), key=len, reverse=True)

        verif_raw_agg = {}
        try:
            df_merah = load_data_from_sheets()
            n_col = 'Riwayat perjalanan1' if 'Riwayat perjalanan1' in df_merah.columns else df_merah.columns[3]
            h_col = next((c for c in df_merah.columns if 'verifikasi' in c.lower() or 'hasil' in c.lower()), None)
            d_col = next((c for c in df_merah.columns if 'diag' in c.lower() or 'sakit' in c.lower() or 'keluhan' in c.lower()), None)

            if n_col:
                for _, r in df_merah.iterrows():
                    raw_negara = str(r.get(n_col, '')).strip()
                    if not raw_negara or raw_negara.lower() in ['nan', '-', '', 'nat']: continue
                    
                    status_v = str(r.get(h_col, '')) if h_col else ''
                    d_val = str(r.get(d_col, '')).strip() if d_col else ''
                    
                    clean_diag = ""
                    if d_val and d_val not in ['-', '', 'nan']:
                        if len(d_val) > 2 or not d_val.isupper():
                            clean_diag = d_val

                    matched_countries = []
                    raw_lower = raw_negara.lower()
                    for c_name in sorted_valid_countries:
                        if c_name.lower() in raw_lower:
                            matched_countries.append(c_name)
                            raw_lower = raw_lower.replace(c_name.lower(), '')
                    
                    if not matched_countries:
                        matched_countries = [raw_negara]

                    for negara_name in matched_countries:
                        key = negara_name.strip()
                        if not key or len(key) <= 1: continue
                        
                        if key not in verif_raw_agg:
                            verif_raw_agg[key] = {"verif_merah": 0, "t_respon": 0, "t_valid": 0, "diag_list": []}
                        
                        verif_raw_agg[key]["verif_merah"] += 1
                        if 'tidak respons' in status_v.lower():
                            verif_raw_agg[key]["t_respon"] += 1
                        elif 'tidak dilakukan' in status_v.lower() or 'belum' in status_v.lower():
                            verif_raw_agg[key]["t_valid"] += 1
                        if clean_diag:
                            verif_raw_agg[key]["diag_list"].append(clean_diag)
        except Exception as ex:
            print(f"Info verifikasi error: {ex}")

        verif_data = []
        for negara_name, dat in verif_raw_agg.items():
            v_count = dat["verif_merah"]
            t_resp = dat["t_respon"]
            t_dival = dat["t_valid"]
            terverifikasi = max(0, v_count - t_dival - t_resp)
            cakupan_pct = round((terverifikasi / v_count * 100), 2) if v_count > 0 else 0.0
            diag_counts = pd.Series(dat["diag_list"]).value_counts().head(2)
            diag_str = "; ".join([f"{k} ({v})" for k, v in diag_counts.items()]) if not diag_counts.empty else "-"
            total_riwayat_landing = landing_total_map.get(negara_name.lower(), "-")

            verif_data.append({
                "negara": negara_name,
                "total_riwayat": total_riwayat_landing,
                "verif_merah": v_count,
                "terverifikasi": terverifikasi,
                "tidak_respon": t_resp,
                "tidak_dilakukan": t_dival,
                "cakupan_pct": cakupan_pct,
                "diagnosis": diag_str
            })

        tren_2026 = [0] * 12
        tren_2025 = [0] * 12
        try:
            df_trend = load_data_from_sheets()
            n_col_trend = 'Riwayat perjalanan1' if 'Riwayat perjalanan1' in df_trend.columns else df_trend.columns[3]
            if negara and negara != "Semua Negara":
                df_trend = df_trend[df_trend[n_col_trend].astype(str).str.contains(negara, case=False, na=False)]

            if 'Tanggal' in df_trend.columns:
                indo_months = {
                    'januari': 'Jan', 'februari': 'Feb', 'maret': 'Mar', 'april': 'Apr', 'mei': 'May', 'juni': 'Jun',
                    'juli': 'Jul', 'agustus': 'Aug', 'september': 'Sep', 'oktober': 'Oct', 'november': 'Nov', 'desember': 'Dec',
                    'jan': 'Jan', 'feb': 'Feb', 'mar': 'Mar', 'apr': 'Apr', 'me': 'May', 'jun': 'Jun',
                    'jul': 'Jul', 'agu': 'Aug', 'sep': 'Sep', 'okt': 'Oct', 'nov': 'Nov', 'des': 'Dec'
                }
                def clean_and_parse_date(val):
                    if pd.isna(val): return pd.NaT
                    s = str(val).lower().strip()
                    for id_m, en_m in indo_months.items():
                        if id_m in s:
                            s = s.replace(id_m, en_m)
                            break
                    return pd.to_datetime(s, errors='coerce', dayfirst=True, format='mixed')

                df_trend['parsed_date'] = df_trend['Tanggal'].apply(clean_and_parse_date)
                df_trend['year'] = df_trend['parsed_date'].dt.year
                df_trend['month'] = df_trend['parsed_date'].dt.month

                df_2026 = df_trend[df_trend['year'] == 2026]
                if not df_2026.empty:
                    m_2026 = df_2026.groupby('month').size()
                    for m in range(1, 13):
                        tren_2026[m-1] = int(m_2026.get(m, 0))

                df_2025 = df_trend[df_trend['year'] == 2025]
                if not df_2025.empty:
                    m_2025 = df_2025.groupby('month').size()
                    for m in range(1, 13):
                        tren_2025[m-1] = int(m_2025.get(m, 0))
        except Exception as ex_t:
            print(f"Info tren error: {ex_t}")

        rincian_bulanan_data = []
        try:
            if 'Tanggal' in df_landing.columns and 'Total Isian' in df_landing.columns and 'Berisiko' in df_landing.columns:
                df_monthly = df_landing.groupby(df_landing['parsed_date'].dt.to_period('M')).agg({
                    'Total Isian': 'sum',
                    'Berisiko': 'sum'
                }).reset_index()
                
                for _, row in df_monthly.iterrows():
                    p_date = row['parsed_date']
                    if pd.isna(p_date): continue
                    tot_is = int(row['Total Isian'])
                    mer_is = int(row['Berisiko'])
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
        except Exception as e_rm:
            print(f"Error rincian bulanan: {e_rm}")

        return {
            "total_ppln": total_ppln,
            "merah": merah,
            "kuning": kuning,
            "hijau": hijau,
            "negara": negara_data,
            "verif": verif_data,
            "tren_2026": tren_2026,
            "tren_2025": tren_2025,
            "rincian_bulanan": rincian_bulanan_data,
            "interpretasi": interpretasi
        }
    except Exception as e:
        return {"error": str(e)}

class FilterRequest(BaseModel):
    gejala: str | None = None
    diagnosis: str | None = None
    periode: str | None = "semua"

@app.post("/api/stats")
def get_filtered_stats(req: FilterRequest):
    df_global = load_data_from_sheets()
    
    if df_global.empty:
        return {"error": "Data Google Sheets kosong atau tidak dapat diakses"}

    tanggal_terakhir = "28/10/2026"
    tanggal_col_name = next((c for c in df_global.columns if 'tanggal' in c.lower() or 'date' in c.lower()), None)
    
    if tanggal_col_name:
        df_global['parsed_date'] = pd.to_datetime(df_global[tanggal_col_name], errors='coerce', dayfirst=True)
        if df_global['parsed_date'].isna().sum() > len(df_global) * 0.5:
            df_global['parsed_date'] = pd.to_datetime(df_global[tanggal_col_name], errors='coerce', format='mixed')

        if 'Tanggal' in df_global.columns:
            val_tgl = df_global['Tanggal'].dropna()
            if not val_tgl.empty:
                tanggal_terakhir = str(val_tgl.iloc[-1])

    gejala_col_name = next((c for c in df_global.columns if 'gejala' in c.lower() or 'daftar gejala' in c.lower()), None)
    diag_col_name = next((c for c in df_global.columns if 'diagnosis' in c.lower()), None)

    fixed_top_gejala = []
    if gejala_col_name:
        all_g = []
        for val in df_global[gejala_col_name].dropna():
            cleaned_str = str(val).replace('â€¢', ',').replace('•', ',').replace(';', ',')
            for item in cleaned_str.split(','):
                ci = item.strip().strip('"').strip("'").title()
                if ci and len(ci) > 2 and ci != '-':
                    all_g.append(ci)
        fixed_top_gejala = [k for k, v in pd.Series(all_g).value_counts().head(10).items()]

    fixed_top_diag = []
    if diag_col_name:
        d_series = df_global[diag_col_name].dropna().astype(str).str.strip()
        d_series = d_series[~d_series.isin(['-', '', 'A', 'a', 'nan', 'NaN'])]
        fixed_top_diag = [k for k, v in d_series.value_counts().head(10).items()]

    df_filtered = df_global.copy()

    if req.periode and req.periode != "semua" and 'parsed_date' in df_filtered.columns:
        if req.periode == "this_month":
            max_date = df_filtered['parsed_date'].max()
            target_bulan = max_date.month if pd.notna(max_date) else 10
            target_tahun = max_date.year if pd.notna(max_date) else 2026
            df_filtered = df_filtered[
                (df_filtered['parsed_date'].dt.month == target_bulan) & 
                (df_filtered['parsed_date'].dt.year == target_tahun)
            ]
        elif req.periode.isdigit():
            target_tahun = int(req.periode)
            df_filtered = df_filtered[df_filtered['parsed_date'].dt.year == target_tahun]

    if req.gejala and gejala_col_name:
        df_filtered = df_filtered[
            df_filtered[gejala_col_name].fillna('').str.contains(req.gejala, case=False, na=False)
        ]
    if req.diagnosis and diag_col_name:
        df_filtered = df_filtered[
            df_filtered[diag_col_name].fillna('').str.contains(req.diagnosis, case=False, na=False)
        ]

    total_merah = len(df_filtered)
    
    verif_counts = {}
    terverifikasi = 0
    tidak_respon = 0
    tidak_divalidasi = 0
    
    h_col = next((c for c in df_filtered.columns if 'verifikasi' in c.lower() or 'hasil' in c.lower()), None)
    if h_col:
        verif_series = df_filtered[h_col].dropna()
        verif_counts = verif_series.value_counts().to_dict()
        tidak_respon = int(verif_counts.get('Penumpang tidak respons', 0))
        tidak_divalidasi = int(verif_counts.get('Tidak dilakukan verifikasi', 0))
        terverifikasi = int(total_merah - tidak_divalidasi - tidak_respon)
        if terverifikasi < 0: terverifikasi = 0

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
            pct = round((count / base_total) * 100, 2)
            top_diag_data.append({"val": d_item, "count": count, "pct": pct})
        top_diag_data.sort(key=lambda x: x['count'], reverse=True)

    g_txt = req.gejala if req.gejala else "Keseluruhan Gejala"
    d_txt = req.diagnosis if req.diagnosis else "Keseluruhan Diagnosis"
    
    top_g_name = top_gejala_data[0]['val'] if top_gejala_data and top_gejala_data[0]['count'] > 0 else 'Tidak ada'
    top_d_name = top_diag_data[0]['val'] if top_diag_data and top_diag_data[0]['count'] > 0 else 'Tidak ada'

    pembacaan = (
        f"Berdasarkan pemantauan data real-time Google Sheets untuk periode **{req.periode}**, tercatat akumulasi sebanyak "
        f"**{total_merah:,} kasus PPLN**. Dari total tersebut, tingkat rasio verifikasi lapangan mencapai **{cakupan_pct}%** "
        f"dengan **{terverifikasi:,} kasus tuntas**. Analisis irisan menunjukkan bahwa indikasi gejala dominan saat ini adalah "
        f"**{top_g_name}**, dengan mayoritas diagnosis terarah pada **{top_d_name}**. "
        f"Kondisi ini menuntut kesiagaan tinggi di posko kedatangan guna menekan angka penumpang yang belum memberikan respons (**{tidak_respon:,} kasus**)."
    )
    
    rekomendasi = [
        f"Intensifkan prosedur kekarantinaan dan validasi dokumen kesehatan mendalam pada manifes dengan profil gejala **{g_txt.lower()}**.",
        f"Lakukan pelacakan aktif (tracing) terhadap **{tidak_respon} penumpang** berstatus 'tidak respons' untuk memastikan status kesehatan mereka aman.",
        f"Koordinasikan penanganan medis darurat bersama tim surveilans wilayah kerja jika ditemukan lonjakan pada diagnosis **{top_d_name}**.",
        "Pastikan seluruh rekapitulasi harian pada Google Sheets tersinkronisasi secara real-time untuk mendukung ketepatan kebijakan."
    ]

    return {
        "tanggal_terakhir": tanggal_terakhir,
        "total_merah": total_merah,
        "terverifikasi": terverifikasi,
        "tidak_respon": tidak_respon,
        "tidak_divalidasi": tidak_divalidasi,
        "cakupan_pct": cakupan_pct,
        "status_tindak_lanjut": verif_counts,
        "top_gejala": top_gejala_data,
        "top_diagnosis": top_diag_data,
        "pembacaan": pembacaan,
        "rekomendasi": rekomendasi
    }