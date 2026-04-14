import os
import json
import csv
import logging
import threading
import re
import io
from datetime import datetime, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler,
    MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)
from flask import Flask, jsonify, send_from_directory, request
from PIL import Image, ImageEnhance, ImageFilter
import pytesseract

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

FOOD_KW = [
    "nasi", "mie", "ayam", "sapi", "ikan", "udang", "tahu", "tempe",
    "sayur", "soto", "bakso", "sate", "rendang", "gulai", "geprek",
    "kopi", "teh", "jus", "susu", "es", "air", "cola", "fanta",
    "makan", "resto", "cafe", "warung", "kantin", "food", "drink",
    "rice", "chicken", "coffee", "tea", "juice", "milk", "water",
    "roti", "kue", "snack", "kerupuk", "sambal", "lauk",
    "burger", "pizza", "kentang", "topping", "latte", "cappuccino",
    "americano", "espresso", "matcha", "coklat", "chocolate",
    "tomoro", "starbucks", "kfc", "mcd", "hokben", "padang",
    "bakmi", "ramen", "dimsum", "martabak", "gorengan",
    "grab food", "gofood", "shopeefood", "aren", "iced",
]
TRANSPORT_KW = [
    "bensin", "solar", "bbm", "pertamax", "pertalite",
    "parkir", "tol", "grab", "gojek", "taxi", "ojek",
    "bus", "kereta", "tiket", "pesawat", "service", "servis",
    "oli", "ban", "shell", "pertamina",
]
HEALTH_KW = [
    "obat", "vitamin", "apotek", "dokter", "rumah sakit",
    "klinik", "medical", "paracetamol",
]
BILLS_KW = [
    "listrik", "pln", "pdam", "internet", "wifi",
    "pulsa", "telkomsel", "indosat", "xl",
    "bpjs", "asuransi", "pajak", "sewa", "cicilan",
]
ENTERTAIN_KW = [
    "bioskop", "cinema", "film", "game", "voucher",
    "spotify", "netflix", "nonton", "karaoke",
]
EDUCATION_KW = [
    "buku", "book", "kursus", "course", "les",
    "sekolah", "kuliah", "udemy", "training",
]
SHOPPING_KW = [
    "baju", "celana", "sepatu", "sandal", "tas", "jaket",
    "toko", "shop", "mall", "indomaret", "alfamart",
    "tokopedia", "shopee", "lazada", "elektronik",
    "charger", "kabel", "aksesoris", "sabun", "shampoo",
    "deterjen",
]

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


def detect_category(text):
    lower = text.lower()
    scores = {"food": 0, "transport": 0, "health": 0, "bills": 0,
              "entertainment": 0, "education": 0, "shopping": 0}
    for kw in FOOD_KW:
        if kw in lower:
            scores["food"] += 1
    for kw in TRANSPORT_KW:
        if kw in lower:
            scores["transport"] += 1
    for kw in HEALTH_KW:
        if kw in lower:
            scores["health"] += 1
    for kw in BILLS_KW:
        if kw in lower:
            scores["bills"] += 1
    for kw in ENTERTAIN_KW:
        if kw in lower:
            scores["entertainment"] += 1
    for kw in EDUCATION_KW:
        if kw in lower:
            scores["education"] += 1
    for kw in SHOPPING_KW:
        if kw in lower:
            scores["shopping"] += 1
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "other"


def ocr_receipt(image_bytes):
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = img.convert("L")
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(2.0)
        enhancer2 = ImageEnhance.Sharpness(img)
        img = enhancer2.enhance(2.0)
        img = img.filter(ImageFilter.MedianFilter(size=3))
        width, height = img.size
        if width < 1000:
            ratio = 1500 / width
            img = img.resize((1500, int(height * ratio)), Image.LANCZOS)
        text = pytesseract.image_to_string(img, lang="ind+eng",
            config="--psm 6 --oem 3")
        if not text.strip():
            text = pytesseract.image_to_string(img, lang="ind+eng",
                config="--psm 4 --oem 3")
        if not text.strip():
            text = pytesseract.image_to_string(img, lang="ind+eng",
                config="--psm 3 --oem 3")
        return text
    except Exception as e:
        logger.error("OCR error: " + str(e))
        return ""


def parse_receipt(ocr_text):
    lines = ocr_text.split("\n")
    store_name = ""
    items = []
    total = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if i < 6 and len(stripped) > 2:
            upper_count = sum(1 for c in stripped if c.isupper())
            if upper_count > len(stripped) * 0.4 and not any(c.isdigit() for c in stripped[:4]):
                if not store_name and len(stripped) > 2:
                    clean = re.sub(r"[^a-zA-Z\s]", "", stripped).strip()
                    if len(clean) > 2:
                        store_name = clean

    total_pattern = re.compile(
        r"(?:total|grand\s*total|jumlah|amount)\s*[:\s]*(?:rp\.?\s*)?(\d{1,3}(?:[.,]\d{3})*(?:\.\d{2})?)",
        re.IGNORECASE)
    for line in lines:
        m = total_pattern.search(line)
        if m:
            amt_str = m.group(1).replace(".", "").replace(",", "")
            try:
                val = int(amt_str)
                if val > total:
                    total = val
            except ValueError:
                pass

    price_pat = re.compile(r"(\d{1,3}(?:[.,]\d{3})+)\s*$")
    skip_words = ["tanggal", "date", "waktu", "time", "kasir", "cashier",
                   "no", "order", "struk", "receipt", "terima kasih",
                   "thank", "alamat", "telp", "phone", "ppn", "tax",
                   "diskon", "discount", "kembalian", "change", "tunai",
                   "cash", "debit", "credit", "qris", "gopay", "ovo",
                   "dana", "shopeepay", "member", "poin", "hotline",
                   "customer", "download", "feedback", "inclusive",
                   "subtotal", "sub total", "rounding"]
    for line in lines:
        stripped = line.strip()
        if not stripped or len(stripped) < 4:
            continue
        lower_line = stripped.lower()
        if any(sw in lower_line for sw in skip_words):
            continue
        pm = price_pat.search(stripped)
        if pm:
            amt_str = pm.group(1).replace(".", "").replace(",", "")
            try:
                price = int(amt_str)
                if 500 <= price <= 50000000:
                    name = stripped[:pm.start()].strip()
                    name = re.sub(r"^[\d\s.x*]+", "", name).strip()
                    name = re.sub(r"[Rr]p\.?\s*$", "", name).strip()
                    if len(name) > 1:
                        items.append({"name": name, "qty": 1, "price": price})
            except ValueError:
                pass

    if not total and items:
        total = sum(i["price"] for i in items)

    if not total:
        all_nums = re.findall(r"\d{1,3}(?:[.,]\d{3})+", ocr_text)
        amounts = []
        for n in all_nums:
            try:
                val = int(n.replace(".", "").replace(",", ""))
                if val >= 1000:
                    amounts.append(val)
            except ValueError:
                pass
        if amounts:
            total = max(amounts)

    return {"store": store_name, "items": items, "total": total}


# ════════════════════════════════════════
# TELEGRAM HANDLERS
# ════════════════════════════════════════

async def start(update, ctx):
    await update.message.reply_text(
        "DuitTracker Bot\n"
        "================\n\n"
        "Cara Pakai:\n"
        "- Kirim foto nota/struk\n"
        "- Ketik: 50000 makan siang\n"
        "- Ketik: 50k kopi\n"
        "- /laporan - ringkasan bulan ini\n"
        "- /hari - pengeluaran hari ini\n"
        "- /riwayat - 10 terakhir\n"
        "- /kategori - per kategori\n"
        "- /hapus [no] - hapus transaksi\n"
        "- /export - download CSV\n"
        "- /web - buka dashboard\n\n"
        "Langsung kirim foto nota aja!"
    )


async def web_link(update, ctx):
    rd = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    url = "https://" + rd if rd else "http://localhost:" + str(PORT)
    await update.message.reply_text("Dashboard: " + url)


async def catat(update, ctx):
    try:
        args = ctx.args
        if not args or len(args) < 2:
            await update.message.reply_text(
                "Format: /catat 50000 makan siang\n"
                "Atau langsung kirim foto nota!"
            )
            return
        amount = int(args[0].replace(".", "").replace(",", ""))
        note = " ".join(args[1:])
        category = detect_category(note)
        expense = {
            "amount": amount, "note": note,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M"),
            "category": category, "wallet": "cash", "source": "telegram",
        }
        data = load_data()
        data.append(expense)
        save_data(data)
        icon = CAT_ICONS.get(category, "")
        cat_label = icon + " " + CATEGORIES.get(category, "Lainnya")
        keyboard = [[InlineKeyboardButton(
            "Ubah Kategori", callback_data="chg_" + str(len(data) - 1))]]
        await update.message.reply_text(
            "Tercatat!\n================\n"
            + format_rupiah(amount) + "\n" + note + "\n"
            + cat_label + " (otomatis)\n"
            + expense["date"] + " " + expense["time"],
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except ValueError:
        await update.message.reply_text("Jumlah harus angka!")


async def handle_callback(update, ctx):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("chg_"):
        idx = int(query.data.replace("chg_", ""))
        ctx.user_data["change_idx"] = idx
        keyboard = []
        row = []
        for key in CATEGORIES:
            icon = CAT_ICONS.get(key, "")
            label = icon + " " + CATEGORIES[key]
            row.append(InlineKeyboardButton(label, callback_data="sc_" + key))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        await query.edit_message_text(
            "Pilih kategori:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data.startswith("sc_"):
        cat = query.data.replace("sc_", "")
        idx = ctx.user_data.get("change_idx")
        if idx is not None:
            data = load_data()
            if 0 <= idx < len(data):
                data[idx]["category"] = cat
                save_data(data)
                e = data[idx]
                icon = CAT_ICONS.get(cat, "")
                cl = icon + " " + CATEGORIES.get(cat, "Lainnya")
                await query.edit_message_text(
                    "Updated!\n================\n"
                    + format_rupiah(e["amount"]) + "\n"
                    + e["note"] + "\n" + cl + "\n"
                    + e["date"] + " " + e.get("time", ""))
                return
        await query.edit_message_text("Data tidak ditemukan.")


async def hapus(update, ctx):
    try:
        args = ctx.args
        if not args:
            await update.message.reply_text("Format: /hapus [nomor dari /riwayat]")
            return
        index = int(args[0]) - 1
        data = load_data()
        if index < 0 or index >= len(data):
            await update.message.reply_text("Nomor tidak valid!")
            return
        removed = data.pop(-(index + 1))
        save_data(data)
        await update.message.reply_text(
            "Dihapus: " + format_rupiah(removed["amount"])
            + " - " + removed["note"])
    except (ValueError, IndexError):
        await update.message.reply_text("Format: /hapus [nomor]")


async def hari_ini(update, ctx):
    data = load_data()
    ts = date.today().strftime("%Y-%m-%d")
    today_exp = [e for e in data if e["date"] == ts]
    if not today_exp:
        await update.message.reply_text("Belum ada pengeluaran hari ini.")
        return
    total = sum(e["amount"] for e in today_exp)
    lines = []
    for e in today_exp:
        icon = CAT_ICONS.get(e.get("category", "other"), "")
        lines.append(icon + " " + format_rupiah(e["amount"]) + " - " + e["note"])
    await update.message.reply_text(
        "Hari Ini (" + ts + ")\n================\n\n"
        + "\n".join(lines)
        + "\n\n================\nTotal: " + format_rupiah(total)
        + "\n" + str(len(today_exp)) + " transaksi")


async def laporan(update, ctx):
    data = load_data()
    now = datetime.now()
    mp = now.strftime("%Y-%m")
    monthly = [e for e in data if e["date"].startswith(mp)]
    if not monthly:
        await update.message.reply_text("Belum ada data bulan ini.")
        return
    total = sum(e["amount"] for e in monthly)
    count = len(monthly)
    avg = total // max(now.day, 1)
    by_cat = {}
    for e in monthly:
        c = e.get("category", "other")
        by_cat[c] = by_cat.get(c, 0) + e["amount"]
    cat_lines = []
    for c, a in sorted(by_cat.items(), key=lambda x: -x[1]):
        icon = CAT_ICONS.get(c, "")
        label = CATEGORIES.get(c, "Lainnya")
        pct = (a / total) * 100 if total > 0 else 0
        bf = int(pct / 5)
        bar = chr(9608) * bf + chr(9617) * (20 - bf)
        cat_lines.append(icon + " " + label + "\n" + bar
            + " " + "{:.0f}".format(pct) + "%\n" + format_rupiah(a))
    await update.message.reply_text(
        "Laporan " + now.strftime("%B %Y") + "\n================\n\n"
        "Total: " + format_rupiah(total) + "\n"
        "Transaksi: " + str(count) + "x\n"
        "Rata-rata/hari: " + format_rupiah(avg) + "\n\n"
        "Per Kategori:\n\n" + "\n\n".join(cat_lines))


async def riwayat(update, ctx):
    data = load_data()
    recent = data[-10:][::-1]
    if not recent:
        await update.message.reply_text("Belum ada data.")
        return
    lines = []
    for i, e in enumerate(recent, 1):
        icon = CAT_ICONS.get(e.get("category", "other"), "")
        lines.append(str(i) + ". " + icon + " " + format_rupiah(e["amount"])
            + "\n   " + e["note"] + "\n   " + e["date"] + " " + e.get("time", ""))
    await update.message.reply_text(
        "10 Terakhir\n================\n\n"
        + "\n\n".join(lines) + "\n\nHapus: /hapus [nomor]")


async def kategori(update, ctx):
    data = load_data()
    now = datetime.now()
    mp = now.strftime("%Y-%m")
    monthly = [e for e in data if e["date"].startswith(mp)]
    if not monthly:
        await update.message.reply_text("Belum ada data bulan ini.")
        return
    total = sum(e["amount"] for e in monthly)
    by_cat = {}
    for e in monthly:
        c = e.get("category", "other")
        by_cat[c] = by_cat.get(c, 0) + e["amount"]
    lines = []
    for c, a in sorted(by_cat.items(), key=lambda x: -x[1]):
        icon = CAT_ICONS.get(c, "")
        label = CATEGORIES.get(c, "Lainnya")
        pct = (a / total) * 100 if total > 0 else 0
        lines.append(icon + " " + label + ": " + format_rupiah(a)
            + " (" + "{:.0f}".format(pct) + "%)")
    await update.message.reply_text(
        "Kategori - " + now.strftime("%B %Y") + "\n================\n\n"
        + "\n".join(lines) + "\n\nTotal: " + format_rupiah(total))


async def export_csv(update, ctx):
    data = load_data()
    if not data:
        await update.message.reply_text("Belum ada data.")
        return
    cf = "export.csv"
    with open(cf, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Tanggal", "Waktu", "Jumlah", "Kategori", "Catatan"])
        for e in data:
            w.writerow([e.get("date", ""), e.get("time", ""), e.get("amount", 0),
                CATEGORIES.get(e.get("category", "other"), "Lainnya"), e.get("note", "")])
    with open(cf, "rb") as f:
        await update.message.reply_document(document=f,
            filename="DuitTracker_" + datetime.now().strftime("%Y%m%d") + ".csv",
            caption="Export " + str(len(data)) + " transaksi")


async def handle_photo(update, ctx):
    msg = await update.message.reply_text("Membaca nota...")

    try:
        photo = update.message.photo[-1]
        file = await ctx.bot.get_file(photo.file_id)
        image_bytes = await file.download_as_bytearray()

        ocr_text = ocr_receipt(bytes(image_bytes))

        if not ocr_text or len(ocr_text.strip()) < 10:
            await msg.edit_text(
                "Tidak bisa membaca nota.\n"
                "Coba foto ulang lebih jelas, atau ketik manual:\n"
                "50000 makan siang")
            return

        receipt = parse_receipt(ocr_text)
        category = detect_category(ocr_text)

        if receipt["total"] <= 0:
            await msg.edit_text(
                "Nota terbaca tapi total tidak ditemukan.\n\n"
                "Teks:\n" + ocr_text[:400] + "\n\n"
                "Ketik manual: 50000 makan siang")
            return

        store = receipt["store"] if receipt["store"] else "Nota"
        note = store
        if receipt["items"]:
            names = [i["name"] for i in receipt["items"][:3]]
            note = store + " - " + ", ".join(names)

        expense = {
            "amount": receipt["total"], "note": note,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M"),
            "category": category, "wallet": "cash", "source": "ocr",
        }

        data = load_data()
        data.append(expense)
        save_data(data)

        icon = CAT_ICONS.get(category, "")
        cat_label = icon + " " + CATEGORIES.get(category, "Lainnya")

        items_text = ""
        if receipt["items"]:
            for item in receipt["items"][:6]:
                items_text += "  " + item["name"] + " = " + format_rupiah(item["price"]) + "\n"

        result = "Nota terbaca!\n================\n"
        if store:
            result += "Toko: " + store + "\n"
        if items_text:
            result += "\nItem:\n" + items_text
        result += ("\nTotal: " + format_rupiah(receipt["total"]) + "\n"
            + cat_label + " (otomatis)\n"
            + expense["date"] + " " + expense["time"])

        keyboard = [[InlineKeyboardButton(
            "Ubah Kategori", callback_data="chg_" + str(len(data) - 1))]]

        await msg.edit_text(result, reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        logger.error("Photo error: " + str(e))
        await msg.edit_text("Error: " + str(e) + "\nKetik manual: 50000 makan siang")


async def handle_text(update, ctx):
    text = update.message.text.strip()
    parts = text.split(None, 1)
    if not parts:
        return
    try:
        a = parts[0].replace(".", "").replace(",", "")
        a = a.replace("k", "000").replace("K", "000").replace("rb", "000").replace("jt", "000000")
        amount = int(a)
        note = parts[1] if len(parts) > 1 else "Tanpa catatan"
        category = detect_category(note)
        expense = {
            "amount": amount, "note": note,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M"),
            "category": category, "wallet": "cash", "source": "telegram",
        }
        data = load_data()
        data.append(expense)
        save_data(data)
        icon = CAT_ICONS.get(category, "")
        cl = icon + " " + CATEGORIES.get(category, "Lainnya")
        keyboard = [[InlineKeyboardButton(
            "Ubah Kategori", callback_data="chg_" + str(len(data) - 1))]]
        await update.message.reply_text(
            "Tercatat!\n================\n"
            + format_rupiah(amount) + "\n" + note + "\n"
            + cl + " (otomatis)\n"
            + expense["date"] + " " + expense["time"],
            reply_markup=InlineKeyboardMarkup(keyboard))
    except (ValueError, IndexError):
        await update.message.reply_text(
            "Kirim foto nota, atau ketik:\n50000 makan siang\n25k kopi\n/laporan")


# ════════════════════════════════════════
# FLASK WEB
# ════════════════════════════════════════

web_app = Flask(__name__)
web_app.logger.setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

@web_app.route("/")
def index():
    return send_from_directory(".", "dashboard.html")

@web_app.route("/api/expenses")
def api_expenses():
    return jsonify(load_data())

@web_app.route("/api/expenses", methods=["POST"])
def api_add():
    data = load_data()
    data.append(request.json)
    save_data(data)
    return jsonify({"status": "ok"})

@web_app.route("/api/expenses/<int:idx>", methods=["DELETE"])
def api_del(idx):
    data = load_data()
    if 0 <= idx < len(data):
        data.pop(idx)
        save_data(data)
        return jsonify({"status": "ok"})
    return jsonify({"status": "error"}), 404

@web_app.route("/api/summary")
def api_summary():
    data = load_data()
    now = datetime.now()
    mp = now.strftime("%Y-%m")
    monthly = [e for e in data if e["date"].startswith(mp)]
    total = sum(e["amount"] for e in monthly)
    by_cat = {}
    for e in monthly:
        c = e.get("category", "other")
        by_cat[c] = by_cat.get(c, 0) + e["amount"]
    by_date = {}
    for e in monthly:
        by_date[e["date"]] = by_date.get(e["date"], 0) + e["amount"]
    ts = date.today().strftime("%Y-%m-%d")
    return jsonify({
        "total_month": total, "count": len(monthly),
        "avg_daily": total // max(now.day, 1),
        "today": sum(e["amount"] for e in data if e["date"] == ts),
        "by_category": by_cat, "by_date": by_date,
        "month": now.strftime("%B %Y"),
    })

def run_web():
    web_app.run(host="0.0.0.0", port=PORT, debug=False)


# ════════════════════════════════════════
# MAIN
# ════════════════════════════════════════

def main():
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN belum diset di Railway Variables!")
        return

    threading.Thread(target=run_web, daemon=True).start()
    print("=" * 50)
    print("DuitTracker AKTIF!")
    print("OCR: Tesseract (built-in, no API key needed)")
    print("Dashboard: port " + str(PORT))
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
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
