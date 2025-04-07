#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ContextTypes

# Устанавливаем кодировку для вывода
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        # Для Python < 3.7
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Настройка логирования
os.makedirs('logs', exist_ok=True)
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s', '%Y-%m-%d %H:%M:%S')
log_file = 'logs/bot.log'

my_handler = RotatingFileHandler(
    log_file, mode='a', maxBytes=5*1024*1024, 
    backupCount=2, encoding='utf-8', delay=0
)
my_handler.setFormatter(log_formatter)
my_handler.setLevel(logging.INFO)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.INFO)

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(my_handler)
root_logger.addHandler(console_handler)

# Установка кодировки для логгера
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO, encoding='utf-8')

# Словарь для хранения счетчика сообщений пользователей
user_message_counter = {}
# Отправлять сообщение о поддержке после каждого N сообщения
DONATION_MESSAGE_FREQUENCY = 3

# Функция для отправки уведомлений о поддержке автора
async def send_donation_message(update, context):
    """
    Отправляет сообщение о возможности поддержать автора
    
    Args:
        update: Объект обновления Telegram
        context: Контекст для получения данных бота
    """
    try:
        # Получаем данные для донатов из переменных окружения
        donate_account = os.getenv("YANDEX_MONEY", "41001XXXXX")
        
        # Формируем сообщение о поддержке
        donation_message = (
            "💰 *Поддержите автора бота!*\n\n"
            "Если бот помогает вам экономить время и деньги, "
            "поддержите его развитие небольшим донатом на ЮMoney:\n"
            f"`{donate_account}`"
        )
        
        # Отправляем сообщение в текущий чат
        await update.message.reply_text(
            donation_message,
            parse_mode="Markdown"
        )
        logging.info(f"Отправлено сообщение о донате пользователю {update.effective_user.id}")
    
    except Exception as e:
        logging.error(f"Ошибка при отправке сообщения о поддержке: {e}")

if __name__ == "__main__":
    logging.info("Запуск бота...")
    try:
        # Проверка на уже запущенный экземпляр бота
        pid_file = 'pid.lock'
        if os.path.exists(pid_file):
            try:
                with open(pid_file, 'r') as f:
                    old_pid = int(f.read().strip())
                
                # Проверяем, существует ли процесс с таким PID
                import psutil
                if psutil.pid_exists(old_pid):
                    logging.error(f"Бот уже запущен с PID {old_pid}. Остановите его перед запуском нового.")
                    print(f"Бот уже запущен с PID {old_pid}. Остановите его перед запуском нового.")
                    sys.exit(1)
                else:
                    logging.info(f"Обнаружен устаревший PID {old_pid}, процесс не существует. Продолжаем запуск.")
            except (ValueError, IOError) as e:
                logging.warning(f"Ошибка при чтении файла PID: {e}")
        
        # Записываем текущий PID в файл
        with open(pid_file, 'w') as f:
            f.write(str(os.getpid()))
        logging.info(f"Записан PID {os.getpid()} в {pid_file}")
        
        # Загружаем переменные окружения
        load_dotenv()
        
        # Импортируем основной модуль
        from telegram.ext import ApplicationBuilder
        from telegram.ext import CommandHandler, MessageHandler, CallbackQueryHandler, filters
        from telegram import Update
        
        # Импортируем необходимые функции из wb_bot
        import wb_bot
        from wb_bot import main, start, help_command, similar_command
        from wb_bot import handle_chatgpt_command, handle_gpt_message, error_handler
        from wb_bot import clean_cache, search_command
        # Импортируем обработчик кнопок из wb_bot вместо отдельного файла
        from wb_bot import button_callback_handler
        
        logging.info("Модуль wb_bot успешно импортирован")
        
        # Инициализация и запуск бота напрямую
        def run_bot():
            logging.info("Инициализация бота...")
            
            # Получаем токен
            token = os.getenv("TELEGRAM_TOKEN")
            if not token:
                logging.critical("Токен не настроен в .env файле!")
                raise ValueError("Токен бота не настроен. Проверьте файл .env")
            
            # Создаем приложение
            application = ApplicationBuilder().token(token).build()
            
            # Регистрация обработчиков
            application.add_handler(CommandHandler("start", start))
            application.add_handler(CommandHandler("help", help_command))
            application.add_handler(CommandHandler("similar", similar_command))
            application.add_handler(CommandHandler("search", search_command))  # Добавляем обработчик команды search
            
            # Обработчики для ChatGPT
            if os.getenv("OPENAI_API_KEY"):
                application.add_handler(CommandHandler("chat", wb_bot.handle_gpt_message))
                application.add_handler(CommandHandler("chatgpt", handle_chatgpt_command))
                application.add_handler(CommandHandler("ask", handle_chatgpt_command))  # /ask теперь использует ChatGPT
                
                # Обработчик для сообщений, начинающихся с 'gpt', 'chatgpt' или 'gemini'
                application.add_handler(
                    MessageHandler(filters.TEXT & filters.Regex(r'^(chatgpt|gpt|gemini)\s+'), wb_bot.handle_gpt_message)
                )
            else:
                logging.warning("API ключ OpenAI не настроен. Функции ChatGPT не будут доступны.")
            
            # Обработчик для кнопок обратного вызова
            application.add_handler(CallbackQueryHandler(button_callback_handler))
            
            # Создаем обертку для обработчика сообщений с поддержкой донатов
            async def message_handler_with_donations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
                """
                Обрабатывает все текстовые сообщения и отправляет сообщение о поддержке
                после каждого DONATION_MESSAGE_FREQUENCY сообщения от пользователя
                """
                # Вызываем оригинальный обработчик сообщений
                await wb_bot.handle_message(update, context)
                
                # Получаем ID пользователя
                user_id = update.effective_user.id
                
                # Увеличиваем счетчик сообщений для пользователя
                user_message_counter[user_id] = user_message_counter.get(user_id, 0) + 1
                
                # Проверяем, нужно ли отправить сообщение о поддержке
                if user_message_counter[user_id] % DONATION_MESSAGE_FREQUENCY == 0:
                    # Отправляем сообщение о поддержке
                    await send_donation_message(update, context)
            
            # Обработчик для всех текстовых сообщений с поддержкой донатов
            application.add_handler(MessageHandler(filters.TEXT, message_handler_with_donations))
            
            # Регистрация обработчика ошибок
            application.add_error_handler(error_handler)
            
            # Планировщик задач (очистка кэша)
            job_queue = application.job_queue
            job_queue.run_repeating(clean_cache, interval=3600, first=3600)  # Каждый час
            
            # Запускаем бота
            logging.info("Бот запущен и готов к работе!")
            
            # Устанавливаем команды бота для отображения подсказок в Telegram
            commands = [
                ("start", "Начать работу с ботом"),
                ("help", "Показать справку по командам"),
                ("similar", "Найти похожие товары дешевле"),
                ("search", "Поиск товаров на Wildberries"),
                ("ask", "Задать вопрос ChatGPT")
            ]
            
            # Регистрируем команды у Telegram, чтобы появлялись подсказки при вводе /
            try:
                # Команды бота будут установлены через асинхронный метод в основном цикле
                # через Job при запуске приложения
                logging.info("Команды бота будут зарегистрированы при запуске")
                
                # Добавляем задачу на установку команд после запуска бота
                async def setup_commands(context):
                    await context.bot.set_my_commands(commands)
                    logging.info("Команды бота успешно зарегистрированы")
                
                application.job_queue.run_once(setup_commands, when=1)
            except Exception as e:
                logging.error(f"Ошибка при установке команд бота: {e}")
            
            application.run_polling()
        
        # Запускаем бота
        run_bot()
        
    except ImportError as e:
        logging.error(f"Ошибка при импорте модулей: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logging.info("Бот остановлен пользователем")
    except Exception as e:
        logging.critical(f"Критическая ошибка при запуске бота: {e}", exc_info=True) 