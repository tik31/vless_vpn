Supports python-telegram-bot v20.x async API
"""

import os
import sys
import asyncio
import logging
from pathlib import Path
from typing import Set
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Пути к файлам
SCRIPT_DIR = Path(__file__).parent.resolve()
SUBSCRIBERS_FILE = SCRIPT_DIR / "subscribers.txt"
LAST_MESSAGE_FILE = SCRIPT_DIR / "last_message.txt"
PENDING_MESSAGE_FILE = SCRIPT_DIR / "pending_message.txt"

# Токен бота из переменной окружения
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN environment variable is not set!")
    sys.exit(1)


class TelegramBotDaemon:
    """Telegram Bot Daemon для работы в фоновом режиме"""
    
    def __init__(self):
        self.subscribers: Set[int] = self._load_subscribers()
        self.application: Application = None
        self.monitoring_task = None
        
    def _load_subscribers(self) -> Set[int]:
        """Загружает список подписчиков из файла"""
        if not SUBSCRIBERS_FILE.exists():
            return set()
        
        try:
            with open(SUBSCRIBERS_FILE, 'r') as f:
                return {int(line.strip()) for line in f if line.strip().isdigit()}
        except Exception as e:
            logger.error(f"Error loading subscribers: {e}")
            return set()
    
    def _save_subscriber(self, chat_id: int) -> bool:
        """Сохраняет подписчика в файл, если его там нет"""
        if chat_id in self.subscribers:
            return False
        
        try:
            with open(SUBSCRIBERS_FILE, 'a') as f:
                f.write(f"{chat_id}\n")
            self.subscribers.add(chat_id)
            logger.info(f"New subscriber added: {chat_id}")
            return True
        except Exception as e:
            logger.error(f"Error saving subscriber {chat_id}: {e}")
            return False
    
    def _get_last_message(self) -> str:
        """Получает последнее сообщение из файла"""
        if not LAST_MESSAGE_FILE.exists():
            return "Добро пожаловать! 👋 Вы подписались на уведомления."
        
        try:
            with open(LAST_MESSAGE_FILE, 'r', encoding='utf-8') as f:
                message = f.read().strip()
                return message if message else "Добро пожаловать! 👋 Вы подписались на уведомления."
        except Exception as e:
            logger.error(f"Error reading last message: {e}")
            return "Добро пожаловать! 👋 Вы подписались на уведомления."
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        chat_id = update.effective_chat.id
        
        # Сохраняем подписчика
        is_new = self._save_subscriber(chat_id)
        
        # Получаем последнее сообщение
        last_message = self._get_last_message()
        
        try:
            await update.message.reply_text(
                last_message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
            logger.info(f"User {chat_id} subscribed (new: {is_new})")
        except Exception as e:
            logger.error(f"Error sending message to {chat_id}: {e}")
    
    async def broadcast_message(self, message: str):
        """Рассылает сообщение всем подписчикам"""
        if not self.subscribers:
            logger.warning("No subscribers to send message to")
            return
        
        success_count = 0
        failed_count = 0
        
        for chat_id in self.subscribers:
            try:
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True
                )
                success_count += 1
                logger.info(f"Message sent to {chat_id}")
            except Exception as e:
                logger.error(f"Failed to send message to {chat_id}: {e}")
                failed_count += 1
        
        logger.info(f"Broadcast complete: {success_count} success, {failed_count} failed")
    
    async def monitor_pending_messages(self):
        """Мониторит файл pending_message.txt и отправляет сообщения"""
        while True:
            try:
                if PENDING_MESSAGE_FILE.exists():
                    # Читаем сообщение
                    with open(PENDING_MESSAGE_FILE, 'r', encoding='utf-8') as f:
                        message = f.read().strip()
                    
                    if message:
                        logger.info(f"Pending message detected: {message[:50]}...")
                        
                        # Сохраняем как последнее сообщение
                        with open(LAST_MESSAGE_FILE, 'w', encoding='utf-8') as f:
                            f.write(message)
                        
                        # Рассылаем всем подписчикам
                        await self.broadcast_message(message)
                    
                    # Удаляем файл после обработки
                    PENDING_MESSAGE_FILE.unlink()
                    logger.info("Pending message file processed and removed")
                
            except Exception as e:
                logger.error(f"Error monitoring pending messages: {e}")
            
            # Проверяем каждую секунду
            await asyncio.sleep(1)
    
    async def post_init(self, application: Application):
        """Callback после инициализации приложения"""
        self.application = application
        # Запускаем задачу мониторинга
        self.monitoring_task = asyncio.create_task(self.monitor_pending_messages())
        logger.info("Monitoring task started")
    
    async def post_shutdown(self, application: Application):
        """Callback перед завершением работы"""
        if self.monitoring_task:
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass
        logger.info("Monitoring task stopped")
    
    def run(self):
        """Запускает бота в режиме демона"""
        logger.info("Starting Telegram Bot Daemon...")
        
        # Создаём приложение
        self.application = (
            Application.builder()
            .token(BOT_TOKEN)
            .post_init(self.post_init)
            .post_shutdown(self.post_shutdown)
            .build()
        )
        
        # Добавляем обработчики
        self.application.add_handler(CommandHandler("start", self.start_command))
        
        # Запускаем polling
        logger.info("Bot is running. Press Ctrl+C to stop.")
        self.application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )


def send_notification(message: str):
    """Отправляет уведомление через демон (внешний вызов)"""
    try:
        # Проверяем, что сообщение не пустое
        if not message.strip():
            print("Error: Message cannot be empty", file=sys.stderr)
            sys.exit(1)
        
        # Создаём файл с pending сообщением
        with open(PENDING_MESSAGE_FILE, 'w', encoding='utf-8') as f:
            f.write(message)
        
        print(f"Notification queued successfully: {message[:50]}...")
        logger.info(f"Notification queued: {message[:50]}...")
        
    except Exception as e:
        print(f"Error queueing notification: {e}", file=sys.stderr)
        logger.error(f"Error queueing notification: {e}")
        sys.exit(1)


def main():
    """Главная функция"""
    # Если есть аргументы командной строки - это внешний вызов для отправки уведомления
    if len(sys.argv) > 1:
        message = " ".join(sys.argv[1:])
        send_notification(message)
    else:
        # Иначе запускаем демон
        daemon = TelegramBotDaemon()
        try:
            daemon.run()
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            sys.exit(1)


if __name__ == "__main__":
    main()

