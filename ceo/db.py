"""
Database layer untuk CEO Dashboard Cahaya Senja.

Memakai SQLite (1 file, nol konfigurasi) sebagai upgrade dari storage JSON.
Untuk produksi nanti, skema ini kompatibel dengan PostgreSQL/Supabase
sehingga bisa dipanggil N8N & AI agent lewat REST API.

Grain data: HARIAN x CHANNEL x SKU. Cukup untuk semua KPI CEO tanpa
menyimpan jutaan baris order mentah.
"""

import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("CEO_DB_PATH", os.path.join(os.path.dirname(__file__), "ceo.db"))

SCHEMA = """
-- ════════════════ MASTER DATA (jarang berubah) ════════════════

CREATE TABLE IF NOT EXISTS channels (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT UNIQUE NOT NULL,          -- Shopee, Tokopedia, TikTok Shop, Lazada
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS suppliers (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT UNIQUE NOT NULL,
    phone TEXT
);

CREATE TABLE IF NOT EXISTS products (
    sku         TEXT PRIMARY KEY,             -- kunci yang menyatukan semua tabel
    name        TEXT NOT NULL,
    category    TEXT,
    cost_price  REAL DEFAULT 0,              -- HPP, WAJIB untuk hitung margin & laba
    supplier_id INTEGER REFERENCES suppliers(id)
);

CREATE TABLE IF NOT EXISTS targets (
    month          TEXT NOT NULL,            -- format YYYY-MM
    channel        TEXT DEFAULT 'ALL',       -- 'ALL' = target total bisnis
    revenue_target REAL NOT NULL,
    PRIMARY KEY (month, channel)
);

-- ════════════════ DATA HARIAN (di-upload tiap hari) ════════════════

CREATE TABLE IF NOT EXISTS sales_daily (
    date        TEXT NOT NULL,               -- YYYY-MM-DD
    channel     TEXT NOT NULL,
    sku         TEXT,                         -- NULL = ringkasan channel tanpa detail produk
    qty         INTEGER DEFAULT 0,
    revenue     REAL DEFAULT 0,
    order_count INTEGER DEFAULT 0,
    PRIMARY KEY (date, channel, sku)
);

CREATE TABLE IF NOT EXISTS ad_spend_daily (
    date               TEXT NOT NULL,
    channel            TEXT NOT NULL,
    cost               REAL DEFAULT 0,
    impressions        INTEGER DEFAULT 0,
    clicks             INTEGER DEFAULT 0,
    conversions        INTEGER DEFAULT 0,
    revenue_attributed REAL DEFAULT 0,        -- omset yang berasal dari iklan
    PRIMARY KEY (date, channel)
);

CREATE TABLE IF NOT EXISTS inventory_snapshot (
    date      TEXT NOT NULL,
    sku       TEXT NOT NULL,
    stock_qty INTEGER DEFAULT 0,
    PRIMARY KEY (date, sku)
);

CREATE TABLE IF NOT EXISTS cash_ledger (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    date      TEXT NOT NULL,
    type      TEXT NOT NULL,                  -- in | out | ar (piutang) | ap (hutang)
    amount    REAL NOT NULL,
    category  TEXT,
    note      TEXT,
    due_date  TEXT,                           -- untuk ar / ap
    is_settled INTEGER DEFAULT 0              -- ar/ap sudah lunas?
);

-- ════════════════ DI-GENERATE SISTEM ════════════════

CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    type       TEXT NOT NULL,                 -- omset_drop, roas_drop, dst
    severity   TEXT NOT NULL,                 -- info | warning | critical
    title      TEXT NOT NULL,
    message    TEXT NOT NULL,
    is_read    INTEGER DEFAULT 0,
    payload    TEXT                           -- JSON data pendukung
);

CREATE TABLE IF NOT EXISTS daily_brief (
    date       TEXT PRIMARY KEY,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Indeks untuk query agregasi yang sering dipakai
CREATE INDEX IF NOT EXISTS idx_sales_date    ON sales_daily(date);
CREATE INDEX IF NOT EXISTS idx_sales_channel ON sales_daily(channel);
CREATE INDEX IF NOT EXISTS idx_ads_date      ON ad_spend_daily(date);
CREATE INDEX IF NOT EXISTS idx_inv_date      ON inventory_snapshot(date);
CREATE INDEX IF NOT EXISTS idx_cash_date     ON cash_ledger(date);
"""

DEFAULT_CHANNELS = ["Shopee", "Tokopedia", "TikTok Shop", "Lazada"]


@contextmanager
def get_conn():
    """Context manager koneksi SQLite dengan row dict-like."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Buat semua tabel + isi channel default kalau belum ada."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        for name in DEFAULT_CHANNELS:
            conn.execute(
                "INSERT OR IGNORE INTO channels (name) VALUES (?)", (name,)
            )


def query(sql, params=()):
    """Jalankan SELECT, kembalikan list of dict."""
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def query_one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    with get_conn() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid


if __name__ == "__main__":
    init_db()
    print("Database siap di:", DB_PATH)
