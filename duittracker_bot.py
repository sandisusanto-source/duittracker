import os
import json
import csv
import logging
import threading
import base64
import re
import tempfile
from datetime import datetime, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler,
    MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)
from flask import Flask, jsonify, send_from_directory, request

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", 5000))
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDENTIALS", "")

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

FOOD_KEYWORDS = [
    "nasi", "mie", "ayam", "sapi", "ikan", "udang", "tahu", "tempe",
    "sayur", "soto", "bakso", "sate", "gado", "rendang", "gulai",
    "kopi", "teh", "jus", "susu", "es", "air", "cola", "fanta",
    "makan", "resto", "cafe", "warung", "kantin", "food", "drink",
    "rice", "chicken", "coffee", "tea", "juice", "milk", "water",
    "roti", "kue", "snack", "kerupuk", "sambal", "lauk", "naget",
    "burger", "pizza", "kentang", "topping", "latte", "cappuccino",
    "americano", "espresso", "matcha", "coklat", "chocolate",
    "tomoro", "starbucks", "kfc", "mcd", "mcdonalds", "hokben",
    "geprek", "padang", "warteg", "bakmi", "ramen", "dimsum",
    "martabak", "gorengan", "pisang", "mangga", "jeruk",
    "grab food", "gofood", "shopeefood",
]

TRANSPORT_KEYWORDS = [
    "bensin", "solar", "bbm", "pertamax", "pertalite",
    "parkir", "tol", "toll", "grab", "gojek", "taxi", "ojek",
    "bus", "kereta", "train", "tiket", "pesawat", "flight",
    "service", "servis", "oli", "ban", "sparepart",
    "shell", "pertamina", "bp", "vivo",
]

HEALTH_KEYWORDS = [
    "obat", "vitamin", "apotek", "pharmacy", "dokter", "doctor",
    "rumah sakit", "hospital", "klinik", "clinic", "medical",
    "paracetamol", "amoxicillin", "antibiotik",
]

BILLS_KEYWORDS = [
    "listrik", "pln", "air", "pdam", "internet", "wifi",
    "pulsa", "paket data", "telkomsel", "indosat", "xl",
    "bpjs", "asuransi", "insurance", "pajak", "tax",
    "sewa", "rent", "cicilan", "kredit",
]

ENTERTAINMENT_KEYWORDS = [
    "bioskop", "cinema", "film", "movie", "game", "voucher",
    "spotify", "netflix", "youtube", "disney", "nonton",
    "karaoke", "billiard", "bowling",
]

EDUCATION_KEYWORDS = [
    "buku", "book", "kursus", "course", "les", "tutor",
    "sekolah", "school", "kuliah", "university", "udemy",
    "training", "seminar", "workshop",
]

SHOPPING_KEYWORDS = [
    "baju", "celana", "sepatu", "sandal", "tas", "jaket",
    "toko", "shop", "store", "mall", "indomaret", "alfamart",
    "tokopedia", "shopee", "lazada", "blibli",
    "elektronik", "hp", "charger", "kabel", "aksesoris",
    "sabun", "shampoo", "sikat", "pasta", "tissue",
    "deterjen", "pel", "sapu",
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
    scores = {
        "food": 0,
        "transport": 0,
        "health": 0,
        "bills": 0,
        "entertainment": 0,
        "education": 0,
        "shopping": 0,
    }
    for kw in FOOD_KEYWORDS:
        if kw in lower:
            scores["food"] += 1
    for kw in TRANSPORT_KEYWORDS:
        if kw in lower:
            scores["transport"] += 1
    for kw in HEALTH_KEYWORDS:
        if kw in lower:
            scores["health"] += 1
    for kw in BILLS_KEYWORDS:
        if kw in lower:
            scores["bills"] += 1
    for kw in ENTERTAINMENT_KEYWORDS:
        if kw in lower:
            scores["entertainment"] += 1
    for kw in EDUCATION_KEYWORDS:
        if kw in lower:
            scores["education"] += 1
    for kw in SHOPPING_KEYWORDS:
        if kw in lower:
            scores["shopping"] += 1

    best = max(scores, key=scores.get)
    if scores[best] > 0:
        return best
    return "other"


def parse_receipt_text(ocr_text):
    lines = ocr_text.split("\n")
    store_name = ""
    items = []
    total = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if i < 5 and len(stripped) > 2 and not any(c.isdigit() for c in stripped[:3]):
            if not store_name and len(stripped) > 3:
                store_name = stripped

    price_pattern = re.compile(r"(\d{1,3}(?:[.,]\d{3})*(?:\.\d{2})?)\s*$")
    total_pattern = re.compile(r"(?:total|grand\s*total|jumlah|subtotal|sub\s*total)\s*[:\s]*(?:rp\.?\s*)?(\d{1,3}(?:[.,]\d{3})*)", re.IGNORECASE)

    for line in lines:
        total_match = total_pattern.search(line)
        if total_match:
            amount_str = total_match.group(1).replace(".", "").replace(",", "")
            try:
                found_total = int(amount_str)
                if found_total > total:
                    total = found_total
            except ValueError:
                pass

    for line in lines:
        stripped = line.strip()
        if not stripped or len(stripped) < 3:
            continue

        skip_words = ["tanggal", "date", "waktu", "time", "kasir", "cashier",
                      "no.", "order", "struk", "receipt", "terima kasih",
                      "thank", "alamat", "address", "telp", "phone",
                      "ppn", "tax", "diskon", "discount", "kembalian",
                      "change", "tunai", "cash", "debit", "credit",
                      "qris", "gopay", "ovo", "dana", "shopeepay",
                      "member", "poin", "point", "hotline", "customer",
                      "download", "feedback", "inclusive"]
        lower_line = stripped.lower()
        if any(sw in lower_line for sw in skip_words):
            continue

        price_match = price_pattern.search(stripped)
        if price_match:
            amount_str = price_match.group(1).replace(".", "").replace(",", "")
            try:
                price = int(amount_str)
                if 500 <= price <= 50000000:
                    name_part = stripped[:price_match.start()].strip()
                    name_part = re.sub(r"^[\d\s.x×*]+", "", name_part).strip()
                    name_part = re.sub(r"[Rr]p\.?\s*$", "", name_part).strip()
                    if len(name_part) > 1:
                        qty_match = re.search(r"(\d+)\s*[x×*]", stripped[:price_match.start()])
                        qty = int(qty_match.group(1)) if qty_match else 1
                        items.append({
                            "name": name_part,
                            "qty": qty,
                            "price": price,
                        })
            except ValueError:
                pass

    if not total and items:
        total = sum(i["price"] for i in items)

    if not total:
        all_numbers = re.findall(r"\d{1,3}(?:[.,]\d{3})+", ocr_text)
        amounts = []
        for n in all_numbers:
            try:
                val = int(n.replace(".", "").replace(",", ""))
                if val >= 1000:
                    amounts.append(val)
            except ValueError:
                pass
        if amounts:
            total = max(amounts)

    return {
        "store": store_name,
        "items": items,
        "total": total,
    }


def setup_google_credentials():
    if not GOOGLE_CREDS_JSON:
        return None
    try:
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump(creds_dict, tmp)
        tmp.close()
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = tmp.name
        return tmp.name
    except Exception as e:
        logger.error("Failed to setup Google credentials: " + str(e))
        return None


def ocr_image(image_bytes):
    try:
        from google.cloud import vision
        client = vision.ImageAnnotatorClient()
        image = vision.Image(content=image_bytes)
        response = client.text_detection(image=image)
        if response.error.message:
            logger.error("Vision API error: " + response.error.message)
            return ""
        texts = response.text_annotations
        if texts:
            return texts[0].description
        return ""
    except ImportError:
        logger.error("google-cloud-vision not installed")
        return ""
    except Exception as e:
        logger.error("OCR error: " + str(e))
        return ""


async def start(update, ctx):
    welcome = (
        "DuitTracker Bot\n"
        "================\n\n"
        "Hai! Aku bot pencatat pengeluaran kamu.\n\n"
        "Cara Pakai:\n"
        "- Foto nota/struk langsung kirim ke sini\n"
        "- Atau ketik: 50000 makan siang\n"
        "- Atau: /catat 50000 makan siang\n"
        "- Laporan: /laporan\n"
        "- Hari ini: /hari\n"
        "- Riwayat: /riwayat\n"
        "- Kategori: /kategori\n"
        "- Hapus: /hapus [nomor]\n"
        "- Export: /export\n"
        "- Dashboard: /web\n\n"
        "Langsung kirim foto nota aja!"
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
                "Format: /catat [jumlah] [catatan]\n"
                "Contoh: /catat 50000 makan siang\n\n"
                "Atau langsung kirim foto nota!"
            )
            return

        amount = int(args[0].replace(".", "").replace(",", ""))
        note = " ".join(args[1:])
        category = detect_category(note)

        expense = {
            "amount": amount,
            "note": note,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M"),
            "category": category,
            "wallet": "cash",
            "source": "telegram",
        }

        data = load_data()
        data.append(expense)
        save_data(data)

        icon = CAT_ICONS.get(category, "")
        cat_label = icon + " " + CATEGORIES.get(category, "Lainnya")

        keyboard = [[
            InlineKeyboardButton("Ubah Kategori", callback_data="chg_" + str(len(data) - 1))
        ]]

        await update.message.reply_text(
            "Tercatat!\n"
            "================\n"
            + format_rupiah(amount) + "\n"
            + note + "\n"
            + cat_label + " (otomatis)\n"
            + expense["date"] + " " + expense["time"] + "\n\n"
            "Kategori salah? Klik tombol di bawah.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except ValueError:
        await update.message.reply_text(
            "Jumlah harus angka!\nContoh: /catat 50000 makan siang"
        )


async def handle_category_change(update, ctx):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("chg_"):
        idx = query.data.replace("chg_", "")
        ctx.user_data["change_idx"] = int(idx)

        keyboard = []
        row = []
        for key in CATEGORIES:
            icon = CAT_ICONS.get(key, "")
            label = icon + " " + CATEGORIES[key]
            row.append(InlineKeyboardButton(label, callback_data="setcat_" + key))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        await query.edit_message_text(
            "Pilih kategori yang benar:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif query.data.startswith("setcat_"):
        category = query.data.replace("setcat_", "")
        idx = ctx.user_data.get("change_idx")

        if idx is not None:
            data = load_data()
            if 0 <= idx < len(data):
                data[idx]["category"] = category
                save_data(data)
                icon = CAT_ICONS.get(category, "")
                cat_label = icon + " " + CATEGORIES.get(category, "Lainnya")
                e = data[idx]
                await query.edit_message_text(
                    "Kategori diupdate!\n"
                    "================\n"
                    + format_rupiah(e["amount"]) + "\n"
                    + e["note"] + "\n"
                    + cat_label + "\n"
                    + e["date"] + " " + e.get("time", "")
                )
            else:
                await query.edit_message_text("Data tidak ditemukan.")
        else:
            await query.edit_message_text("Data tidak ditemukan.")

    elif query.data.startswith("conf_"):
        idx = int(query.data.replace("conf_", ""))
        await query.edit_message_text(
            query.message.text + "\n\nTersimpan!"
        )

    elif query.data.startswith("cat_"):
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
            + expense["date"] + " " + expense["time"]
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
    await update.message.reply_text("Membaca nota... tunggu sebentar.")

    try:
        photo = update.message.photo[-1]
        file = await ctx.bot.get_file(photo.file_id)
        image_bytes = await file.download_as_bytearray()

        ocr_text = ocr_image(bytes(image_bytes))

        if not ocr_text:
            await update.message.reply_text(
                "Tidak bisa membaca nota.\n"
                "Coba foto ulang dengan pencahayaan lebih baik,\n"
                "atau catat manual: /catat [jumlah] [catatan]"
            )
            return

        receipt = parse_receipt_text(ocr_text)
        category = detect_category(ocr_text)

        if receipt["total"] <= 0:
            await update.message.reply_text(
                "Nota terbaca tapi tidak bisa detect total.\n\n"
                "Teks yang terbaca:\n"
                + ocr_text[:500] + "\n\n"
                "Catat manual: /catat [jumlah] [catatan]"
            )
            return

        store = receipt["store"] if receipt["store"] else "Nota"
        items_text = ""
        if receipt["items"]:
            for item in receipt["items"][:8]:
                items_text += (
                    "  " + item["name"]
                    + " x" + str(item["qty"])
                    + " = " + format_rupiah(item["price"]) + "\n"
                )

        note = store
        if receipt["items"]:
            item_names = [i["name"] for i in receipt["items"][:3]]
            note = store + " - " + ", ".join(item_names)

        expense = {
            "amount": receipt["total"],
            "note": note,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M"),
            "category": category,
            "wallet": "cash",
            "source": "ocr",
            "items": receipt["items"][:8],
        }

        data = load_data()
        data.append(expense)
        save_data(data)

        icon = CAT_ICONS.get(category, "")
        cat_label = icon + " " + CATEGORIES.get(category, "Lainnya")

        keyboard = [[
            InlineKeyboardButton("Ubah Kategori", callback_data="chg_" + str(len(data) - 1))
        ]]

        msg = (
            "Nota terbaca!\n"
            "================\n"
        )
        if store:
            msg += "Toko: " + store + "\n"
        if items_text:
            msg += "\nItem:\n" + items_text
        msg += (
            "\nTotal: " + format_rupiah(receipt["total"]) + "\n"
            + cat_label + " (otomatis)\n"
            + expense["date"] + " " + expense["time"] + "\n\n"
            "Kategori salah? Klik tombol di bawah."
        )

        await update.message.reply_text(
            msg,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        logger.error("Photo handler error: " + str(e))
        await update.message.reply_text(
            "Error membaca nota: " + str(e) + "\n"
            "Catat manual: /catat [jumlah] [catatan]"
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
        category = detect_category(note)

        expense = {
            "amount": amount,
            "note": note,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M"),
            "category": category,
            "wallet": "cash",
            "source": "telegram",
        }

        data = load_data()
        data.append(expense)
        save_data(data)

        icon = CAT_ICONS.get(category, "")
        cat_label = icon + " " + CATEGORIES.get(category, "Lainnya")

        keyboard = [[
            InlineKeyboardButton("Ubah Kategori", callback_data="chg_" + str(len(data) - 1))
        ]]

        await update.message.reply_text(
            "Tercatat!\n"
            "================\n"
            + format_rupiah(amount) + "\n"
            + note + "\n"
            + cat_label + " (otomatis)\n"
            + expense["date"] + " " + expense["time"],
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except (ValueError, IndexError):
        await update.message.reply_text(
            "Kirim foto nota, atau ketik:\n"
            "50000 makan siang\n"
            "25k kopi\n"
            "/laporan\n"
            "/riwayat"
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

    setup_google_credentials()

    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()
    print("Dashboard jalan di port " + str(PORT))

    print("=" * 50)
    print("DuitTracker AKTIF!")
    if GOOGLE_CREDS_JSON:
        print("OCR: Google Cloud Vision AKTIF")
    else:
        print("OCR: TIDAK AKTIF (GOOGLE_CREDENTIALS belum diset)")
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
    app.add_handler(CallbackQueryHandler(handle_category_change))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
