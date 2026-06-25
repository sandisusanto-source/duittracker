"""
Lapisan agregasi/analitik untuk CEO Dashboard.

Semua KPI section A-F dihitung di sini dari tabel harian.
Prinsip: setiap angka selalu disertai PERUBAHAN (delta) vs periode
pembanding, karena itulah yang memicu keputusan owner.
"""

from datetime import datetime, timedelta

from db import query, query_one


# ──────────────────────────────────────────────────────────────
# Helper tanggal & delta
# ──────────────────────────────────────────────────────────────
def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _shift(date_str, days):
    return (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")


def _month_of(date_str):
    return date_str[:7]


def pct_change(current, previous):
    """Persentase perubahan; None kalau pembanding 0 (hindari bagi nol)."""
    if previous is None or previous == 0:
        return None
    return round((current - previous) / previous * 100, 1)


def _sum_revenue(date_from, date_to, channel=None):
    sql = "SELECT COALESCE(SUM(revenue),0) v FROM sales_daily WHERE date BETWEEN ? AND ?"
    p = [date_from, date_to]
    if channel:
        sql += " AND channel=?"
        p.append(channel)
    return query_one(sql, p)["v"]


def _cogs(date_from, date_to, channel=None):
    """Harga pokok penjualan = sum(qty * cost_price) untuk baris yang punya SKU."""
    sql = """SELECT COALESCE(SUM(s.qty * p.cost_price),0) v
             FROM sales_daily s JOIN products p ON s.sku = p.sku
             WHERE s.date BETWEEN ? AND ?"""
    pr = [date_from, date_to]
    if channel:
        sql += " AND s.channel=?"
        pr.append(channel)
    return query_one(sql, pr)["v"]


def _ad_cost(date_from, date_to, channel=None):
    sql = "SELECT COALESCE(SUM(cost),0) v FROM ad_spend_daily WHERE date BETWEEN ? AND ?"
    p = [date_from, date_to]
    if channel:
        sql += " AND channel=?"
        p.append(channel)
    return query_one(sql, p)["v"]


def _latest_data_date():
    """Tanggal data penjualan terakhir (acuan 'hari ini' versi data)."""
    row = query_one("SELECT MAX(date) d FROM sales_daily")
    return (row and row["d"]) or _today()


# ──────────────────────────────────────────────────────────────
# A. Executive Summary
# ──────────────────────────────────────────────────────────────
def executive():
    today = _latest_data_date()
    yest = _shift(today, -1)
    month = _month_of(today)
    month_start = month + "-01"

    omset_today = _sum_revenue(today, today)
    omset_yest = _sum_revenue(yest, yest)
    omset_mtd = _sum_revenue(month_start, today)

    tgt = query_one("SELECT revenue_target FROM targets WHERE month=? AND channel='ALL'", (month,))
    target = tgt["revenue_target"] if tgt else 0

    # estimasi laba MTD = omset - HPP - biaya iklan.
    # Hanya dihitung kalau HPP (cost_price) memang sudah diisi; kalau belum,
    # laba/margin = None supaya tidak menampilkan angka palsu (modal dianggap 0).
    cogs_mtd = _cogs(month_start, today)
    ad_mtd = _ad_cost(month_start, today)
    hpp_set = query_one("SELECT COUNT(*) c FROM products WHERE cost_price > 0")["c"]
    if hpp_set and cogs_mtd > 0:
        est_profit = round(omset_mtd - cogs_mtd - ad_mtd)
        margin_pct = round(est_profit / omset_mtd * 100, 1) if omset_mtd else 0
    else:
        est_profit = None
        margin_pct = None

    # proyeksi akhir bulan (linear dari run-rate)
    day_n = int(today[8:10])
    days_in_month = _days_in_month(today)
    projected = round(omset_mtd / day_n * days_in_month) if day_n else 0

    return {
        "data_date": today,
        "omset_today": omset_today,
        "omset_yesterday": omset_yest,
        "omset_today_vs_yest_pct": pct_change(omset_today, omset_yest),
        "omset_mtd": omset_mtd,
        "target_month": target,
        "achievement_pct": round(omset_mtd / target * 100, 1) if target else None,
        "projected_month": projected,
        "projected_vs_target_pct": round(projected / target * 100, 1) if target else None,
        "est_profit_mtd": est_profit,
        "margin_pct": margin_pct,
        "hpp_missing": not (hpp_set and cogs_mtd > 0),
    }


def _days_in_month(date_str):
    y, m = int(date_str[:4]), int(date_str[5:7])
    nm = datetime(y + (m == 12), (m % 12) + 1, 1)
    return (nm - timedelta(days=1)).day


# ──────────────────────────────────────────────────────────────
# B. Marketplace Performance (per channel)
# ──────────────────────────────────────────────────────────────
def channels():
    today = _latest_data_date()
    month_start = _month_of(today) + "-01"
    week_from = _shift(today, -6)
    prev_week_from = _shift(today, -13)
    prev_week_to = _shift(today, -7)

    out = []
    chans = query("SELECT name FROM channels WHERE is_active=1 ORDER BY name")
    for c in chans:
        name = c["name"]
        # total bulan berjalan (MTD)
        m = query_one(
            """SELECT COALESCE(SUM(order_count),0) oc, COALESCE(SUM(revenue),0) rv
               FROM sales_daily WHERE date BETWEEN ? AND ? AND channel=?""",
            (month_start, today, name),
        )
        orders = m["oc"]
        rev_mtd = m["rv"]
        aov = round(rev_mtd / orders) if orders else 0
        # pertumbuhan: minggu ini vs minggu lalu (WoW)
        rev_week = _sum_revenue(week_from, today, name)
        rev_prev = _sum_revenue(prev_week_from, prev_week_to, name)
        out.append({
            "channel": name,
            "revenue": rev_mtd,
            "revenue_7d": rev_week,
            "orders": orders,
            "aov": aov,
            "growth_pct": pct_change(rev_week, rev_prev),
        })
    out.sort(key=lambda x: x["revenue"], reverse=True)
    return {"period": "Bulan berjalan", "channels": out}


# ──────────────────────────────────────────────────────────────
# C. Produk Terlaris (Top 10)
# ──────────────────────────────────────────────────────────────
def top_products(limit=10):
    today = _latest_data_date()
    month_start = _month_of(today) + "-01"
    week_from = _shift(today, -6)
    prev_from = _shift(today, -13)
    prev_to = _shift(today, -7)

    def ranked(d_from, d_to):
        return query(
            """SELECT s.sku, COALESCE(p.name, s.sku) name, p.category, p.cost_price,
                      SUM(s.qty) qty, SUM(s.revenue) revenue
               FROM sales_daily s LEFT JOIN products p ON s.sku = p.sku
               WHERE s.date BETWEEN ? AND ? AND s.sku IS NOT NULL
               GROUP BY s.sku ORDER BY revenue DESC""",
            (d_from, d_to),
        )

    # ranking ditampilkan berdasar bulan berjalan; perubahan rank pakai momentum mingguan
    mtd = ranked(month_start, today)
    cur7 = {r["sku"]: i + 1 for i, r in enumerate(ranked(week_from, today))}
    prev7 = {r["sku"]: i + 1 for i, r in enumerate(ranked(prev_from, prev_to))}

    out = []
    for i, r in enumerate(mtd[:limit]):
        rank = i + 1
        cr = cur7.get(r["sku"])
        pr = prev7.get(r["sku"])
        rank_change = (pr - cr) if (cr and pr) else None  # positif = naik peringkat
        margin = None
        if r["cost_price"]:
            margin = round((r["revenue"] - r["qty"] * r["cost_price"]) / r["revenue"] * 100, 1) if r["revenue"] else 0
        out.append({
            "rank": rank,
            "sku": r["sku"],
            "name": r["name"],
            "category": r["category"] or "-",
            "revenue": r["revenue"],
            "qty": r["qty"],
            "margin_pct": margin,
            "rank_change": rank_change,
            "is_new": r["sku"] not in prev7,
        })
    return {"period": "Bulan berjalan", "products": out}


# ──────────────────────────────────────────────────────────────
# D. Advertising Performance
# ──────────────────────────────────────────────────────────────
def advertising():
    today = _latest_data_date()
    week_from = _shift(today, -6)

    total_cost = _ad_cost(week_from, today)
    total_attr = query_one(
        "SELECT COALESCE(SUM(revenue_attributed),0) v FROM ad_spend_daily WHERE date BETWEEN ? AND ?",
        (week_from, today),
    )["v"]
    total_rev = _sum_revenue(week_from, today)

    roas = round(total_attr / total_cost, 2) if total_cost else None
    acos = round(total_cost / total_attr * 100, 1) if total_attr else None
    tacos = round(total_cost / total_rev * 100, 1) if total_rev else None

    per_channel = []
    chans = query("SELECT name FROM channels WHERE is_active=1")
    for c in chans:
        name = c["name"]
        cost = _ad_cost(week_from, today, name)
        attr = query_one(
            "SELECT COALESCE(SUM(revenue_attributed),0) v FROM ad_spend_daily WHERE date BETWEEN ? AND ? AND channel=?",
            (week_from, today, name),
        )["v"]
        if cost == 0 and attr == 0:
            continue
        per_channel.append({
            "channel": name,
            "cost": cost,
            "roas": round(attr / cost, 2) if cost else None,
            "tacos": round(cost / _sum_revenue(week_from, today, name) * 100, 1)
                     if _sum_revenue(week_from, today, name) else None,
        })

    rated = [c for c in per_channel if c["roas"] is not None]
    best = max(rated, key=lambda x: x["roas"]) if rated else None
    worst = min(rated, key=lambda x: x["roas"]) if rated else None

    return {
        "period": "7 hari terakhir",
        "total_cost": total_cost,
        "roas": roas,
        "acos": acos,
        "tacos": tacos,
        "per_channel": per_channel,
        "best_channel": best,
        "worst_channel": worst,
    }


# ──────────────────────────────────────────────────────────────
# E. Inventory Alert
# ──────────────────────────────────────────────────────────────
def inventory():
    today = _latest_data_date()
    snap_date = query_one("SELECT MAX(date) d FROM inventory_snapshot")
    snap_date = snap_date and snap_date["d"]
    if not snap_date:
        return {"low_stock": [], "dead_stock": [], "snapshot_date": None}

    week_from = _shift(today, -6)
    stocks = query(
        """SELECT i.sku, COALESCE(p.name, i.sku) name, i.stock_qty
           FROM inventory_snapshot i LEFT JOIN products p ON i.sku=p.sku
           WHERE i.date=?""",
        (snap_date,),
    )

    low = []
    for s in stocks:
        sold = query_one(
            "SELECT COALESCE(SUM(qty),0) q FROM sales_daily WHERE sku=? AND date BETWEEN ? AND ?",
            (s["sku"], week_from, today),
        )["q"]
        daily_rate = sold / 7.0
        stock = s["stock_qty"]
        # hanya produk yang SEDANG LAKU yang relevan untuk "hampir habis"
        if daily_rate <= 0:
            continue
        days_left = 0 if stock <= 0 else round(stock / daily_rate)
        if days_left <= 7:
            low.append({
                "sku": s["sku"], "name": s["name"],
                "stock_qty": stock, "days_left": days_left,
            })
    low.sort(key=lambda x: x["days_left"])

    # produk diam: ada stok tapi tidak terjual >30 hari
    cutoff = _shift(today, -30)
    dead = []
    for s in stocks:
        if s["stock_qty"] <= 0:
            continue
        recent = query_one(
            "SELECT COALESCE(SUM(qty),0) q FROM sales_daily WHERE sku=? AND date > ?",
            (s["sku"], cutoff),
        )["q"]
        if recent == 0:
            dead.append({"sku": s["sku"], "name": s["name"], "stock_qty": s["stock_qty"]})

    return {
        "snapshot_date": snap_date,
        "low_stock": low,
        "dead_stock": dead[:20],
    }


# ──────────────────────────────────────────────────────────────
# F. Cashflow Monitoring
# ──────────────────────────────────────────────────────────────
def cashflow():
    today = _latest_data_date()
    week_from = _shift(today, -6)

    cash_in = query_one("SELECT COALESCE(SUM(amount),0) v FROM cash_ledger WHERE type='in'")["v"]
    cash_out = query_one("SELECT COALESCE(SUM(amount),0) v FROM cash_ledger WHERE type='out'")["v"]
    balance = cash_in - cash_out

    ap = query_one("SELECT COALESCE(SUM(amount),0) v FROM cash_ledger WHERE type='ap' AND is_settled=0")["v"]
    ar = query_one("SELECT COALESCE(SUM(amount),0) v FROM cash_ledger WHERE type='ar' AND is_settled=0")["v"]

    week_out = query_one(
        "SELECT COALESCE(SUM(amount),0) v FROM cash_ledger WHERE type='out' AND date BETWEEN ? AND ?",
        (week_from, today),
    )["v"]
    breakdown = query(
        """SELECT COALESCE(category,'Lainnya') label, COALESCE(SUM(amount),0) amount
           FROM cash_ledger WHERE type='out' AND date BETWEEN ? AND ?
           GROUP BY category ORDER BY amount DESC""",
        (week_from, today),
    )

    # prediksi kas 30 hari: saldo + piutang masuk - hutang jatuh tempo - run-rate pengeluaran
    avg_daily_out = week_out / 7.0
    projected_out_30 = avg_daily_out * 30
    ap_due_30 = query_one(
        "SELECT COALESCE(SUM(amount),0) v FROM cash_ledger WHERE type='ap' AND is_settled=0 AND (due_date IS NULL OR due_date <= ?)",
        (_shift(today, 30),),
    )["v"]
    projected_cash_30 = round(balance + ar - ap_due_30 - projected_out_30)

    return {
        "balance": round(balance),
        "accounts_payable": round(ap),
        "accounts_receivable": round(ar),
        "week_expenses": round(week_out),
        "breakdown": breakdown,
        "projected_cash_30d": projected_cash_30,
        "projected_negative": projected_cash_30 < 0,
    }


def full_snapshot():
    """Semua section sekaligus — dipakai alert engine & daily brief."""
    return {
        "executive": executive(),
        "channels": channels(),
        "products": top_products(),
        "advertising": advertising(),
        "inventory": inventory(),
        "cashflow": cashflow(),
    }
