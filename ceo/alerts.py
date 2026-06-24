"""
Alert Engine (Section G) + Daily CEO Brief (Section H).

Alert = rule-based, deterministik, mudah dikalibrasi.
Brief = Claude merangkai semua angka jadi 3-4 kalimat + prioritas,
        dengan fallback rule-based kalau API key tidak ada.
"""

import json
import os
from datetime import datetime

import analytics
from db import execute, query, query_one, get_conn

# Ambang batas — semua di satu tempat agar gampang dikalibrasi owner
TH_OMSET_DROP = 0.20      # omset turun >20% vs rata-rata
TH_ROAS_DROP = 0.30       # ROAS turun >30% (per channel, butuh data historis)
TH_AD_SPIKE = 1.50        # biaya iklan >150% rata-rata
TH_CHANNEL_DROP = 0.40    # omset channel turun >40% vs minggu lalu
TH_LOW_STOCK_DAYS = 7     # stok habis dalam <=7 hari
TH_ROAS_MIN = 3.0         # ROAS sehat minimum (acuan warning absolut)


def _add(conn, atype, severity, title, message, payload=None):
    conn.execute(
        """INSERT INTO alerts (created_at, type, severity, title, message, payload)
           VALUES (?,?,?,?,?,?)""",
        (datetime.now().isoformat(timespec="seconds"), atype, severity,
         title, message, json.dumps(payload or {}, ensure_ascii=False)),
    )


def run_alert_engine():
    """Hitung ulang semua alert. Hapus alert lama yang belum dibaca lalu generate baru."""
    snap = analytics.full_snapshot()
    exe = snap["executive"]
    chans = snap["channels"]["channels"]
    ads = snap["advertising"]
    inv = snap["inventory"]
    cash = snap["cashflow"]
    products = snap["products"]["products"]

    generated = 0
    with get_conn() as conn:
        # bersihkan alert lama yang auto-generated (biar tidak menumpuk)
        conn.execute("DELETE FROM alerts WHERE is_read=0")

        # 1. Omset anjlok (hari ini vs kemarin)
        d = exe.get("omset_today_vs_yest_pct")
        if d is not None and d <= -TH_OMSET_DROP * 100:
            _add(conn, "omset_drop", "critical",
                 "Omset turun tajam",
                 "Omset hari ini %s, turun %.0f%% dibanding kemarin." % (
                     _rp(exe["omset_today"]), abs(d)),
                 {"pct": d})
            generated += 1

        # 2. Channel anjlok
        for c in chans:
            g = c.get("growth_pct")
            if g is not None and g <= -TH_CHANNEL_DROP * 100:
                _add(conn, "channel_drop", "warning",
                     "Penjualan %s anjlok" % c["channel"],
                     "Omset %s 7 hari turun %.0f%% vs minggu lalu (%s)." % (
                         c["channel"], abs(g), _rp(c["revenue_7d"])),
                     {"channel": c["channel"], "pct": g})
                generated += 1

        # 3. ROAS rendah (acuan absolut, karena historis ROAS mungkin terbatas di MVP)
        for c in ads.get("per_channel", []):
            if c.get("roas") is not None and c["roas"] < TH_ROAS_MIN and c["cost"] > 0:
                _add(conn, "roas_drop", "warning",
                     "ROAS %s rendah" % c["channel"],
                     "ROAS iklan %s = %.1f (di bawah target %.1f). Cek kampanye." % (
                         c["channel"], c["roas"], TH_ROAS_MIN),
                     {"channel": c["channel"], "roas": c["roas"]})
                generated += 1

        # 4. Biaya iklan melonjak (total minggu vs run-rate sederhana)
        # (perbandingan absolut: cost harian terakhir vs rata-rata mingguan)
        spike = _ad_spike()
        if spike:
            _add(conn, "ad_spike", "warning", "Biaya iklan melonjak",
                 spike["msg"], spike)
            generated += 1

        # 5. Best-seller kehabisan stok
        top_skus = {p["sku"] for p in products[:10]}
        snap_date = inv.get("snapshot_date")
        if snap_date:
            zero = query(
                "SELECT i.sku, COALESCE(p.name,i.sku) name FROM inventory_snapshot i "
                "LEFT JOIN products p ON i.sku=p.sku WHERE i.date=? AND i.stock_qty<=0",
                (snap_date,))
            for z in zero:
                if z["sku"] in top_skus:
                    _add(conn, "bestseller_oos", "critical",
                         "Best-seller habis: %s" % z["name"],
                         "Produk terlaris %s stoknya 0. Restock segera!" % z["name"],
                         {"sku": z["sku"]})
                    generated += 1

        # 6. Stok kritis (habis <=7 hari)
        for s in inv.get("low_stock", [])[:5]:
            _add(conn, "low_stock", "warning",
                 "Stok menipis: %s" % s["name"],
                 "%s diperkirakan habis dalam %d hari (sisa %d unit)." % (
                     s["name"], s["days_left"], s["stock_qty"]),
                 s)
            generated += 1

        # 7. Margin turun (di bawah ambang sehat)
        if exe.get("margin_pct") is not None and exe["margin_pct"] < 15:
            _add(conn, "margin_drop", "warning", "Margin tipis",
                 "Estimasi margin bulan ini %.1f%%, di bawah 15%%. Cek HPP & diskon." % exe["margin_pct"],
                 {"margin": exe["margin_pct"]})
            generated += 1

        # 8. Kas menipis
        if cash.get("projected_negative"):
            _add(conn, "cash_low", "critical", "Proyeksi kas negatif",
                 "Prediksi kas 30 hari ke depan %s. Atur penagihan piutang & tunda pengeluaran." % (
                     _rp(cash["projected_cash_30d"])),
                 {"projected": cash["projected_cash_30d"]})
            generated += 1

    return generated


def _ad_spike():
    """Deteksi lonjakan: biaya iklan hari terakhir > 150% rata-rata 7 hari sebelumnya."""
    last = query_one("SELECT MAX(date) d FROM ad_spend_daily")
    if not last or not last["d"]:
        return None
    last_date = last["d"]
    today_cost = query_one(
        "SELECT COALESCE(SUM(cost),0) v FROM ad_spend_daily WHERE date=?", (last_date,))["v"]
    from analytics import _shift
    avg = query_one(
        "SELECT COALESCE(AVG(daily),0) v FROM (SELECT date, SUM(cost) daily FROM ad_spend_daily "
        "WHERE date BETWEEN ? AND ? GROUP BY date)",
        (_shift(last_date, -7), _shift(last_date, -1)))["v"]
    if avg > 0 and today_cost > avg * TH_AD_SPIKE:
        return {"msg": "Biaya iklan %s pada %s, %.0f%% di atas rata-rata harian (%s)." % (
            _rp(today_cost), last_date, today_cost / avg * 100, _rp(avg)),
            "today": today_cost, "avg": avg}
    return None


def get_alerts(include_read=False):
    sql = "SELECT * FROM alerts"
    if not include_read:
        sql += " WHERE is_read=0"
    sql += " ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, id DESC"
    return query(sql)


# ════════════════ DAILY CEO BRIEF ════════════════

def generate_brief():
    """Generate brief harian. Pakai Claude jika ada API key, fallback rule-based."""
    snap = analytics.full_snapshot()
    body = _brief_via_claude(snap) or _brief_fallback(snap)
    date = snap["executive"]["data_date"]
    execute(
        """INSERT INTO daily_brief (date, body, created_at) VALUES (?,?,?)
           ON CONFLICT(date) DO UPDATE SET body=excluded.body, created_at=excluded.created_at""",
        (date, body, datetime.now().isoformat(timespec="seconds")),
    )
    return body


def get_latest_brief():
    return query_one("SELECT * FROM daily_brief ORDER BY date DESC LIMIT 1")


def _brief_via_claude(snap):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt = (
            "Kamu CFO untuk bisnis marketplace 'Cahaya Senja' (perlengkapan toko emas & perhiasan). "
            "Dari data JSON berikut, tulis ringkasan pagi untuk owner: 3-4 kalimat padat lalu "
            "3 prioritas hari ini dalam bentuk poin. Fokus profit & cashflow. "
            "Bahasa Indonesia, langsung to-the-point, tanpa basa-basi pembuka. "
            "Sebut angka konkret (Rupiah, %).\n\nDATA:\n"
            + json.dumps(snap, ensure_ascii=False, default=str)
        )
        resp = client.messages.create(
            model=os.environ.get("BRIEF_MODEL", "claude-opus-4-8"),
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        print("Brief Claude error:", e)
        return None


def _brief_fallback(snap):
    """Brief rule-based tanpa AI — tetap actionable."""
    exe = snap["executive"]
    chans = snap["channels"]["channels"]
    products = snap["products"]["products"]
    ads = snap["advertising"]
    inv = snap["inventory"]
    cash = snap["cashflow"]

    parts = []
    d = exe.get("omset_today_vs_yest_pct")
    trend = ("naik %.0f%%" % d) if d and d > 0 else (("turun %.0f%%" % abs(d)) if d else "stabil")
    parts.append("Omset terakhir (%s) %s, %s dibanding kemarin." % (
        exe["data_date"], _rp(exe["omset_today"]), trend))

    if exe.get("achievement_pct") is not None:
        parts.append("Pencapaian target bulan ini %.0f%% (%s dari %s), estimasi laba %s (margin %.0f%%)." % (
            exe["achievement_pct"], _rp(exe["omset_mtd"]), _rp(exe["target_month"]),
            _rp(exe["est_profit_mtd"]), exe["margin_pct"]))
    else:
        parts.append("Omset bulan berjalan %s, estimasi laba %s." % (
            _rp(exe["omset_mtd"]), _rp(exe["est_profit_mtd"])))

    if products:
        parts.append("Produk terbaik: %s (%s)." % (products[0]["name"], _rp(products[0]["revenue"])))
    if ads.get("worst_channel"):
        w = ads["worst_channel"]
        parts.append("ROAS terlemah di %s (%.1f)." % (w["channel"], w["roas"]))

    # prioritas
    prio = []
    if inv.get("low_stock"):
        s = inv["low_stock"][0]
        prio.append("Restock %s (habis dalam %d hari)." % (s["name"], s["days_left"]))
    if ads.get("worst_channel") and ads["worst_channel"]["roas"] and ads["worst_channel"]["roas"] < TH_ROAS_MIN:
        prio.append("Audit kampanye iklan %s." % ads["worst_channel"]["channel"])
    if cash.get("projected_negative"):
        prio.append("Amankan kas: tagih piutang %s." % _rp(cash["accounts_receivable"]))
    weak = [c for c in chans if c.get("growth_pct") is not None and c["growth_pct"] < -20]
    if weak:
        prio.append("Cek penurunan penjualan %s." % weak[0]["channel"])
    if not prio:
        prio.append("Pertahankan momentum; tidak ada anomali kritis.")

    body = " ".join(parts) + "\n\nPrioritas hari ini:\n" + "\n".join(
        "%d. %s" % (i + 1, p) for i, p in enumerate(prio[:3]))
    return body


def _rp(n):
    try:
        return "Rp " + "{:,.0f}".format(float(n)).replace(",", ".")
    except (ValueError, TypeError):
        return "Rp 0"
