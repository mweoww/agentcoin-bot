import requests
import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

def send_notification(message):
    """Kirim notifikasi ke Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, data=data)
    except:
        pass

def notify_start():
    send_notification("🚀 *Miner Started*\nBot aktif dan mulai mining!")

def notify_success(problem, answer, tx):
    send_notification(
        f"✅ *Mining Success*\n"
        f"Problem: `{problem[:50]}...`\n"
        f"Answer: `{answer}`\n"
        f"Tx: `{tx[:10]}...`"
    )

def notify_error(error):
    send_notification(f"❌ *Error*\n`{error}`")

def notify_daily_summary(cycles, solved, earnings):
    send_notification(
        f"📊 *Daily Summary*\n"
        f"Cycles: {cycles}\n"
        f"Solved: {solved}\n"
        f"AGC Earned: {earnings}"
    )
