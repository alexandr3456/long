import requests
import pandas as pd
import time
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("❌ TELEGRAM_TOKEN missing")

# список монет (не делай 100+ сразу)
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "TONUSDT","XRPUSDT"]

subscribers = set()

# ================= API =================
def get_klines(symbol):
    url = "https://api.bybit.com/v5/market/kline"
    params = {
        "symbol": symbol,
        "interval": "5",
        "limit": 250  # важно!
    }

    response = requests.get(url, params=params)
    data = response.json()

    if "result" not in data:
        return None

    candles = data["result"]["list"]
    candles.reverse()

    df = pd.DataFrame(candles, columns=[
        "time", "open", "high", "low", "close", "volume", "turnover"
    ])

    df["close"] = df["close"].astype(float)
    df["volume"] = df["volume"].astype(float)

    return df


# ================= СТРАТЕГИЯ =================
def analyze(symbol):
    df = get_klines(symbol)
    if df is None or len(df) < 200:
        return None

    # EMA
    df["ema200"] = df["close"].ewm(span=200).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()

    # RSI
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()

    rs = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))

    # объём
    df["vol_avg"] = df["volume"].rolling(20).mean()

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # условия стратегии
    trend = last["close"] > last["ema200"]
    pullback = last["close"] <= last["ema50"] * 1.01
    rsi_signal = prev["rsi"] < 40 and last["rsi"] > prev["rsi"]
    volume_signal = last["volume"] > last["vol_avg"]

    if trend and pullback and rsi_signal and volume_signal:
        return {
            "symbol": symbol,
            "price": last["close"],
            "rsi": round(last["rsi"], 2)
        }

    return None


# ================= ОТПРАВКА =================
async def send_signal(bot: Bot, signal):
    text = (
        f"🚀 LONG сигнал\n\n"
        f"{signal['symbol']}\n"
        f"Цена: {signal['price']}\n"
        f"RSI: {signal['rsi']}"
    )

    for user_id in subscribers:
        try:
            await bot.send_message(chat_id=user_id, text=text)
        except:
            pass


# ================= ЦИКЛ =================
async def scan_market(app):
    bot = app.bot

    print("🔍 Сканирую рынок...")

    for symbol in SYMBOLS:
        signal = analyze(symbol)

        if signal:
            print(f"✅ SIGNAL: {symbol}")
            await send_signal(bot, signal)

        time.sleep(0.2)  # чтобы не было Connection pool error


# ================= КОМАНДЫ =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subscribers.add(update.effective_chat.id)
    await update.message.reply_text("✅ Ты подписан на сигналы!")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subscribers.discard(update.effective_chat.id)
    await update.message.reply_text("❌ Ты отписан.")


# ================= MAIN =================
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))

    scheduler = BackgroundScheduler()
    scheduler.add_job(lambda: app.create_task(scan_market(app)), "interval", minutes=5)
    scheduler.start()

    print("🚀 Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
