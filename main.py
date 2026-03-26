import logging
import requests
import json
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Bot
from telegram.ext import Updater, CommandHandler, CallbackContext, Update
from telegram.ext import Dispatcher

# API для криптовалют
API_URL = "https://api.coingecko.com/api/v3/coins/markets"
CRYPTO_LIST = ["bitcoin", "ethereum", "litecoin"]  # Пример криптовалют
CURRENCY = "usd"

# Ваш токен бота
TELEGRAM_TOKEN = 'YOUR_BOT_TOKEN'

# Логирование
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# Функция для получения данных о криптовалютах
def get_crypto_data():
    params = {
        'vs_currency': CURRENCY,
        'ids': ','.join(CRYPTO_LIST)
    }
    response = requests.get(API_URL, params=params)
    return response.json()

# Функция для анализа криптовалют и отправки сигнала на лонг
def analyze_and_send_signal():
    data = get_crypto_data()

    for crypto in data:
        symbol = crypto['symbol'].upper()
        price = crypto['current_price']
        change_percentage = crypto['price_change_percentage_24h']
        
        # Простая логика для отправки сигнала на лонг, если цена растет
        if change_percentage > 5:  # Можно изменить порог по своему усмотрению
            signal = f"🚀 {symbol} в росте! Текущая цена: ${price}. Рост за 24ч: {change_percentage}%"
            send_signal_to_subscribers(signal)

# Функция для отправки сигнала подписчикам
def send_signal_to_subscribers(message: str):
    # Сюда можно добавить список ID пользователей, которые подписаны на рассылку
    subscribers = [123456789, 987654321]  # Пример ID пользователей
    bot = Bot(token=TELEGRAM_TOKEN)
    for user_id in subscribers:
        bot.send_message(chat_id=user_id, text=message)

# Команда /start
def start(update: Update, context: CallbackContext):
    update.message.reply_text("Привет! Я крипто-бот, буду сообщать тебе сигналы на лонг.")

# Запуск бота
def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dispatcher: Dispatcher = updater.dispatcher

    # Обработчик команды /start
    dispatcher.add_handler(CommandHandler("start", start))

    # Запуск планировщика задач
    scheduler = BackgroundScheduler()
    scheduler.add_job(analyze_and_send_signal, 'interval', minutes=5)  # Каждые 5 минут
    scheduler.start()

    # Запуск бота
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
