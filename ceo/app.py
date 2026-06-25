"""
CEO Dashboard "Cahaya Senja" — Flask app.

Menyediakan:
  - Web dashboard (mobile-first) di /
  - API agregasi read-only di /api/*
  - Upload file XLSX/CSV di /api/upload
  - Refresh alert + brief di /api/refresh
  - Scheduler harian (APScheduler) jam 07:00: hitung alert + brief, push Telegram

Dijalankan: python app.py
Env:
  ANTHROPIC_API_KEY  -> aktifkan Daily Brief via Claude (opsional)
  BOT_TOKEN, CEO_CHAT_ID -> push brief & alert ke Telegram pagi hari (opsional)
  PORT (default 5000)
"""

import os
import logging

from flask import Flask, jsonify, request, send_from_directory

import analytics
import alerts as alert_mod
from db import init_db, query, execute
from importer import import_file
import tiktok_importer
import accurate_importer

logging.basicConfig(level=logging.INFO)
logging.getLogger("werkzeug").setLevel(logging.WARNING)
logger = logging.getLogger("ceo")

PORT = int(os.environ.get("PORT", 5000))
HERE = os.path.dirname(__file__)

app = Flask(__name__)


# ──────────────────────── Web ────────────────────────
@app.route("/")
def index():
    return send_from_directory(HERE, "dashboard.html")


# ──────────────────────── API: section data ────────────────────────
@app.route("/api/executive")
def api_executive():
    return jsonify(analytics.executive())


@app.route("/api/channels")
def api_channels():
    return jsonify(analytics.channels())


@app.route("/api/products")
def api_products():
    return jsonify(analytics.top_products())


@app.route("/api/advertising")
def api_advertising():
    return jsonify(analytics.advertising())


@app.route("/api/inventory")
def api_inventory():
    return jsonify(analytics.inventory())


@app.route("/api/cashflow")
def api_cashflow():
    return jsonify(analytics.cashflow())


@app.route("/api/alerts")
def api_alerts():
    return jsonify({"alerts": alert_mod.get_alerts()})


@app.route("/api/brief")
def api_brief():
    b = alert_mod.get_latest_brief()
    return jsonify(b or {"body": "Belum ada brief. Upload data lalu klik Refresh.", "date": None})


@app.route("/api/overview")
def api_overview():
    """Satu panggilan untuk seluruh dashboard (hemat round-trip di mobile)."""
    snap = analytics.full_snapshot()
    snap["alerts"] = alert_mod.get_alerts()
    b = alert_mod.get_latest_brief()
    snap["brief"] = b or {"body": "Belum ada brief.", "date": None}
    return jsonify(snap)


# ──────────────────────── API: aksi ────────────────────────
@app.route("/api/upload", methods=["POST"])
def api_upload():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "Tidak ada file."}), 400
    results = []
    for f in request.files.getlist("file"):
        try:
            content = f.read()
            # urutan deteksi: TikTok -> Accurate -> importer umum
            res = tiktok_importer.detect_and_import(f.filename, content)
            if res is None:
                res = accurate_importer.detect_and_import(f.filename, content)
            if res is None:
                res = import_file(f.filename, content)
        except Exception as e:
            logger.exception("Import error")
            res = {"ok": False, "error": str(e), "filename": f.filename}
        results.append(res)
    # otomatis hitung ulang alert + brief setelah upload
    try:
        alert_mod.run_alert_engine()
        alert_mod.generate_brief()
    except Exception as e:
        logger.exception("Recompute error: %s", e)
    return jsonify({"ok": all(r.get("ok") for r in results), "results": results})


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Hapus semua data transaksi (mis. menghapus data contoh sebelum isi data asli)."""
    for t in ["sales_daily", "ad_spend_daily", "inventory_snapshot",
              "cash_ledger", "alerts", "daily_brief", "products"]:
        execute("DELETE FROM %s" % t)
    return jsonify({"ok": True, "message": "Semua data dihapus. Silakan upload data asli."})


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    n = alert_mod.run_alert_engine()
    body = alert_mod.generate_brief()
    return jsonify({"ok": True, "alerts_generated": n, "brief": body})


@app.route("/api/alerts/<int:aid>/read", methods=["POST"])
def api_mark_read(aid):
    execute("UPDATE alerts SET is_read=1 WHERE id=?", (aid,))
    return jsonify({"ok": True})


@app.route("/api/targets", methods=["POST"])
def api_set_target():
    """Set target bulanan cepat dari UI."""
    data = request.json or {}
    month = data.get("month")
    target = data.get("revenue_target")
    if not month or target is None:
        return jsonify({"ok": False, "error": "month & revenue_target wajib"}), 400
    execute(
        """INSERT INTO targets (month, channel, revenue_target) VALUES (?, 'ALL', ?)
           ON CONFLICT(month, channel) DO UPDATE SET revenue_target=excluded.revenue_target""",
        (month, float(target)),
    )
    return jsonify({"ok": True})


# ──────────────────────── Scheduler + Telegram push ────────────────────────
def _telegram_push(text):
    token = os.environ.get("BOT_TOKEN")
    chat_id = os.environ.get("CEO_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        import urllib.request
        import urllib.parse
        url = "https://api.telegram.org/bot%s/sendMessage" % token
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        urllib.request.urlopen(url, data=data, timeout=10)
    except Exception as e:
        logger.warning("Telegram push gagal: %s", e)


def morning_job():
    """Dijalankan tiap pagi: hitung alert, generate brief, kirim ke Telegram."""
    logger.info("Menjalankan morning job...")
    alert_mod.run_alert_engine()
    body = alert_mod.generate_brief()
    crit = [a for a in alert_mod.get_alerts() if a["severity"] == "critical"]
    msg = "☀️ CEO Brief Cahaya Senja\n\n" + body
    if crit:
        msg += "\n\n🔴 Alert kritis:\n" + "\n".join("• " + a["title"] for a in crit)
    _telegram_push(msg)


def start_scheduler():
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        sched = BackgroundScheduler(timezone=os.environ.get("TZ", "Asia/Jakarta"))
        sched.add_job(morning_job, "cron", hour=7, minute=0, id="morning_brief")
        sched.start()
        logger.info("Scheduler aktif — brief harian jam 07:00 WIB")
    except Exception as e:
        logger.warning("Scheduler tidak aktif: %s", e)


def _maybe_seed_demo():
    """Isi data contoh saat deploy demo (SEED_DEMO=1) jika DB masih kosong."""
    if os.environ.get("SEED_DEMO") != "1":
        return
    try:
        from db import query_one
        if query_one("SELECT 1 FROM sales_daily LIMIT 1"):
            return  # sudah ada data
        import seed
        seed.seed()
        alert_mod.run_alert_engine()
        alert_mod.generate_brief()
        logger.info("Data demo dimuat (SEED_DEMO=1).")
    except Exception as e:
        logger.warning("Gagal memuat data demo: %s", e)


def main():
    init_db()
    _maybe_seed_demo()
    start_scheduler()
    print("=" * 50)
    print("CEO Dashboard Cahaya Senja AKTIF")
    print("Dashboard: http://localhost:%d" % PORT)
    print("Brief via Claude:", "AKTIF" if os.environ.get("ANTHROPIC_API_KEY") else "fallback rule-based")
    print("=" * 50)
    app.run(host="0.0.0.0", port=PORT, debug=False)


if __name__ == "__main__":
    main()
