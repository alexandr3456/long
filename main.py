import asyncio
import os
import json
import logging
from datetime import datetime, timedelta
import pandas as pd
import pandas_ta_classic as ta
import ccxt
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

# ===================== CONFIG =====================
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("❌ TELEGRAM_TOKEN missing")

CHECK_INTERVAL = 5      # minutes
COOLDOWN_MINUTES = 30
DATA_FILE = "data.json"

# ===================== LOGGING =====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ===================== BOT =====================
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
subscribers = set()
last_signals = {}

# ===================== STORAGE =====================
def load_data():
    global subscribers
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
                subscribers = set(data.get("subscribers", []))
                logger.info(f"Loaded {len(subscribers)} subscribers")
        except Exception as e:
            logger.error(f"Failed to load data file: {e}")

def save_data():
    try:
        with open(DATA_FILE, "w") as f:
            json.dump({"subscribers": list(subscribers)}, f)
    except Exception as e:
        logger.error(f"Failed to save data file: {e}")

# ===================== EXCHANGE =====================
exchange = ccxt.bybit({
    "enableRateLimit": True,
    "options": {"defaultType": "future"}
})

# ===================== COMMANDS =====================
@dp.message()
async def handle_all(message: Message):
    text = message.text or ""
    if text.startswith("/start"):
        subscribers.add(message.chat.id)
        save_data()
        await message.answer("✅ Подписка на LONG сигналы включена")
    elif text.startswith("/stop"):
        subscribers.discard(message.chat.id)
        save_data()
        await message.answer("❌ Подписка отключена")
    elif text.startswith("/status"):
        await message.answer(f"👥 Подписчиков: {len(subscribers)}\n⚙️ Интервал: {CHECK_INTERVAL} мин")
    else:
        await message.answer("Бот работает 👍\n/start — подписаться на LONG сигналы")


# ===================== INDICATORS & STRATEGY =====================
def calculate_indicators(df):
    df["rsi"] = ta.rsi(df["close"], length=18)
    df["ema50"] = ta.ema(df["close"], length=50)
    df["ema200"] = ta.ema(df["close"], length=200)
    df["ema_dist"] = df["close"] / df["ema50"] - 1
    df["volume_ma"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_ma"]
    return df


def get_signal(df, funding_rate, open_interest):
    """
    Логика стратегии:
    - Тренд: price > EMA200
    - Отскок: RSI < 40 или в зоне 40–55 после роста за 20 свечей
    - Импульс объёма: >1.8× среднего
    - Цена чуть ниже EMA50 (дисконт)
    - Последняя свеча зелёная
    - Фандинг не слишком негативный
    - Есть открытый интерес
    """
    price = df["close"].iloc[-1]
    ema50 = df["ema50"].iloc[-1]
    ema200 = df["ema200"].iloc[-1]
    rsi = df["rsi"].iloc[-1]
    volume_ratio = df["volume_ratio"].iloc[-1]

    # 1. Трендовый фильтр: только long в восходящем тренде
    in_uptrend = price > ema200 if ema200 else False

    # 2. Рост за 20 свечей (примерно 100 минут на 5m)
    base_price = df["close"].iloc[-21] if len(df) >= 21 else df["close"].iloc[0]
    price_change_20 = (price / base_price - 1) * 100

    # 3. RSI: не покупка на силе, а отскок
    rsi_ok = rsi < 40 or (40 <= rsi < 55)

    # 4. Импульс объёма
    volume_spike = volume_ratio > 1.8 if volume_ratio > 0 else False

    # 5. Цена ниже EMA50, но не коллапс
    far_from_ema = price < ema50 * 0.97 and price > ema50 * 0.90 if ema50 else False

    # 6. Последняя свеча зелёная
    last_green = df["close"].iloc[-1] > df["open"].iloc[-1]

    # 7. Фандинг: не сверх‑негативный
    funding_ok = funding_rate > -0.01

    # 8. Open Interest: есть интерес
    oi_ok = open_interest > 0

    # Счётчик качества сигнала
    score = 0
    if in_uptrend:
        score += 2
    if price_change_20 > 3 and rsi_ok:
        score += 3
    if volume_spike:
        score += 2
    if far_from_ema:
        score += 2
    if last_green:
        score += 1
    if funding_ok:
        score += 1
    if oi_ok:
        score += 1

    meta = {
        "price_change_20": price_change_20,
        "rsi": rsi,
        "volume_ratio": volume_ratio,
        "ema_dist_50": ((price / ema50 - 1) * 100) if ema50 else 0,
        "ema_dist_200": ((price / ema200 - 1) * 100) if ema200 else 0,
        "funding": funding_rate,
        "oi": open_interest,
    }

    if score >= 8:
        return score, meta
    return 0, meta


# ===================== FETCH HELPERS =====================
async def fetch_ohlcv_async(symbol):
    try:
        return await asyncio.to_thread(
            exchange.fetch_ohlcv,
            symbol,
            timeframe="5m",
            limit=50,
            params={"category": "linear"}
        )
    except Exception as e:
        logger.error(f"fetch_ohlcv_async error for {symbol}: {e}")
        return []


async def fetch_funding(symbol):
    try:
        data = await asyncio.to_thread(exchange.fetch_funding_rate, symbol)
        return data.get("fundingRate", 0)
    except Exception as e:
        logger.error(f"fetch_funding error for {symbol}: {e}")
        return 0


async def fetch_oi(symbol):
    try:
        data = await asyncio.to_thread(
            exchange.fetch_open_interest,
            symbol,
            params={"category": "linear"}
        )
        return float(data.get("openInterest", 0))
    except Exception as e:
        logger.error(f"fetch_oi error for {symbol}: {e}")
        return 0


# ===================== CORE SCAN =====================
async def process_symbol(symbol):
    try:
        now = datetime.now()
        if symbol in last_signals and now - last_signals[symbol] < timedelta(minutes=COOLDOWN_MINUTES):
            return None

        ohlcv = await fetch_ohlcv_async(symbol)
        if not ohlcv or len(ohlcv) < 50:  # нужно 50+ для ema200
            return None

        df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
        df = calculate_indicators(df)

        # Пропускаем, если нет EMA200 рассчитанной
        if "ema200" not in df or df["ema200"].iloc[-10:].isna().all():
            return None

        funding = await fetch_funding(symbol)
        oi = await fetch_oi(symbol)

        score, data = get_signal(df, funding, oi)

        if score >= 8:
            last_signals[symbol] = now
            return symbol, score, data
    except Exception as e:
        logger.error(f"process_symbol error for {symbol}: {e}")
    return None


async def scan_market():
    logger.info("🔍 LONG Scan started")
    try:
        markets = await asyncio.to_thread(exchange.load_markets)
        symbols = [
            s for s, i in markets.items()
            if i.get("linear") and i.get("quote") == "USDT" and i.get("active", True)
        ]

        tasks = [process_symbol(s) for s in symbols[:100]]  # можно увеличить
        results = await asyncio.gather(*tasks)
        signals = [r for r in results if r]

        for symbol, score, d in signals:
            token = symbol.replace("USDT", "").replace(":", "")
            text = f"""
🚀 <b>LONG SIGNAL</b> — ${token}
🔥 Score: <b>{score}/13</b>
📈 Рост за 20 свечей: {d['price_change_20']:.2f}%
📉 RSI: {d['rsi']:.1f}
📊 Volume ratio: x{d['volume_ratio']:.1f}
📐 EMA50 dist: {d['ema_dist_50']:.1f}%
📐 EMA200 dist: {d['ema_dist_200']:.1f}%
💰 Funding: {d['funding']:.4f}%
📊 OI: {d['oi']:.0f}
🕒 {datetime.now().strftime('%H:%M:%S')}
🔗 https://www.bybit.com/trade/perpetual/{symbol}
            """.strip()

            for user in list(subscribers):
                try:
                    await bot.send_message(
                        user,
                        text,
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
                except Exception as e:
                    logger.warning(f"Failed to send to {user}: {e}")
                    if "chat not found" in str(e).lower():
                        subscribers.discard(user)

        logger.info(f"✅ LONG signals sent: {len(signals)}")
    except Exception as e:
        logger.error(f"Scan error: {e}")


# ===================== MAIN =====================
async def main():
    load_data()
    logger.info("🚀 LONG Signal Bot started")
    await bot.delete_webhook(drop_pending_updates=True)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(scan_market, "interval", minutes=CHECK_INTERVAL)
    scheduler.start()

    async def delayed_scan():
        await asyncio.sleep(3)
        await scan_market()

    asyncio.create_task(delayed_scan())

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
