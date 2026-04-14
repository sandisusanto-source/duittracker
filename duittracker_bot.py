import os
import json
import csv
import logging
import threading
from datetime import datetime, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler,
    MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)
from flask import Flask, jsonify, send_from_directory, request

# Token dari environment variable Railway
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", 5000))

DATA_FILE = "expenses.json"

CATEGORIES = {
    "food": "Makanan",
    "transport": "Transport",
    "shopping": "Belanja",
    "bills": "Tagihan",
    "health": "Kesehatan",
    "entertainment": "Hiburan",
    "education": "Pendidikan",
    "other": "Lainnya",
}

CAT_ICONS = {
    "food": "\U0001f35c",
    "transport": "\U0001f697",
    "shopping": "\U0001f6cd",
    "bills": "\U0001f4c4",
    "health": "\U0001f48a",
    "entertainment": "\U0001f3ac",
    "education": "\U0001f4da",
    "other": "\U0001f4e6",
}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def format_rupiah(amount):
    return "Rp {:,.0f}".format(amount).replace(",", ".")


async def start(update, ctx):
    welcome = (
        "DuitTracker Bot\n"
        "================\n\n"
        "Hai! Aku bot pencatat pengeluaran kamu.\n\n"
        "Cara Pakai:\n"
        "- Catat: /catat 50000 makan siang\n"
        "- Atau ketik langsung: 50000 makan siang\n"
        "- Laporan bulan ini: /laporan\n"
        "- Hari ini: /hari\n"
        "- Riwayat: /riwayat\n"
        "- Per kategori: /kategori\n"
        "- Hapus: /hapus [nomor]\n"
        "- Export CSV: /export\n"
        "- Dashboard web: /web\n\n"
        "Yuk mulai catat pengeluaran!"
    )
    await update.message.reply_text(welcome)


async def web_link(update, ctx):
    railway_url = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if railway_url:
        url = "https://" + railway_url
    else:
        url = "http://localhost:" + str(PORT)
    await update.message.reply_text(
        "Dashboard Web DuitTracker:\n"
        + url + "\n\n"
        "Buka link di atas di browser kamu."
    )


async def catat(update, ctx):
    try:
        args = ctx.args
        if not args or len(args) < 2:
            await update.message.reply_text(
                "Format salah!\n\n"
                "Cara pakai:\n"
                "/catat [jumlah] [catatan]\n\n"
                "Contoh:\n"
                "/catat 50000 makan siang\n"
                "/catat 150000 bensin motor"
            )
            return

        amount = int(args[0].replace(".", "").replace(",", ""))
        note = " ".join(args[1:])

        ctx.user_data["pending"] = {
            "amount": amount,
            "note": note,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M"),
        }

        keyboard = []
        row = []
        for key in CATEGORIES:
            icon = CAT_ICONS.get(key, "")
            label = icon + " " + CATEGORIES[key]
            row.append(InlineKeyboardButton(label, callback_data="cat_" + key))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            format_rupiah(amount) + "\n" + note + "\n\nPilih kategori:",
            reply_markup=reply_markup,
        )

    except ValueError:
        await update.message.reply_text(
            "Jumlah harus angka!\nContoh: /catat 50000 makan siang"
        )


async def handle_category_selection(update, ctx):
    query = update.callback_query
    await query.answer()

    if not query.data.startswith("cat_"):
        return

    category = query.data.replace("cat_", "")
    pending = ctx.user_data.get("pending")

    if not pending:
        await query.edit_message_text("Data tidak ditemukan, coba catat ulang.")
        return

    expense = {
        "amount": pending["amount"],
        "note": pending["note"],
        "date": pending["date"],
        "time": pending["time"],
        "category": category,
        "wallet": "cash",
        "source": "telegram",
    }

    data = load_data()
    data.append(expense)
    save_data(data)

    del ctx.user_data["pending"]

    icon = CAT_ICONS.get(category, "")
    cat_label = icon + " " + CATEGORIES.get(category, "Lainnya")
    await query.edit_message_text(
        "Tercatat!\n"
        "================\n"
        + format_rupiah(expense["amount"]) + "\n"
        + expense["note"] + "\n"
        + cat_label + "\n"
        + expense["date"] + " " + expense["time"] + "\n\n"
        "Lihat dashboard: /web"
    )


async def hapus(update, ctx):
    try:
        args = ctx.args
        if not args:
            await update.message.reply_text(
                "Format: /hapus [nomor]\nLihat nomor di /riwayat"
            )
            return

        index = int(args[0]) - 1
        data = load_data()

        if index < 0 or index >= len(data):
            await update.message.reply_text("Nomor tidak valid!")
            return

        removed = data.pop(-(index + 1))
        save_data(data)

        await update.message.reply_text(
            "Dihapus:\n"
            + format_rupiah(removed["amount"]) + "\n"
            + removed["note"] + "\n"
            + removed["date"]
        )

    except (ValueError, IndexError):
        await update.message.reply_text("Format: /hapus [nomor]")


async def hari_ini(update, ctx):
    data = load_data()
    today_str = date.today().strftime("%Y-%m-%d")
    today_expenses = [e for e in data if e["date"] == today_str]

    if not today_expenses:
        await update.message.reply_text("Belum ada pengeluaran hari ini.")
        return

    total = sum(e["amount"] for e in today_expenses)
    lines = []
    for e in today_expenses:
        icon = CAT_ICONS.get(e.get("category", "other"), "")
        lines.append(
            icon + " " + format_rupiah(e["amount"]) + " - " + e["note"]
        )

    msg = (
        "Hari Ini (" + today_str + ")\n"
        "================\n\n"
        + "\n".join(lines)
        + "\n\n================\n"
        "Total: " + format_rupiah(total) + "\n"
        + str(len(today_expenses)) + " transaksi"
    )
    await update.message.reply_text(msg)


async def laporan(update, ctx):
    data = load_data()
    now = datetime.now()
    month_prefix = now.strftime("%Y-%m")
    monthly = [e for e in data if e["date"].startswith(month_prefix)]

    if not monthly:
        await update.message.reply_text("Belum ada data bulan ini.")
        return

    total = sum(e["amount"] for e in monthly)
    count = len(monthly)
    avg_daily = total // max(now.day, 1)

    by_cat = {}
    for e in monthly:
        cat = e.get("category", "other")
        by_cat[cat] = by_cat.get(cat, 0) + e["amount"]

    cat_lines = []
    for cat, amt in sorted(by_cat.items(), key=lambda x: -x[1]):
        icon = CAT_ICONS.get(cat, "")
        label = CATEGORIES.get(cat, "Lainnya")
        pct = (amt / total) * 100 if total > 0 else 0
        bar_full = int(pct / 5)
        bar_empty = 20 - bar_full
        bar = chr(9608) * bar_full + chr(9617) * bar_empty
        cat_lines.append(
            icon + " " + label + "\n"
            + bar + " " + "{:.0f}".format(pct) + "%\n"
            + format_rupiah(amt)
        )

    msg = (
        "Laporan " + now.strftime("%B %Y") + "\n"
        "================\n\n"
        "Total: " + format_rupiah(total) + "\n"
        "Transaksi: " + str(count) + "x\n"
        "Rata-rata/hari: " + format_rupiah(avg_daily) + "\n\n"
        "Per Kategori:\n\n"
        + "\n\n".join(cat_lines)
    )
    await update.message.reply_text(msg)


async def riwayat(update, ctx):
    data = load_data()
    recent = data[-10:][::-1]

    if not recent:
        await update.message.reply_text("Belum ada data.")
        return

    lines = []
    for i, e in enumerate(recent, 1):
        icon = CAT_ICONS.get(e.get("category", "other"), "")
        lines.append(
            str(i) + ". " + icon + " " + format_rupiah(e["amount"]) + "\n"
            "   " + e["note"] + "\n"
            "   " + e["date"] + " " + e.get("time", "")
        )

    msg = (
        "10 Transaksi Terakhir\n"
        "================\n\n"
        + "\n\n".join(lines)
        + "\n\nHapus: /hapus [nomor]"
    )
    await update.message.reply_text(msg)


async def kategori(update, ctx):
    data = load_data()
    now = datetime.now()
    month_prefix = now.strftime("%Y-%m")
    monthly = [e for e in data if e["date"].startswith(month_prefix)]

    if not monthly:
        await update.message.reply_text("Belum ada data bulan ini.")
        return

    total = sum(e["amount"] for e in monthly)
    by_cat = {}
    for e in monthly:
        cat = e.get("category", "other")
        by_cat[cat] = by_cat.get(cat, 0) + e["amount"]

    lines = []
    for cat, amt in sorted(by_cat.items(), key=lambda x: -x[1]):
        icon = CAT_ICONS.get(cat, "")
        label = CATEGORIES.get(cat, "Lainnya")
        pct = (amt / total) * 100 if total > 0 else 0
        lines.append(
            icon + " " + label + ": " + format_rupiah(amt)
            + " (" + "{:.0f}".format(pct) + "%)"
        )

    msg = (
        "Kategori - " + now.strftime("%B %Y") + "\n"
        "================\n\n"
        + "\n".join(lines)
        + "\n\nTotal: " + format_rupiah(total)
    )
    await update.message.reply_text(msg)


async def export_csv(update, ctx):
    data = load_data()
    if not data:
        await update.message.reply_text("Belum ada data untuk di-export.")
        return

    csv_file = "duittracker_export.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Tanggal", "Waktu", "Jumlah", "Kategori", "Catatan", "Wallet", "Sumber"])
        for e in data:
            writer.writerow([
                e.get("date", ""),
                e.get("time", ""),
                e.get("amount", 0),
                CATEGORIES.get(e.get("category", "other"), "Lainnya"),
                e.get("note", ""),
                e.get("wallet", "cash"),
                e.get("source", "manual"),
            ])

    with open(csv_file, "rb") as f:
        await update.message.reply_document(
            document=f,
            filename="DuitTracker_" + datetime.now().strftime("%Y%m%d") + ".csv",
            caption="Export " + str(len(data)) + " transaksi",
        )


async def handle_photo(update, ctx):
    photo = update.message.photo[-1]
    file = await ctx.bot.get_file(photo.file_id)
    file_path = "receipts/" + photo.file_id + ".jpg"
    os.makedirs("receipts", exist_ok=True)
    await file.download_to_drive(file_path)

    await update.message.reply_text(
        "Foto nota diterima dan tersimpan!\n\n"
        "OCR belum aktif.\n"
        "Untuk sementara, catat manual:\n"
        "/catat [jumlah] [catatan]"
    )


async def handle_text(update, ctx):
    text = update.message.text.strip()
    parts = text.split(None, 1)

    if not parts:
        return

    try:
        amount_str = parts[0].replace(".", "").replace(",", "")
        amount_str = amount_str.replace("k", "000").replace("K", "000")
        amount = int(amount_str)
        note = parts[1] if len(parts) > 1 else "Tanpa catatan"

        ctx.user_data["pending"] = {
            "amount": amount,
            "note": note,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M"),
        }

        keyboard = []
        row = []
        for key in CATEGORIES:
            icon = CAT_ICONS.get(key, "")
            label = icon + " " + CATEGORIES[key]
            row.append(InlineKeyboardButton(label, callback_data="cat_" + key))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        await update.message.reply_text(
            format_rupiah(amount) + "\n" + note + "\n\nPilih kategori:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except (ValueError, IndexError):
        await update.message.reply_text(
            "Aku ga ngerti. Coba:\n\n"
            "- /catat 50000 makan siang\n"
            "- Ketik: 50000 makan siang\n"
            "- Ketik: 50k kopi\n"
            "- /laporan\n"
            "- /riwayat"
        )


# ════════════════════════════════════════
# FLASK WEB SERVER
# ════════════════════════════════════════

web_app = Flask(__name__)
web_app.logger.setLevel(logging.WARNING)
wlog = logging.getLogger("werkzeug")
wlog.setLevel(logging.WARNING)


@web_app.route("/")
def index():
    return send_from_directory(".", "dashboard.html")


@web_app.route("/api/expenses")
def api_expenses():
    data = load_data()
    return jsonify(data)


@web_app.route("/api/expenses", methods=["POST"])
def api_add_expense():
    expense = request.json
    data = load_data()
    data.append(expense)
    save_data(data)
    return jsonify({"status": "ok"})


@web_app.route("/api/expenses/<int:idx>", methods=["DELETE"])
def api_delete_expense(idx):
    data = load_data()
    if 0 <= idx < len(data):
        removed = data.pop(idx)
        save_data(data)
        return jsonify({"status": "ok", "removed": removed})
    return jsonify({"status": "error"}), 404


@web_app.route("/api/summary")
def api_summary():
    data = load_data()
    now = datetime.now()
    month_prefix = now.strftime("%Y-%m")
    monthly = [e for e in data if e["date"].startswith(month_prefix)]

    total = sum(e["amount"] for e in monthly)
    count = len(monthly)
    avg = total // max(now.day, 1)

    by_cat = {}
    for e in monthly:
        cat = e.get("category", "other")
        by_cat[cat] = by_cat.get(cat, 0) + e["amount"]

    by_date = {}
    for e in monthly:
        d = e["date"]
        by_date[d] = by_date.get(d, 0) + e["amount"]

    today_str = date.today().strftime("%Y-%m-%d")
    today_total = sum(e["amount"] for e in data if e["date"] == today_str)

    return jsonify({
        "total_month": total,
        "count": count,
        "avg_daily": avg,
        "today": today_total,
        "by_category": by_cat,
        "by_date": by_date,
        "month": now.strftime("%B %Y"),
    })


def run_web():
    web_app.run(host="0.0.0.0", port=PORT, debug=False)


# ════════════════════════════════════════
# MAIN
# ════════════════════════════════════════

def main():
    if not BOT_TOKEN:
        print("=" * 50)
        print("ERROR: BOT_TOKEN belum diset!")
        print("Set di Railway Variables:")
        print("  BOT_TOKEN = token_dari_botfather")
        print("=" * 50)
        return

    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()
    print("Dashboard jalan di port " + str(PORT))

    print("=" * 50)
    print("DuitTracker AKTIF!")
    print("=" * 50)

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("catat", catat))
    app.add_handler(CommandHandler("hapus", hapus))
    app.add_handler(CommandHandler("hari", hari_ini))
    app.add_handler(CommandHandler("laporan", laporan))
    app.add_handler(CommandHandler("riwayat", riwayat))
    app.add_handler(CommandHandler("kategori", kategori))
    app.add_handler(CommandHandler("export", export_csv))
    app.add_handler(CommandHandler("web", web_link))
    app.add_handler(CallbackQueryHandler(handle_category_selection))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
