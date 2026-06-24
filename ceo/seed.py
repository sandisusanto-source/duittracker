"""
Generate data contoh realistis untuk demo CEO Dashboard Cahaya Senja.
Membuat ~21 hari data: penjualan, iklan, stok, kas, produk (HPP), target.

Jalankan: python seed.py
Deterministik (tanpa random murni) supaya hasil bisa direproduksi.
"""

import math
from datetime import datetime, timedelta

from db import init_db, get_conn

CHANNELS = ["Shopee", "Tokopedia", "TikTok Shop", "Lazada"]

# (sku, nama, kategori, HPP, harga jual rata2, popularitas)
PRODUCTS = [
    ("AJ1002C", "Timbangan Digital AJ1002C", "Timbangan", 145000, 219000, 1.00),
    ("KCP-01", "Kotak Cincin Premium", "Kotak Perhiasan", 28000, 65000, 0.85),
    ("DPH-T3", "Display Perhiasan Tier 3 Susun", "Display", 95000, 185000, 0.70),
    ("KKL-12", "Kotak Kalung Beludru", "Kotak Perhiasan", 32000, 78000, 0.65),
    ("TBG-500", "Timbangan Emas 500g 0.01", "Timbangan", 210000, 349000, 0.55),
    ("DPC-R", "Display Cincin Roll", "Display", 42000, 89000, 0.50),
    ("LUP-30", "Loupe Pembesar 30x", "Alat", 18000, 45000, 0.45),
    ("KGL-BX", "Kotak Gelang Transparan", "Kotak Perhiasan", 24000, 55000, 0.40),
    ("DPS-NK", "Display Necklace Stand", "Display", 38000, 82000, 0.35),
    ("TBG-200", "Timbangan Saku 200g", "Timbangan", 65000, 129000, 0.30),
    ("BTL-PWR", "Botol Pembersih Perhiasan", "Alat", 15000, 39000, 0.25),
    ("SRG-MJ", "Sarung Majun Poles Emas", "Alat", 8000, 25000, 0.18),
]

DAYS = 21


def daterange():
    today = datetime.now()
    for i in range(DAYS - 1, -1, -1):
        yield (today - timedelta(days=i)).strftime("%Y-%m-%d"), DAYS - 1 - i


def seed():
    init_db()
    with get_conn() as conn:
        # bersihkan data lama (demo)
        for t in ["sales_daily", "ad_spend_daily", "inventory_snapshot",
                  "cash_ledger", "targets", "alerts", "daily_brief", "products"]:
            conn.execute("DELETE FROM %s" % t)

        # master produk
        for sku, name, cat, hpp, _price, _pop in PRODUCTS:
            conn.execute(
                "INSERT OR REPLACE INTO products (sku,name,category,cost_price) VALUES (?,?,?,?)",
                (sku, name, cat, hpp))

        # target bulan ini
        month = datetime.now().strftime("%Y-%m")
        conn.execute("INSERT OR REPLACE INTO targets (month,channel,revenue_target) VALUES (?,?,?)",
                     (month, "ALL", 350_000_000))

        ch_weight = {"Shopee": 0.42, "Tokopedia": 0.25, "TikTok Shop": 0.23, "Lazada": 0.10}

        for date, idx in daterange():
            # tren musiman ringan + dip akhir minggu pekan terakhir (untuk memicu alert)
            base = 1.0 + 0.015 * idx
            wobble = 1.0 + 0.12 * math.sin(idx / 2.0)
            # buat hari terakhir turun tajam supaya alert "omset drop" muncul (demo)
            if idx == DAYS - 1:
                wobble *= 0.62

            for ch, w in ch_weight.items():
                day_orders = 0
                day_rev = 0.0
                for sku, name, cat, hpp, price, pop in PRODUCTS:
                    # qty per produk per channel per hari
                    q = base * wobble * w * pop * 6.0
                    qty = int(round(q + 0.3 * math.sin((idx + len(sku)) / 1.5)))
                    if qty <= 0:
                        continue
                    # TikTok dorong timbangan AJ1002C lebih kuat
                    if ch == "TikTok Shop" and sku == "AJ1002C":
                        qty = int(qty * 1.6)
                    rev = qty * price
                    conn.execute(
                        """INSERT OR REPLACE INTO sales_daily
                           (date,channel,sku,qty,revenue,order_count) VALUES (?,?,?,?,?,?)""",
                        (date, ch, sku, qty, rev, 0))
                    day_orders += qty
                    day_rev += rev
                # set order_count realistis (1.4 item/order) di baris ringkasan tidak dipakai;
                # sebar order_count ke baris pertama channel
                conn.execute(
                    "UPDATE sales_daily SET order_count=? WHERE date=? AND channel=? AND sku=?",
                    (max(1, int(day_orders / 1.4)), date, ch, "AJ1002C"))

                # iklan: ~7% omset, kecuali Shopee hari terakhir melonjak (demo alert)
                ad_rate = 0.07
                if ch == "Shopee" and idx == DAYS - 1:
                    ad_rate = 0.18  # lonjakan + ROAS jeblok
                cost = day_rev * ad_rate
                attr = day_rev * (0.45 if not (ch == "Shopee" and idx == DAYS - 1) else 0.30)
                conn.execute(
                    """INSERT OR REPLACE INTO ad_spend_daily
                       (date,channel,cost,impressions,clicks,conversions,revenue_attributed)
                       VALUES (?,?,?,?,?,?,?)""",
                    (date, ch, round(cost), int(day_rev / 500), int(day_rev / 4000),
                     max(1, int(day_orders * 0.4)), round(attr)))

        # snapshot stok terakhir: bikin beberapa kritis & 1 dead stock
        last = datetime.now().strftime("%Y-%m-%d")
        stock_map = {
            "AJ1002C": 240, "KCP-01": 18,   # KCP-01 best seller hampir habis
            "DPH-T3": 60, "KKL-12": 90, "TBG-500": 35, "DPC-R": 8,  # DPC-R kritis
            "LUP-30": 120, "KGL-BX": 70, "DPS-NK": 0,  # DPS-NK habis
            "TBG-200": 40, "BTL-PWR": 200, "SRG-MJ": 500,
        }
        for sku, qty in stock_map.items():
            conn.execute(
                "INSERT OR REPLACE INTO inventory_snapshot (date,sku,stock_qty) VALUES (?,?,?)",
                (last, sku, qty))
        # produk diam >30 hari: tambah SKU tanpa penjualan
        conn.execute("INSERT OR REPLACE INTO products (sku,name,category,cost_price) VALUES (?,?,?,?)",
                     ("OLD-DISPLAY", "Display Akrilik Model Lama", "Display", 55000))
        conn.execute("INSERT OR REPLACE INTO inventory_snapshot (date,sku,stock_qty) VALUES (?,?,?)",
                     (last, "OLD-DISPLAY", 45))

        # cashflow
        ledger = [
            ("in", 180_000_000, "Pencairan marketplace", -18, None),
            ("in", 95_000_000, "Pencairan marketplace", -8, None),
            ("out", 120_000_000, "Pembelian stok supplier", -15, None),
            ("out", 18_000_000, "Gaji karyawan", -10, None),
            ("out", 6_500_000, "Operasional & packing", -5, None),
            ("out", 9_000_000, "Biaya iklan top-up", -3, None),
            ("ap", 75_000_000, "Hutang supplier timbangan", -2, 12),  # jatuh tempo 12 hari
            ("ap", 22_000_000, "Hutang packaging", -1, 25),
            ("ar", 88_000_000, "Dana marketplace belum cair", -1, 7),
        ]
        for typ, amt, note, doff, due_off in ledger:
            d = (datetime.now() + timedelta(days=doff)).strftime("%Y-%m-%d")
            due = (datetime.now() + timedelta(days=due_off)).strftime("%Y-%m-%d") if due_off else None
            conn.execute(
                "INSERT INTO cash_ledger (date,type,amount,category,note,due_date) VALUES (?,?,?,?,?,?)",
                (d, typ, amt, note.split()[0], note, due))

    print("Seed selesai: %d hari data untuk %d produk x %d channel." % (DAYS, len(PRODUCTS), len(CHANNELS)))


if __name__ == "__main__":
    seed()
