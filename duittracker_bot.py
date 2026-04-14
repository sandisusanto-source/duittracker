import os
import json
import csv
import logging
import threading
import base64
import io
from datetime import datetime, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler,
    MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)
from flask import Flask, jsonify, send_from_directory, request
from PIL import Image
import anthropic

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
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

claude_client = None

SYSTEM_PROMPT = """Kamu adalah DuitTracker, asisten pencatat pengeluaran pribadi via Telegram.

TUGAS UTAMA:
1. Ketika user kirim FOTO NOTA/STRUK: baca semua item, harga, total, nama toko. Balas dalam format JSON.
2. Ketika user kirim TEKS berisi angka: catat sebagai pengeluaran.
3. Ketika user minta KOREKSI: update data yang sudah tercatat.
4. Ketika user tanya LAPORAN: berikan ringkasan.

FORMAT RESPONSE untuk foto nota - HARUS JSON valid di dalam tag <receipt>:
<receipt>
{"store":"nama toko","items":[{"name":"nama item","qty":1,"price":32000}],"total":61000,"category":"food","payment":"cash","note":"ringkasan singkat"}
</receipt>

FORMAT RESPONSE untuk koreksi data - HARUS JSON valid di dalam tag <correction>:
<correction>
{"action":"update","field":"amount","old_value":81000,"new_value":61000}
</correction>

FORMAT RESPONSE untuk catat manual - HARUS JSON valid di dalam tag <expense>:
<expense>
{"amount":50000,"note":"makan siang","category":"food"}
</expense>

KATEGORI yang tersedia: food, transport, shopping, bills, health, entertainment, education, other

ATURAN:
- Selalu balas dalam bahasa Indonesia yang santai dan ramah
- Untuk foto nota: baca SEMUA teks dengan teliti, perhatikan TOTAL dan GRAND TOTAL
- Perhatikan metode pembayaran (Cash, QRIS, GoPay, OVO, dll)
- Kalau nota tidak jelas, tanya user
- Kalau user koreksi, langsung update tanpa banyak tanya
- Setelah JSON tag, tambahkan pesan konfirmasi yang friendly
- JANGAN pernah mengarang data yang tidak ada di nota"""


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


def get_conversation(ctx):
    if "conversation" not in ctx.user_data:
        ctx.user_data["conversation"] = []
    return ctx.user_data["conversation"]


def add_to_conversation(ctx, role, content):
    conv = get_conversation(ctx)
    conv.append({"role": role, "content": content})
    if len(conv) > 20:
        ctx.user_data["conversation"] = conv[-20:]


def ask_claude(messages, image_data=None):
    if not claude_client:
        return None

    api_messages = []
    for msg in messages[-10:]:
        api_messages.append({"role": msg["role"], "content": msg["content"]})

    if image_data:
        last_msg = api_messages[-1] if api_messages else None
        img_content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": image_data,
                }
            },
            {
                "type": "text",
                "text": last_msg["content"] if last_msg and last_msg["role"] == "user" else "Baca nota ini dan extract semua data."
            }
        ]
        if last_msg and last_msg["role"] == "user":
            api_messages[-1]["content"] = img_content
        else:
            api_messages.append({"role": "user", "content": img_content})

    try:
        response = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            messages=api_messages,
        )
        return response.content[0].text
    except Exception as e:
        logger.error("Claude API error: " + str(e))
        return None


def parse_receipt_response(text):
    import re
    match = re.search(r"<receipt>\s*(\{.*?\})\s*</receipt>", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return None


def parse_correction_response(text):
    import re
    match = re.search(r"<correction>\s*(\{.*?\})\s*</correction>", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return None


def parse_expense_response(text):
    import re
    match = re.search(r"<expense>\s*(\{.*?\})\s*</expense>", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return None


def clean_response(text):
    import re
    text = re.sub(r"<receipt>.*?</receipt>", "", text, flags=re.DOTALL)
    text = re.sub(r"<correction>.*?</correction>", "", text, flags=re.DOTALL)
    text = re.sub(r"<expense>.*?</expense>", "", text, flags=re.DOTALL)
    return text.strip()


# ════════════════════════════════════════
# TELEGRAM HANDLERS
# ════════════════════════════════════════

async def start(update, ctx):
    ctx.user_data["conversation"] = []
    await update.message.reply_text(
        "DuitTracker Bot\n"
        "================\n\n"
        "Hai! Kirim aja foto nota, aku langsung baca.\n\n"
        "Atau ketik:\n"
        "- 50000 makan siang\n"
        "- 25k kopi\n\n"
        "Mau koreksi? Tinggal bilang aja.\n"
        "Misal: 'salah, harusnya 61000'\n\n"
        "Command:\n"
        "/laporan - ringkasan bulan ini\n"
        "/hari - pengeluaran hari ini\n"
        "/riwayat - 10 terakhir\n"
        "/hapus [no] - hapus transaksi\n"
        "/export - download CSV\n"
        "/web - buka dashboard\n"
        "/reset - reset percakapan"
    )


async def reset(update, ctx):
    ctx.user_data["conversation"] = []
    ctx.user_data.pop("last_expense_idx", None)
    await update.message.reply_text("Percakapan direset!")


async def web_link(update, ctx):
    rd = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    url = "https://" + rd if rd else "http://localhost:" + str(PORT)
    await update.message.reply_text("Dashboard: " + url)


async def catat(update, ctx):
    try:
        args = ctx.args
        if not args or len(args) < 2:
            await update.message.reply_text("Format: /catat 50000 makan siang")
            return
        amount = int(args[0].replace(".", "").replace(",", ""))
        note = " ".join(args[1:])

        add_to_conversation(ctx, "user", "/catat " + str(amount) + " " + note)
        response = ask_claude(get_conversation(ctx))

        category = "other"
        if response:
            exp = parse_expense_response(response)
            if exp and "category" in exp:
                category = exp["category"]
            add_to_conversation(ctx, "assistant", response)

        expense = {
            "amount": amount, "note": note,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M"),
            "category": category, "wallet": "cash", "source": "telegram",
        }
        data = load_data()
        data.append(expense)
        save_data(data)
        ctx.user_data["last_expense_idx"] = len(data) - 1

        icon = CAT_ICONS.get(category, "")
        cl = icon + " " + CATEGORIES.get(category, "Lainnya")

        friendly = clean_response(response) if response else ""
        msg = ("Tercatat!\n================\n"
            + format_rupiah(amount) + "\n" + note + "\n" + cl + "\n"
            + expense["date"] + " " + expense["time"])
        if friendly:
            msg += "\n\n" + friendly

        await update.message.reply_text(msg)

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
        await query.edit_message_text("Pilih kategori:",
            reply_markup=InlineKeyboardMarkup(keyboard))

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
                    + format_rupiah(e["amount"]) + "\n" + e["note"]
                    + "\n" + cl + "\n" + e["date"])
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
            "Dihapus: " + format_rupiah(removed["amount"]) + " - " + removed["note"])
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
        + "\n".join(lines) + "\n\n================\nTotal: "
        + format_rupiah(total) + "\n" + str(len(today_exp)) + " transaksi")


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
        "Total: " + format_rupiah(total) + "\nTransaksi: " + str(count)
        + "x\nRata-rata/hari: " + format_rupiah(avg) + "\n\nPer Kategori:\n\n"
        + "\n\n".join(cat_lines))


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

        img = Image.open(io.BytesIO(bytes(image_bytes)))
        if img.mode == "RGBA":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        caption = update.message.caption or "Baca nota ini dan extract semua data."
        add_to_conversation(ctx, "user", caption)

        response = ask_claude(get_conversation(ctx), image_data=b64)

        if not response:
            await msg.edit_text("Gagal membaca nota. Coba lagi atau ketik manual.")
            return

        add_to_conversation(ctx, "assistant", response)

        receipt = parse_receipt_response(response)

        if receipt and receipt.get("total", 0) > 0:
            expense = {
                "amount": receipt["total"],
                "note": receipt.get("note", receipt.get("store", "Nota")),
                "date": datetime.now().strftime("%Y-%m-%d"),
                "time": datetime.now().strftime("%H:%M"),
                "category": receipt.get("category", "other"),
                "wallet": receipt.get("payment", "cash"),
                "source": "ocr",
                "store": receipt.get("store", ""),
                "items": receipt.get("items", []),
            }

            data = load_data()
            data.append(expense)
            save_data(data)
            ctx.user_data["last_expense_idx"] = len(data) - 1

            icon = CAT_ICONS.get(expense["category"], "")
            cl = icon + " " + CATEGORIES.get(expense["category"], "Lainnya")

            result = "Tercatat!\n================\n"
            if receipt.get("store"):
                result += "Toko: " + receipt["store"] + "\n"
            if receipt.get("items"):
                result += "\nItem:\n"
                for item in receipt["items"][:8]:
                    result += ("  " + item.get("name", "")
                        + " x" + str(item.get("qty", 1))
                        + " = " + format_rupiah(item.get("price", 0)) + "\n")
            result += ("\nTotal: " + format_rupiah(receipt["total"]) + "\n"
                + cl + "\n" + expense["date"] + " " + expense["time"])

            friendly = clean_response(response)
            if friendly:
                result += "\n\n" + friendly

            result += "\n\nSalah? Tinggal bilang aja, aku perbaiki."

            keyboard = [[InlineKeyboardButton(
                "Ubah Kategori", callback_data="chg_" + str(len(data) - 1))]]

            await msg.edit_text(result, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            friendly = clean_response(response)
            await msg.edit_text(friendly if friendly else "Tidak bisa membaca nota. Coba foto ulang.")

    except Exception as e:
        logger.error("Photo error: " + str(e))
        await msg.edit_text("Error: " + str(e))


async def handle_text(update, ctx):
    text = update.message.text.strip()
    parts = text.split(None, 1)

    if not parts:
        return

    # Try parse as number first
    try:
        a = parts[0].replace(".", "").replace(",", "")
        a = a.replace("k", "000").replace("K", "000")
        a = a.replace("rb", "000").replace("jt", "000000")
        amount = int(a)
        note = parts[1] if len(parts) > 1 else "Tanpa catatan"

        add_to_conversation(ctx, "user", text)
        response = ask_claude(get_conversation(ctx))

        category = "other"
        if response:
            exp = parse_expense_response(response)
            if exp and "category" in exp:
                category = exp["category"]
            add_to_conversation(ctx, "assistant", response)

        expense = {
            "amount": amount, "note": note,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M"),
            "category": category, "wallet": "cash", "source": "telegram",
        }
        data = load_data()
        data.append(expense)
        save_data(data)
        ctx.user_data["last_expense_idx"] = len(data) - 1

        icon = CAT_ICONS.get(category, "")
        cl = icon + " " + CATEGORIES.get(category, "Lainnya")

        friendly = clean_response(response) if response else ""
        result = ("Tercatat!\n================\n"
            + format_rupiah(amount) + "\n" + note + "\n" + cl + "\n"
            + expense["date"] + " " + expense["time"])
        if friendly:
            result += "\n\n" + friendly

        keyboard = [[InlineKeyboardButton(
            "Ubah Kategori", callback_data="chg_" + str(len(data) - 1))]]

        await update.message.reply_text(result,
            reply_markup=InlineKeyboardMarkup(keyboard))
        return

    except (ValueError, IndexError):
        pass

    # Not a number - treat as conversation (correction, question, etc.)
    add_to_conversation(ctx, "user", text)

    # Add context about last expense
    last_idx = ctx.user_data.get("last_expense_idx")
    if last_idx is not None:
        data = load_data()
        if 0 <= last_idx < len(data):
            last_exp = data[last_idx]
            context = ("Data terakhir yang tercatat: "
                + json.dumps(last_exp, ensure_ascii=False))
            conv = get_conversation(ctx)
            if len(conv) >= 2:
                conv.insert(-1, {"role": "user", "content": context})

    response = ask_claude(get_conversation(ctx))

    if not response:
        await update.message.reply_text(
            "Maaf, aku ga ngerti. Coba:\n"
            "- Kirim foto nota\n"
            "- Ketik: 50000 makan siang\n"
            "- /laporan")
        return

    add_to_conversation(ctx, "assistant", response)

    # Check if Claude wants to correct something
    correction = parse_correction_response(response)
    if correction and last_idx is not None:
        data = load_data()
        if 0 <= last_idx < len(data):
            field = correction.get("field", "amount")
            new_val = correction.get("new_value")
            if field == "amount" and new_val:
                data[last_idx]["amount"] = int(new_val)
            elif field == "category" and new_val:
                data[last_idx]["category"] = new_val
            elif field == "note" and new_val:
                data[last_idx]["note"] = new_val
            save_data(data)

    # Check if Claude found an expense in the text
    exp_data = parse_expense_response(response)
    if exp_data and exp_data.get("amount"):
        expense = {
            "amount": exp_data["amount"],
            "note": exp_data.get("note", ""),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M"),
            "category": exp_data.get("category", "other"),
            "wallet": "cash", "source": "telegram",
        }
        data = load_data()
        data.append(expense)
        save_data(data)
        ctx.user_data["last_expense_idx"] = len(data) - 1

    friendly = clean_response(response)
    if friendly:
        await update.message.reply_text(friendly)
    else:
        await update.message.reply_text("Oke, sudah diupdate!")


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
    global claude_client

    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN belum diset!")
        return

    if ANTHROPIC_API_KEY:
        claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        print("Claude API: AKTIF")
    else:
        print("WARNING: ANTHROPIC_API_KEY belum diset! Bot jalan tanpa AI.")

    threading.Thread(target=run_web, daemon=True).start()

    print("=" * 50)
    print("DuitTracker v4 AKTIF!")
    print("OCR: Claude Vision (smart)")
    print("Dashboard: port " + str(PORT))
    print("=" * 50)

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("catat", catat))
    app.add_handler(CommandHandler("hapus", hapus))
    app.add_handler(CommandHandler("hari", hari_ini))
    app.add_handler(CommandHandler("laporan", laporan))
    app.add_handler(CommandHandler("riwayat", riwayat))
    app.add_handler(CommandHandler("export", export_csv))
    app.add_handler(CommandHandler("web", web_link))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
