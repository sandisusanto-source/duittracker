# CEO Dashboard — Cahaya Senja

Dashboard eksekutif untuk bisnis marketplace **Cahaya Senja** (perlengkapan toko emas & perhiasan).
Tujuannya satu: membantu owner mengambil keputusan **dalam < 5 menit setiap pagi** — fokus
profit, cashflow, dan **perubahan** dibanding kemarin/minggu lalu/bulan lalu, plus alert anomali
dan ringkasan harian otomatis.

Dibangun di atas stack yang sama dengan DuitTracker: **Python + Flask + SQLite + Chart-less HTML + Claude**.

---

## Fitur (sesuai brief A–H)

| Bagian | Isi |
|---|---|
| **A. Executive Summary** | Omset hari ini/kemarin/bulan berjalan, target, % pencapaian, proyeksi, estimasi laba & margin |
| **B. Marketplace Performance** | Per channel (Shopee, Tokopedia, TikTok Shop, Lazada): omset, order, AOV, pertumbuhan |
| **C. Produk Terlaris** | Top 10: omset, qty, margin estimasi, perubahan ranking |
| **D. Advertising** | Total biaya iklan, ROAS, ACOS, TACOS, channel terbaik & terburuk |
| **E. Inventory Alert** | Hampir habis (+ estimasi hari habis), barang diam >30 hari |
| **F. Cashflow** | Saldo kas, hutang supplier, piutang, pengeluaran minggu ini, prediksi kas 30 hari |
| **G. Alert Center** | 8 aturan otomatis (omset anjlok, ROAS rendah, best-seller habis, dst) |
| **H. Daily CEO Brief** | Ringkasan pagi otomatis (Claude, dengan fallback rule-based) |

---

## Cara menjalankan (lokal)

```bash
cd ceo
pip install -r requirements.txt
python seed.py        # isi data contoh (opsional, untuk demo)
python app.py         # buka http://localhost:5000
```

Tanpa `ANTHROPIC_API_KEY`, Daily Brief tetap jalan memakai mesin rule-based.

### Variabel lingkungan (semua opsional)

| Env | Fungsi |
|---|---|
| `ANTHROPIC_API_KEY` | Aktifkan Daily Brief versi AI (Claude) |
| `BRIEF_MODEL` | Override model brief (default `claude-opus-4-8`) |
| `BOT_TOKEN` + `CEO_CHAT_ID` | Kirim brief & alert kritis ke Telegram tiap pagi 07:00 |
| `PORT` | Port web (default 5000) |
| `CEO_DB_PATH` | Lokasi file SQLite (default `ceo/ceo.db`) |

### Deploy

`Dockerfile` sudah tersedia (pola sama dengan project utama) — cocok untuk Railway/Fly/VPS.

---

## Cara input data (MVP: upload XLSX/CSV)

Buka dashboard → **Upload** → tarik file. Jenis file **terdeteksi otomatis** dari nama kolomnya
(mendukung header Indonesia & Inggris). Template ada di folder [`templates/`](templates/):

| File | Untuk | Kolom utama |
|---|---|---|
| `penjualan_template.csv` | Penjualan harian | tanggal, marketplace, sku, qty, omset, jumlah order |
| `iklan_template.csv` | Biaya iklan | tanggal, marketplace, biaya iklan, omset iklan |
| `stok_template.csv` | Snapshot stok | tanggal, sku, sisa stok |
| `kas_template.csv` | Cashflow | tanggal, tipe (in/out/ar/ap), jumlah, jatuh tempo |
| `produk_template.csv` | Master produk + **HPP** | sku, nama, kategori, hpp |
| `target_template.csv` | Target bulanan | bulan, channel, target omset |

> **Penting:** isi **HPP** (harga pokok) tiap produk — tanpa ini, estimasi laba & margin tidak akurat.

Setiap selesai upload, alert & brief otomatis dihitung ulang. Tombol **Refresh** memaksa
perhitungan ulang kapan saja.

---

## Arsitektur

```
Upload XLSX/CSV ─▶ importer.py ─▶ SQLite (db.py) ─▶ analytics.py ─▶ /api/* ─▶ dashboard.html
                                       │
                                       └─▶ alerts.py (alert engine + Claude brief)
                                                │
                              scheduler 07:00 (app.py) ─▶ push Telegram
```

| File | Tanggung jawab |
|---|---|
| `db.py` | Skema 9 tabel + helper SQLite (kompatibel Postgres/Supabase) |
| `importer.py` | Baca & normalisasi XLSX/CSV, auto-deteksi jenis |
| `analytics.py` | Semua agregasi KPI + perhitungan delta |
| `alerts.py` | Alert engine (8 rule) + Daily Brief (Claude/fallback) |
| `app.py` | Flask: web, API, upload, scheduler harian, push Telegram |
| `dashboard.html` | UI mobile-first (vanilla JS, satu file) |
| `seed.py` | Data contoh realistis untuk demo |

### Threshold alert (mudah dikalibrasi)

Semua ambang ada di bagian atas `alerts.py`:
`TH_OMSET_DROP=20%`, `TH_CHANNEL_DROP=40%`, `TH_ROAS_MIN=3.0`, `TH_AD_SPIKE=150%`, `TH_LOW_STOCK_DAYS=7`.

---

## Jalur pengembangan (N8N + AI agent)

Skema SQLite ini 1:1 dengan PostgreSQL. Untuk skala produksi:

1. Pindah DB ke **Supabase** → otomatis dapat REST API.
2. Pindah scheduler (import → agregat → alert → brief → kirim) ke **workflow N8N** (tiap langkah = node).
3. Tambah **AI agent** yang menjawab pertanyaan owner ("kenapa omset Tokopedia turun?") dengan
   query langsung ke tabel yang sama.
4. Ganti upload manual dengan **API resmi marketplace** (Shopee/Tokopedia/TikTok/Lazada).
```
