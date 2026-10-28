from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pandas as pd
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Backend Smartkarkes AI - Realtime Sheets")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Link Google Spreadsheet publik milik Putri (Tab: MERAH)
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/1fqp6xsIxYOkF-SWaEUBae1pW7BKu9RMiI7xyjOuvB5o/gviz/tq?tqx=out:csv&sheet=MERAH"

def load_data_from_sheets():
    try:
        df = pd.read_csv(SHEET_CSV_URL)
        df.columns = df.columns.str.strip().str.replace('\ufeff', '')
        return df
    except Exception as e:
        print(f"⚠️ Gagal memuat data dari Google Sheets: {e}")
        # Fallback cadangan ke file lokal jika offline
        if os.path.exists("data/data_merah.csv"):
            df = pd.read_csv("data/data_merah.csv")
            df.columns = df.columns.str.strip().str.replace('\ufeff', '')
            return df
        return pd.DataFrame()

class FilterRequest(BaseModel):
    gejala: str | None = None
    diagnosis: str | None = None
    periode: str | None = "semua"

@app.post("/api/stats")
def get_filtered_stats(req: FilterRequest):
    # Tarik data terbaru secara real-time langsung dari Google Sheets setiap kali API dipanggil
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

    # Hitung Top 10 Gejala Global
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

    # Hitung Top 10 Diagnosis Global
    fixed_top_diag = []
    if diag_col_name:
        d_series = df_global[diag_col_name].dropna().astype(str).str.strip()
        d_series = d_series[~d_series.isin(['-', '', 'A', 'a', 'nan', 'NaN'])]
        fixed_top_diag = [k for k, v in d_series.value_counts().head(10).items()]

    df_filtered = df_global.copy()

    # 1. Filter Berdasarkan Periode Waktu
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
            df_filtered = df_filtered[
                df_filtered['parsed_date'].dt.year == target_tahun
            ]

    # 2. Filter Berdasarkan Gejala & Diagnosis
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
    
    if 'Hasil Verifikasi' in df_filtered.columns:
        verif_series = df_filtered['Hasil Verifikasi'].dropna()
        verif_counts = verif_series.value_counts().to_dict()
        tidak_respon = int(verif_counts.get('Penumpang tidak respons', 0))
        tidak_divalidasi = int(verif_counts.get('Tidak dilakukan verifikasi', 0))
        terverifikasi = int(total_merah - tidak_divalidasi - tidak_respon)
        if terverifikasi < 0: terverifikasi = 0

    cakupan_pct = round((terverifikasi / total_merah * 100), 2) if total_merah > 0 else 0.0

    # 3. Hitung Gejala Berdasarkan Data Terfilter
    df_for_gejala = df_filtered.copy()
    top_gejala_data = []
    if gejala_col_name:
        for g_item in fixed_top_gejala:
            count = int(df_for_gejala[gejala_col_name].fillna('').str.contains(g_item, case=False, na=False).sum())
            top_gejala_data.append({"val": g_item, "count": count})
        top_gejala_data.sort(key=lambda x: x['count'], reverse=True)

    # 4. Hitung Diagnosis Berdasarkan Data Terfilter
    df_for_diag = df_filtered.copy()
    top_diag_data = []
    if diag_col_name:
        base_total = len(df_for_diag) if len(df_for_diag) > 0 else 1
        for d_item in fixed_top_diag:
            count = int((df_for_diag[diag_col_name].dropna().astype(str).str.strip() == d_item).sum())
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