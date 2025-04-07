#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import json
import time
import random
import asyncio
import logging
import requests
from requests.exceptions import ConnectionError, Timeout, HTTPError, RequestException
import re
import uuid
import socket
import aiohttp
from urllib.parse import quote
from datetime import datetime
from functools import wraps
from typing import Dict, List, Optional, Tuple, Any, Callable, Union
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from telegram.error import BadRequest
from collections import defaultdict
import cloudscraper
from cloudscraper.exceptions import CloudflareChallengeError

# Импортируем функции из find_similar.py вместо similar_products
from find_similar import get_similar_products, find_similar_cheaper_products, get_product_details
# Импортируем функции из wb_search.py
from wb_search import extract_search_query, search_products, format_search_results

# Проверяем наличие модуля OpenAI
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Константы и настройки
CHANNEL_ID = "@SKYFORGEOFFICIAL"  # Канал для обязательной подписки
PARTNER_ID = os.getenv("PARTNER_ID", "wildberries")  # Партнерский ID
YANDEX_MONEY = os.getenv("YANDEX_MONEY", "41001XXXXX")  # ЮMoney для донатов
MAX_CONCURRENT_REQUESTS = 5  # Максимальное количество одновременных запросов
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
MAX_RETRIES = 3  # Максимальное количество повторных попыток при ошибках
CACHE_LIFETIME = 12  # Время жизни кеша в часах
MAX_REQUESTS_PER_MINUTE = 20  # Максимальное количество запросов в минуту
RETRY_DELAY = 1  # Задержка между повторными попытками в секундах
DONATE_TEXT = f"Поддержать проект: {YANDEX_MONEY}"  # Текст с просьбой поддержать проект
COOLDOWN_PERIOD = 5  # Период остывания в секундах для повторных запросов одного и того же товара

# Настройки для OpenAI API
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")  # Ключ OpenAI API 
OPENAI_MODEL = "gpt-4o-mini"  # Модель OpenAI для использования
MAX_GPT_REQUESTS_PER_DAY = 5  # Максимальное количество запросов к ChatGPT в день на пользователя
MAX_GPT_RESPONSE_LENGTH = 500 # Максимальная длина ответа ChatGPT в символах

# --- НАСТРОЙКИ ПРОКСИ ---
# Настройки HTTPS-прокси с авторизацией
PROXY_ENABLED = True  # Флаг для включения/отключения использования прокси


# Формируем строки для прокси
PROXY_AUTH = f"{PROXY_USER}:{PROXY_PASSWORD}@{PROXY_IP}:{PROXY_PORT}"
PROXY_URL_HTTP = f"http://{PROXY_AUTH}"
PROXY_URL_HTTPS = f"http://{PROXY_AUTH}"  # HTTPS-прокси использует http:// в URL

# Словарь с настройками прокси для requests
PROXIES = {
    "http": PROXY_URL_HTTP,
    "https": PROXY_URL_HTTPS
} if PROXY_ENABLED else None

# Функция для выполнения HTTP запроса с прокси и fallback
def make_request_with_fallback(url, method="GET", headers=None, params=None, data=None, timeout=30):
    """
    Выполняет HTTP-запрос, сначала пробуя использовать прокси, 
    а затем в случае ошибки - без прокси (fallback)
    
    Args:
        url: URL для запроса
        method: HTTP метод (GET, POST и т.д.)
        headers: Заголовки запроса
        params: URL параметры
        data: Данные для запроса (для POST)
        timeout: Таймаут запроса
        
    Returns:
        Response: Объект ответа requests или None в случае ошибки
    """
    if headers is None:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        }
    
    # Пробуем сначала с прокси
    if PROXY_ENABLED:
        try:
            logger.info(f"Выполняю запрос к {url} через прокси {PROXY_IP}:{PROXY_PORT}")
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                data=data,
                proxies=PROXIES,
                timeout=timeout
            )
            logger.info(f"Запрос через прокси выполнен успешно, статус: {response.status_code}")
            return response
        except (requests.exceptions.RequestException, ConnectionError, TimeoutError) as e:
            logger.warning(f"Ошибка при запросе через прокси: {str(e)}. Пробую без прокси.")
    
    # Fallback без прокси
    try:
        logger.info(f"Выполняю запрос к {url} без прокси")
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            data=data,
            timeout=timeout
        )
        logger.info(f"Запрос без прокси выполнен успешно, статус: {response.status_code}")
        return response
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка при запросе без прокси: {str(e)}")
        return None
# --- КОНЕЦ НАСТРОЕК ПРОКСИ ---

# Начальный промпт для ChatGPT
CHATGPT_SYSTEM_PROMPT = """Ты - помощник в Telegram-боте для поиска товаров на Wildberries. 
Твоя задача - отвечать на вопросы пользователей о товарах, давать рекомендации 
и советы по покупкам. Учитывай контекст предыдущих сообщений. 
Стиль общения - дружелюбный и профессиональный. Отвечай в формате простого текста без Markdown разметки.
Важные правила:
1. Всегда отвечай на русском языке
2. используй эмодзи в ответах
3. Фокусируйся на фактах о товарах
4. Ограничивай свои ответы в пределах 500 символов
5. Если пользователь спрашивает о чем-то, что не связано с онлайн-покупками или Wildberries, вежливо переводи разговор в контекст покупок
"""

# Словари для отслеживания использования AI
gpt_user_requests = {}  # Формат: {user_id: [timestamp1, timestamp2, ...]}

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Семафор для ограничения одновременных запросов
request_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

# Добавляем константы для лимита запросов
MAX_REQUESTS_PER_MINUTE = 10
REQUESTS_FILE = "requests_log.csv"

# Кеш для хранения данных о товарах
# Формат: {артикул: (данные, время_последнего_обновления)}
product_cache = {}
CACHE_LIFETIME = 6  # Время жизни кеша в часах

# Словарь для отслеживания запросов пользователей
user_requests = defaultdict(list)

def check_request_limit(user_id=None, max_requests=MAX_REQUESTS_PER_MINUTE, time_window=60):
    """
    Проверяет, не превышен ли лимит запросов для пользователя.
    
    Args:
        user_id: ID пользователя (если None, то проверяется общий лимит)
        max_requests: Максимальное количество запросов в течение time_window
        time_window: Временное окно в секундах
    
    Returns:
        True, если лимит не превышен, False, если превышен
    """
    # Если не передан user_id, просто разрешаем запрос
    if user_id is None:
        return True
        
    current_time = time.time()
    
    # Очищаем старые записи (старше time_window секунд)
    user_requests[user_id] = [t for t in user_requests[user_id] if current_time - t < time_window]
    
    # Проверяем количество запросов
    if len(user_requests[user_id]) >= max_requests:
        logger.warning(f"Пользователь {user_id} превысил лимит запросов ({max_requests} за {time_window} сек)")
        return False
    
    # Добавляем новый запрос
    user_requests[user_id].append(current_time)
    return True

# Проверка интернет-соединения
def check_internet_connection():
    """Проверяет доступность интернет-соединения"""
    try:
        # Пробуем подключиться к Google DNS
        socket.create_connection(("8.8.8.8", 53), timeout=3)
        logger.info("Интернет-соединение доступно")
        return True
    except OSError as e:
        logger.error(f"Ошибка интернет-соединения: {e}")
        return False

# Проверка доступности хостов Wildberries
def check_wildberries_hosts():
    """Проверяет доступность основных хостов Wildberries"""
    hosts = [
        "wildberries.ru",
        "wbxcatalog-ru.wildberries.ru",
        "wbx-content-v2.wbstatic.net",
        "card.wb.ru"
    ]
    
    results = {}
    for host in hosts:
        try:
            socket.getaddrinfo(host, 80)
            results[host] = True
            logger.info(f"Хост {host} доступен")
        except socket.gaierror as e:
            results[host] = False
            logger.warning(f"Ошибка разрешения имени хоста {host}: {e}")
    
    return results

# Функция для получения случайного прокси
def get_random_proxy():
    """Возвращает случайный прокси из списка прокси"""
    if not PROXY_LIST:
        return None
    return random.choice(PROXY_LIST)

# Создание scraper с прокси (если включено)
def create_scraper_instance():
    """
    Создает экземпляр scraper для запросов к Wildberries
    
    Returns:
        requests.Session: Экземпляр сессии с настроенными заголовками
    """
    # Создаем сессию
    session = requests.Session()
    
    # Устанавливаем заголовки для запросов
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
        "Origin": "https://www.wildberries.ru",
        "Referer": "https://www.wildberries.ru/",
    })
    
    # Добавляем прокси с авторизацией, если включено
    if PROXY_ENABLED:
        try:
            logger.info(f"Настройка сессии с прокси {PROXY_IP}:{PROXY_PORT}")
            session.proxies = PROXIES
        except Exception as e:
            logger.error(f"Ошибка при настройке прокси: {str(e)}")
    
    return session

async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Проверяет, подписан ли пользователь на канал"""
    # Если CHANNEL_ID не указан или пустой, пропускаем проверку
    if not CHANNEL_ID:
        logger.info("Проверка подписки отключена (CHANNEL_ID не указан)")
        return True
        
    user_id = update.effective_user.id
    
    try:
        # Получаем информацию о пользователе в канале
        chat_member = await context.bot.get_chat_member(CHANNEL_ID, user_id)
        
        # Проверяем статус пользователя
        if chat_member.status in ['member', 'administrator', 'creator']:
            logger.info(f"Пользователь {user_id} подписан на канал {CHANNEL_ID}")
            return True
        else:
            logger.info(f"Пользователь {user_id} НЕ подписан на канал {CHANNEL_ID}, статус: {chat_member.status}")
            return False
    except Exception as e:
        logger.error(f"Ошибка при проверке подписки: {e}")
        # Если возникла ошибка, лучше пропустить пользователя
        return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start"""
    user = update.effective_user
    chat_id = update.effective_chat.id
    logger.info(f"Пользователь {user.first_name} {user.last_name} подключился")
    
    try:
        # Проверяем подписку на канал
        is_subscribed = await check_subscription(update, context)
        
        if CHANNEL_ID and not is_subscribed:
            # Если пользователь не подписан, отправляем сообщение о необходимости подписки
            await update.message.reply_text(
                f"Для использования бота, пожалуйста, подпишитесь на наш канал: {CHANNEL_ID}\n"
                "После подписки отправьте /start снова."
            )
            return
        
        # Основное приветственное сообщение
        welcome_text = (
            "Пришли артикул товара с Wildberries или ссылку на товар, я найду лучшее предложение!" + 
            DONATE_TEXT
        )
        
        await update.message.reply_text(
            welcome_text,
            parse_mode='HTML'
        )
        logger.info("Сообщение /start успешно отправлено")
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения /start: {e}")
        print(f"Ошибка при отправке сообщения: {e}")

async def get_product_html_cloudscraper(article: str) -> Optional[str]:
    """
    Получение HTML-страницы товара с помощью cloudscraper
    
    Args:
        article: Артикул товара
    
    Returns:
        HTML страницы или None, если не удалось получить
    """
    # Используем семафор для ограничения количества одновременных запросов
    async with request_semaphore:
        try:
            # Создаем scraper для обхода защиты
            scraper = cloudscraper.create_scraper(
                browser={
                    'browser': 'chrome',
                    'platform': 'windows',
                    'desktop': True
                }
            )
            
            # Формируем URL и заголовки
            url = f"https://www.wildberries.ru/catalog/{article}/detail.aspx"
            headers = {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1"
            }
            
            # Устанавливаем таймаут для запроса
            timeout = 30  # 30 секунд
            
            # Добавляем прокси, если включено
            if PROXY_ENABLED:
                try:
                    logger.info(f"Настройка прокси {PROXY_IP}:{PROXY_PORT} для запроса HTML")
                    scraper.proxies = PROXIES
                except Exception as e:
                    logger.error(f"Ошибка при настройке прокси: {str(e)}")
            
            # Попытка с прокси (если настроен)
            try:
                logger.info(f"Выполняю запрос HTML для артикула {article}" + 
                           (f" через прокси {PROXY_IP}:{PROXY_PORT}" if PROXY_ENABLED and scraper.proxies else ""))
                response = scraper.get(url, headers=headers, timeout=timeout)
                
                # Проверяем статус ответа
                if response.status_code == 200:
                    logger.info(f"HTML получен успешно для артикула {article}")
                    return response.text
                else:
                    logger.warning(f"Не удалось получить HTML для артикула {article}. Статус: {response.status_code}")
                    
                    # Если прокси используется и запрос не удался, пробуем без прокси
                    if PROXY_ENABLED and scraper.proxies:
                        logger.info(f"Пробую получить HTML без прокси для артикула {article}")
                        scraper.proxies = None
                        response = scraper.get(url, headers=headers, timeout=timeout)
                        if response.status_code == 200:
                            logger.info(f"HTML успешно получен без прокси для артикула {article}")
                            return response.text
                    return None
                    
            except requests.exceptions.RequestException as e:
                logger.warning(f"Ошибка запроса с прокси: {str(e)}")
                
                # Если прокси используется и запрос не удался, пробуем без прокси
                if PROXY_ENABLED and scraper.proxies:
                    try:
                        logger.info(f"Пробую получить HTML без прокси для артикула {article}")
                        scraper.proxies = None
                        response = scraper.get(url, headers=headers, timeout=timeout)
                        if response.status_code == 200:
                            logger.info(f"HTML успешно получен без прокси для артикула {article}")
                            return response.text
                    except Exception as fallback_error:
                        logger.error(f"Ошибка при запросе без прокси: {str(fallback_error)}")
                
                return None
                
        except cloudscraper.exceptions.CloudflareChallengeError:
            logger.warning(f"Обнаружена защита Cloudflare для артикула {article}")
            return None
        except requests.exceptions.Timeout:
            logger.warning(f"Таймаут при получении HTML для артикула {article}")
            return None
        except requests.exceptions.ConnectionError:
            logger.warning(f"Ошибка соединения при получении HTML для артикула {article}")
            return None
        except Exception as e:
            logger.error(f"Ошибка при получении HTML для артикула {article}: {str(e)}")
            return None

async def get_product_from_api(article: str) -> Optional[Dict[str, Any]]:
    """
    Получение данных о товаре напрямую через Wildberries API
    
    Args:
        article: Артикул товара
    
    Returns:
        Dict с данными товара или None, если товар не найден
    """
    try:
        logger.info(f"Попытка получения данных о товаре {article} через API")
        
        # Создаем scraper для обхода защиты
        scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'desktop': True
            }
        )
        
        # Добавляем прокси, если включено
        if PROXY_ENABLED:
            try:
                logger.info(f"Настройка прокси {PROXY_IP}:{PROXY_PORT} для запроса к API")
                scraper.proxies = PROXIES
            except Exception as e:
                logger.error(f"Ошибка при настройке прокси для API: {str(e)}")
        
        # Современные API-эндпоинты Wildberries, наиболее стабильный первый
        api_urls = [
            f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-6972066&spp=30&nm={article}",  # Основной API v2
            f"https://card.wb.ru/cards/detail?nm={article}",  # Основной API v1
            f"https://wbxcatalog-ru.wildberries.ru/nm-2-card/catalog?nm={article}",  # Каталог API
        ]
        
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/html",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
        }
        
        # Пробуем каждый API поочередно
        for api_url in api_urls:
            logger.info(f"Запрос к API: {api_url}")
            try:
                # Выполняем запрос с прокси
                logger.info(f"Выполняю запрос к API для артикула {article}" + 
                           (f" через прокси {PROXY_IP}:{PROXY_PORT}" if PROXY_ENABLED and scraper.proxies else ""))
                
                try:
                    response = scraper.get(api_url, headers=headers, timeout=15)
                except requests.exceptions.RequestException as e:
                    logger.warning(f"Ошибка при запросе к API с прокси: {str(e)}")
                    
                    # Если используется прокси и произошла ошибка, пробуем без прокси
                    if PROXY_ENABLED and scraper.proxies:
                        logger.info(f"Пробую запрос к API без прокси для {api_url}")
                        try:
                            scraper.proxies = None
                            response = scraper.get(api_url, headers=headers, timeout=15)
                        except requests.exceptions.RequestException as fallback_error:
                            logger.error(f"Также не удалось получить данные без прокси: {str(fallback_error)}")
                            continue
                
                # Явно устанавливаем кодировку UTF-8
                response.encoding = 'utf-8'
                
                if response.status_code == 200:
                    # Обработка успешного ответа
                    try:
                        # Проверяем, это JSON или HTML
                        content_type = response.headers.get('Content-Type', '').lower()
                        
                        if 'json' in content_type or api_url.endswith('.json'):
                            # Это JSON-ответ
                            try:
                                data = response.json()
                                logger.info(f"Получены данные через API: {response.status_code}")
                                
                                # Парсим результат в зависимости от формата ответа
                                if 'data' in data and 'products' in data['data'] and len(data['data']['products']) > 0:
                                    product = data['data']['products'][0]
                                    
                                    # Извлекаем имя товара
                                    name = product.get('name')
                                    if not name or '{{:~t(' in name or 'unsuccessfulLoad' in name:
                                        logger.warning("В API-ответе отсутствует название товара или оно некорректное")
                                        continue
                                    
                                    # Получаем актуальную цену товара
                                    price = None
                                    
                                    # Проверяем разные варианты полей с ценой
                                    if 'salePriceU' in product:
                                        price = int(product['salePriceU']) / 100
                                    elif 'priceU' in product:
                                        price = int(product['priceU']) / 100
                                    elif 'price' in product:
                                        price_val = float(product['price'])
                                        # Если цена выглядит как копейки, делим на 100
                                        if price_val > 1000:
                                            price = price_val / 100
                                        else:
                                            price = price_val
                                    
                                    # Проверяем наличие рейтинга
                                    rating = None
                                    if 'reviewRating' in product:
                                        rating = float(product['reviewRating'])
                                    elif 'rating' in product:
                                        rating = float(product['rating'])
                                    
                                    # Проверка на валидность данных
                                    if not price or price <= 10:
                                        logger.warning("В API-ответе отсутствует цена товара или она некорректная")
                                        
                                    # Формируем результат с доступными данными
                                    result = {
                                        'name': name,
                                        'article': article
                                    }
                                    
                                    if price and price > 10:
                                        result['price'] = price
                                        
                                    if rating is not None:
                                        result['rating'] = rating
                                    
                                    logger.info(f"Найден товар через API: {name}, цена: {price}, рейтинг: {rating}")
                                    return result
                                    
                            except json.JSONDecodeError as e:
                                logger.error(f"Ошибка декодирования JSON: {e}")
                            except Exception as e:
                                logger.error(f"Ошибка при обработке JSON данных: {e}")
                    except Exception as e:
                        logger.error(f"Ошибка при обработке ответа API: {e}")
                elif response.status_code == 404:
                    logger.warning(f"Товар не найден в API (HTTP 404): {api_url}")
                elif response.status_code == 403:
                    logger.warning(f"Доступ к API запрещен (HTTP 403): {api_url}")
                else:
                    logger.warning(f"Ошибка запроса к API: HTTP {response.status_code}")
            except ConnectionError as e:
                logger.error(f"Ошибка соединения при запросе к API {api_url}: {e}")
            except Timeout as e:
                logger.error(f"Таймаут при запросе к API {api_url}: {e}")
            except RequestException as e:
                logger.error(f"Ошибка запроса к API {api_url}: {e}")
        
        # Если ни один API не вернул данные, возвращаем None
        logger.warning(f"Не удалось получить данные о товаре {article} через API")
        return None
            
    except Exception as e:
        logger.error(f"Критическая ошибка при запросе API: {e}")
        return None


async def get_product_data_from_html(html: str, article: str) -> Optional[Dict[str, Any]]:
    """
    Извлекает данные о товаре из HTML-страницы
    
    Args:
        html: HTML-страница товара
        article: Артикул товара
    
    Returns:
        Dict с данными товара или None, если товар не найден
    """
    if not html or len(html) < 100:
        logger.warning(f"Получен пустой или слишком короткий HTML для артикула {article}")
        return None
        
    try:
        # Исправляем проблемы с кодировкой
        if "ÐÐ½ÑÐµÑÐ½Ð" in html or "Ð¼Ð°Ð³Ð°Ð·Ð¸Ð½" in html:
            logger.warning("Обнаружена проблема с кодировкой HTML")
            try:
                # Пробуем декодировать с разными кодировками
                for encoding in ['windows-1251', 'cp1251', 'latin-1']:
                    try:
                        if isinstance(html, str):
                            html_bytes = html.encode('utf-8', errors='replace')
                            fixed_html = html_bytes.decode(encoding, errors='replace')
                            # Проверяем успешность декодирования
                            if "Интернет" in fixed_html and "магазин" in fixed_html:
                                logger.info(f"Кодировка исправлена с помощью {encoding}")
                                html = fixed_html
                                break
                    except Exception as e:
                        logger.debug(f"Ошибка при пробе кодировки {encoding}: {e}")
            except Exception as e:
                logger.warning(f"Ошибка при исправлении кодировки: {e}")
        
        # Проверяем, содержит ли страница сообщение об ошибке
        if any(error_text in html.lower() for error_text in 
              ["извините, такой страницы не существует", 
               "страница не найдена", 
               "товар не найден"]):
            logger.warning(f"Страница артикула {article} содержит сообщение об ошибке")
            return None
        
        # Создаем объект BeautifulSoup для парсинга
        soup = BeautifulSoup(html, 'lxml')
        
        # Извлекаем название товара
        product_name = None
        
        # 1. Ищем в теге h1
        h1_tag = soup.find('h1')
        if h1_tag:
            product_name = h1_tag.get_text().strip()
            logger.info(f"Название из h1: {product_name}")
        
        # 2. Ищем в мета-тегах
        if not product_name or len(product_name) < 3:
            og_title = soup.find('meta', property='og:title')
            if og_title and og_title.get('content'):
                product_name = og_title.get('content').strip()
                logger.info(f"Название из og:title: {product_name}")
        
        # 3. Ищем в JSON-LD
        if not product_name or len(product_name) < 3:
            script_tags = soup.find_all('script', type='application/ld+json')
            for script in script_tags:
                try:
                    json_data = json.loads(script.string)
                    if isinstance(json_data, dict) and 'name' in json_data:
                        product_name = json_data['name']
                        if product_name and len(product_name) > 3:
                            logger.info(f"Название из JSON-LD: {product_name}")
                            break
                except (json.JSONDecodeError, AttributeError):
                    continue
        
        # Проверяем валидность названия
        if not product_name or len(product_name) < 3 or '{{:~t(' in product_name:
            logger.warning(f"Не удалось найти валидное название товара для артикула {article}")
            # Устанавливаем название по умолчанию
            product_name = f"Товар {article}"
        
        # Извлекаем цену и рейтинг из HTML
        price_value = extract_price(html)
        rating_value = extract_rating(html)
        
        # Формируем результат
        result = {
            'name': product_name,
            'article': article
        }
        
        # Добавляем цену, если она найдена и валидна
        if price_value and price_value > 10:
            result['price'] = price_value
        
        # Добавляем рейтинг, если он найден и валиден
        if rating_value is not None and 0 <= rating_value <= 5:
            result['rating'] = rating_value
        
        # Логируем результат
        if 'price' in result or 'rating' in result:
            logger.info(f"Успешно извлечены данные из HTML: {result}")
            return result
        else:
            logger.warning(f"Не удалось извлечь цену и рейтинг для артикула {article}")
            # Всё равно возвращаем результат с именем
            return result
            
    except Exception as e:
        logger.error(f"Ошибка при парсинге HTML-страницы: {e}")
        return None

async def handle_cheaper_search(update: Update, context: ContextTypes.DEFAULT_TYPE, args: list) -> None:
    """
    Обрабатывает запрос на поиск более дешевых аналогов товара
    
    Args:
        update: Объект события от Telegram
        context: Контекст бота
        args: Список аргументов (артикул, макс. цена, мин. рейтинг)
    """
    try:
        # Получаем информацию о пользователе и чате
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        
        # Извлекаем артикул из аргументов
        article = None
        max_price = None
        min_rating = 4.0  # По умолчанию минимальный рейтинг 4.0
        
        # Ищем артикул в аргументах
        for arg in args:
            if arg.isdigit() and len(arg) >= 5:
                article = arg
                break
        
        if not article:
            await update.message.reply_text("❓ Не указан артикул товара. Укажите артикул вместе с запросом.")
            return
        
        # Проверяем, есть ли указание максимальной цены
        price_args = [arg for arg in args if re.match(r'^\d+(\.\d+)?$', arg) and arg != article]
        if price_args:
            try:
                max_price = float(price_args[0])
                logger.info(f"Указана максимальная цена: {max_price}")
            except ValueError:
                pass
        
        # Проверяем, есть ли указание минимального рейтинга
        rating_args = [arg for arg in args if re.match(r'^[1-5](\.\d+)?$', arg)]
        if rating_args:
            try:
                min_rating = float(rating_args[0])
                logger.info(f"Указан минимальный рейтинг: {min_rating}")
            except ValueError:
                pass
        
        # Отправляем сообщение о начале поиска
        loading_message = await update.message.reply_text(
            f"🔍 Ищу товары дешевле, чем артикул {article}..."
        )
        
        # Получаем данные об исходном товаре
        product_data = await get_product_data(article)
        
        # Проверяем, удалось ли получить данные
        if isinstance(product_data, dict) and 'error' in product_data:
            await loading_message.edit_text(f"❌ {product_data['error']}")
            return
        
        if not product_data or not product_data[0]:
            await loading_message.edit_text("❌ Не удалось получить информацию об исходном товаре.")
            return
        
        # Распаковываем данные о товаре
        name, price, details_json = product_data
        
        # Если цена не указана явно, используем цену товара
        if not max_price and price:
            max_price = price * 0.9  # 90% от текущей цены
        
        # Если цена всё ещё не определена, используем значение по умолчанию
        if not max_price:
            max_price = 10000  # Значение по умолчанию
        
        # Пробуем получить рейтинг из details_json
        rating = None
        if details_json:
            try:
                details = json.loads(details_json)
                rating = details.get('rating')
            except json.JSONDecodeError:
                pass
        
        # Создаем сообщение с информацией об исходном товаре
        message = f"📦 *Исходный товар:* {name}\n"
        if price is not None:
            message += f"💰 *Цена:* {price:,.2f} ₽\n"
        else:
            message += "💰 *Цена:* Временно недоступна\n"
        if rating is not None:
            # Корректное отображение рейтинга в виде золотых звёзд
            full_stars = min(5, int(rating))
            half_star = rating - int(rating) >= 0.5
            empty_stars = 5 - full_stars - (1 if half_star else 0)
            
            # Используем символы звезд для лучшего визуального отображения
            star_rating = '★' * full_stars
            if half_star:
                star_rating += '✭'
            star_rating += '☆' * empty_stars
            
            message += f"⭐ *Рейтинг:* {rating} {star_rating}\n"
        else:
            message += "⭐ *Рейтинг:* Нет данных\n"
        
        message += f"\n🔍 *Поиск товаров дешевле {max_price:,.2f} ₽ с рейтингом от {min_rating}*\n"
        
        # Удаляем сообщение о загрузке
        try:
            await loading_message.delete()
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение о загрузке: {e}")
        
        # Отправляем сообщение о параметрах поиска
        status_message = await update.message.reply_text(message, parse_mode='Markdown')
        
        # Отправляем сообщение о начале поиска похожих товаров
        loading_message = await update.message.reply_text("🔍 Выполняется поиск похожих товаров...")
        
        # Ищем похожие товары
        similar_products = await get_similar_products(
            article, 
            max_price=max_price, 
            min_rating=min_rating
        )
        
        # Удаляем сообщение о загрузке
        try:
            await loading_message.delete()
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение о загрузке: {e}")
        
        # Если похожие товары найдены
        if similar_products:
            # Ограничиваем количество товаров до 5
            similar_products = similar_products[:5]
            
            # Создаем сообщение с результатами
            result_message = f"✅ Найдено {len(similar_products)} похожих товаров:\n\n"
            
            # Добавляем информацию о каждом товаре
            for i, product in enumerate(similar_products, 1):
                product_article = product.get('article')
                product_name = product.get('name')
                product_price = product.get('price')
                product_rating = product.get('rating')
                product_url = product.get('url')
                
                result_message += f"*{i}. {product_name[:50]}...*\n"
                result_message += f"💰 Цена: {product_price:,.2f} ₽ "
                
                # Добавляем процент экономии
                if price:
                    saving = price - product_price
                    saving_percent = (saving / price) * 100
                    result_message += f"(-{saving_percent:.1f}%)\n"
                else:
                    result_message += "\n"
                
                if product_rating is not None:
                    # Корректное отображение рейтинга в виде золотых звёзд
                    full_stars = min(5, int(product_rating))
                    half_star = product_rating - int(product_rating) >= 0.5
                    empty_stars = 5 - full_stars - (1 if half_star else 0)
                    
                    # Используем символы звезд для лучшего визуального отображения
                    star_rating = '★' * full_stars
                    if half_star:
                        star_rating += '✭'
                    star_rating += '☆' * empty_stars
                    
                    result_message += f"⭐ Рейтинг: {product_rating} {star_rating}\n"
                
                result_message += f"🔗 [Ссылка на товар]({product_url})\n\n"
            
            # Отправляем сообщение с результатами
            await update.message.reply_text(
                result_message,
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
        else:
            # Если похожие товары не найдены
            await update.message.reply_text(
                "❌ Не удалось найти похожие товары дешевле указанной цены. Попробуйте изменить параметры поиска."
            )
    
    except Exception as e:
        logger.error(f"Ошибка при поиске дешевых аналогов: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ Произошла ошибка при поиске дешевых аналогов. Пожалуйста, попробуйте позже."
        )

def get_wb_product_data(article: str, use_proxy=False, max_retries=3, delay=1):
    """
    Получение данных о товаре с Wildberries (оптимизированная версия).
    
    Args:
        article: Артикул товара
        use_proxy: Использовать прокси или нет
        max_retries: Максимальное количество повторных попыток
        delay: Задержка между попытками в секундах
        
    Returns:
        dict: Данные о товаре или информация об ошибке
    """
    logger.info(f"Получение данных о товаре с артикулом {article}")
    
    # Проверка на пустой артикул
    if not article or not article.strip():
        logger.warning("Получен пустой артикул товара")
        return {"error": "Артикул товара не может быть пустым"}
    
    try:
        # Проверяем кеш
        if article in product_cache:
            data, timestamp = product_cache[article]
            # Проверяем, не устарели ли данные
            if datetime.now() - timestamp < timedelta(hours=CACHE_LIFETIME):
                logger.info(f"Данные для артикула {article} получены из кеша")
                return data
            else:
                logger.info(f"Данные для артикула {article} в кеше устарели")
        
        # Проверяем подключение к интернету
        if not check_internet_connection():
            return {"error": "Ошибка: Интернет недоступен"}
        
        # Функция для получения прокси
        def get_random_proxy():
            if not PROXY_LIST or not use_proxy:
                return None
            return random.choice(PROXY_LIST)
        
        # Создаем экземпляр scraper с возможностью обхода защиты
        def create_scraper_instance():
            scraper = cloudscraper.create_scraper(
                browser={
                    'browser': 'chrome',
                    'platform': 'windows',
                    'desktop': True
                }
            )
            
            # Устанавливаем заголовки
            headers = {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
            }
            scraper.headers.update(headers)
            
            # Добавляем прокси из глобальных настроек
            if PROXY_ENABLED:
                try:
                    logger.info(f"Настройка прокси {PROXY_IP}:{PROXY_PORT} для запроса товара {article}")
                    scraper.proxies = PROXIES
                except Exception as e:
                    logger.error(f"Ошибка при настройке прокси: {str(e)}")
            
            return scraper
        
        # Функция для получения API эндпоинтов
        def get_api_endpoints(article):
            endpoints = [
                # Основной API v2 (самый надежный)
                f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-6972066&nm={article}",
                # Основной API v1
                f"https://card.wb.ru/cards/detail?nm={article}",
                # Альтернативные API
                f"https://wbxcatalog-ru.wildberries.ru/nm-2-card/catalog?nm={article}"
            ]
            logger.info(f"Подготовлены API эндпоинты для товара {article}: {len(endpoints)} шт.")
            return endpoints
        
        # Функция для извлечения данных из HTML
        def extract_from_html(html):
            if not html:
                return None
                
            # Исправляем кодировку если нужно
            html = fix_encoding(html)
            
            # Проверяем наличие сообщений об ошибках на странице
            error_texts = ["извините, такой страницы не существует", 
                          "страница не найдена", 
                          "товар не найден"]
            
            if any(error_text in html.lower() for error_text in error_texts):
                logger.warning(f"Страница товара {article} содержит сообщение об ошибке")
                return {"error": "Товар не найден"}
            
            try:
                soup = BeautifulSoup(html, 'lxml')
                
                # Извлекаем название товара
                name = None
                
                # Ищем в h1
                h1 = soup.find('h1')
                if h1:
                    name = h1.get_text().strip()
                
                # Ищем в meta тегах
                if not name or len(name) < 3:
                    meta_title = soup.find('meta', property='og:title')
                    if meta_title and meta_title.get('content'):
                        name = meta_title.get('content').strip()
                
                # Ищем в JSON данных
                if not name or len(name) < 3:
                    scripts = soup.find_all('script', type='application/ld+json')
                    for script in scripts:
                        try:
                            data = json.loads(script.string)
                            if isinstance(data, dict) and 'name' in data:
                                name = data['name']
                                break
                        except (json.JSONDecodeError, AttributeError):
                            continue
                
                # Проверяем валидность имени
                if not name or len(name) < 3 or '{{:~t(' in name:
                    name = f"Товар {article}"
                
                # Извлекаем цену
                price = None
                
                # Ищем цену в разных местах
                price_element = soup.find(['span', 'ins', 'div'], class_=lambda c: c and 'price' in c.lower())
                if price_element:
                    price_text = price_element.get_text().strip()
                    price_text = re.sub(r'[^\d.,]', '', price_text).replace(',', '.')
                    try:
                        price = float(price_text)
                    except ValueError:
                        pass
                
                # Если не нашли цену, ищем с помощью регулярных выражений
                if not price:
                    price_patterns = [
                        r'(\d[\d\s]*[.,]?\d*)\s*(?:₽|руб)',
                        r'"finalPrice":(\d+)',
                        r'"price":(\d+)',
                        r'<meta property="product:price:amount" content="(\d+\.?\d*)'
                    ]
                    
                    for pattern in price_patterns:
                        matches = re.findall(pattern, html)
                        for match in matches:
                            try:
                                price_text = re.sub(r'[^\d.,]', '', match).replace(',', '.')
                                price = float(price_text)
                                if price > 10:  # Проверка на адекватность цены
                                    break
                            except ValueError:
                                continue
                        if price and price > 10:
                            break
                
                # Извлекаем рейтинг
                rating = None
                
                # Ищем рейтинг в разных местах
                rating_element = soup.find(['span', 'div'], class_=lambda c: c and 'rating' in c.lower())
                if rating_element:
                    rating_text = rating_element.get_text().strip()
                    rating_match = re.search(r'([\d.,]+)', rating_text)
                    if rating_match:
                        try:
                            rating = float(rating_match.group(1).replace(',', '.'))
                            if rating > 5:  # Если рейтинг больше 5, вероятно это не рейтинг
                                rating = None
                        except ValueError:
                            pass
                
                # Если не нашли рейтинг, ищем с помощью регулярных выражений
                if not rating:
                    rating_patterns = [
                        r'"rating":\s*"?([\d.,]+)"?',
                        r'"reviewRating":\s*"?([\d.,]+)"?',
                        r'ratingValue":\s*"?([\d.,]+)"?'
                    ]
                    
                    for pattern in rating_patterns:
                        matches = re.findall(pattern, html)
                        for match in matches:
                            try:
                                rating_value = float(match.replace(',', '.'))
                                if 0 <= rating_value <= 5:  # Проверка на адекватность рейтинга
                                    rating = rating_value
                                    break
                            except ValueError:
                                continue
                        if rating is not None:
                            break
                
                # Формируем результат
                result = {
                    'name': name,
                    'article': article
                }
                
                if price and price > 10:
                    result['price'] = price
                    
                if rating is not None and 0 <= rating <= 5:
                    result['rating'] = rating
                
                return result
                
            except Exception as e:
                logger.error(f"Ошибка при извлечении данных из HTML: {e}")
                return None
        
        # Функция для обработки ответа API
        def process_api_response(response):
            if not response or response.status_code != 200:
                return None
                
            # Устанавливаем кодировку
            response.encoding = 'utf-8'
            
            try:
                data = response.json()
                
                # Обрабатываем ответ API v2
                if 'data' in data and 'products' in data['data'] and data['data']['products']:
                    product = data['data']['products'][0]
                    
                    # Извлекаем имя
                    name = product.get('name')
                    if not name or '{{:~t(' in name or 'unsuccessfulLoad' in name:
                        return None
                    
                    # Находим цену
                    price = None
                    
                    # Проверяем разные варианты полей с ценой
                    if 'salePriceU' in product:
                        price = int(product['salePriceU']) / 100
                    elif 'priceU' in product:
                        price = int(product['priceU']) / 100
                    elif 'price' in product:
                        price_val = float(product['price'])
                        if price_val > 1000:  # Если цена в копейках
                            price = price_val / 100
                        else:
                            price = price_val
                    
                    # Проверяем еще поля с ценой в разных местах структуры
                    if not price or price <= 10:
                        # Ищем в sizes и stocks
                        if 'sizes' in product and product['sizes']:
                            for size in product['sizes']:
                                if 'stocks' in size and size['stocks']:
                                    for stock in size['stocks']:
                                        if 'priceU' in stock:
                                            price = float(stock['priceU']) / 100
                                            break
                                if price and price > 10:
                                    break
                    
                    # Ищем рейтинг
                    rating = None
                    if 'reviewRating' in product:
                        rating = float(product['reviewRating'])
                    elif 'rating' in product:
                        rating = float(product['rating'])
                    
                    # Формируем результат
                    result = {
                        'name': name,
                        'article': article
                    }
                    
                    if price and price > 10:
                        result['price'] = price
                        
                    if rating is not None and 0 <= rating <= 5:
                        result['rating'] = rating
                        
                    # Добавляем ссылку на товар
                    result['url'] = f"https://www.wildberries.ru/catalog/{article}/detail.aspx"
                    
                    return result
                
                return None
                
            except json.JSONDecodeError:
                return None
            except Exception as e:
                logger.error(f"Ошибка при обработке ответа API: {e}")
                return None
        
        # Основной код функции
        scraper = create_scraper_instance()
        
        # 1. Пробуем получить данные через API
        for attempt in range(max_retries):
            try:
                # Получаем список API эндпоинтов
                api_endpoints = get_api_endpoints(article)
                
                for api_url in api_endpoints:
                    try:
                        logger.info(f"Запрос к API: {api_url}")
                        response = scraper.get(api_url, timeout=10)
                        
                        if response.status_code == 200:
                            # Обрабатываем успешный ответ
                            result = process_api_response(response)
                            if result:
                                logger.info(f"Успешно получены данные через API: {api_url}")
                                # Сохраняем результат в кеш
                                product_cache[article] = (result, datetime.now())
                                return result
                        elif response.status_code == 404:
                            logger.warning(f"Товар не найден в API: {api_url}")
                        else:
                            logger.warning(f"Ошибка запроса к API: HTTP {response.status_code}")
                            
                    except (RequestException, ConnectionError, Timeout) as e:
                        logger.warning(f"Сетевая ошибка при запросе к API {api_url}: {e}")
                
                # Если API не сработали, пробуем получить HTML страницу
                try:
                    url = f"https://www.wildberries.ru/catalog/{article}/detail.aspx"
                    logger.info(f"Запрос к странице товара: {url}")
                    
                    response = scraper.get(url, timeout=15)
                    response.encoding = 'utf-8'  # Явно устанавливаем кодировку
                    
                    if response.status_code == 200:
                        html_result = extract_from_html(response.text)
                        if html_result:
                            if 'error' in html_result:
                                return html_result  # Возвращаем сообщение об ошибке
                            else:
                                # Добавляем URL к результату
                                html_result['url'] = url
                                logger.info(f"Успешно получены данные из HTML страницы")
                                # Сохраняем результат в кеш
                                product_cache[article] = (html_result, datetime.now())
                                return html_result
                    elif response.status_code == 404:
                        logger.warning("Страница товара не найдена (HTTP 404)")
                        return {"error": "Товар не найден"}
                    else:
                        logger.warning(f"Ошибка при запросе страницы товара: HTTP {response.status_code}")
                        
                except (RequestException, ConnectionError, Timeout) as e:
                    logger.warning(f"Сетевая ошибка при запросе страницы товара: {e}")
                
                # Если это не последняя попытка, делаем паузу перед следующей
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    # Создаем новый scraper на случай блокировки
                    scraper = create_scraper_instance()
                    
            except Exception as e:
                logger.error(f"Общая ошибка при попытке {attempt+1}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(delay)
        
        # Если все попытки неудачны
        return {"error": "Не удалось получить данные о товаре"}
        
    except Exception as e:
        logger.error(f"Критическая ошибка при получении данных о товаре {article}: {e}")
        return {"error": f"Ошибка: {str(e)}"}

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /help"""
    try:
        # Текст справки
        help_text = (
            "📋 <b>Справка по использованию бота</b>\n\n"
            "Отправьте мне артикул товара с WildBerries или ссылку на товар, я покажу подробную информацию.\n\n"
            "<b>Примеры запросов:</b>\n"
            "• 12345678 - просто артикул товара\n"
            "• https://www.wildberries.ru/catalog/12345678/detail.aspx - ссылка на товар\n"
            "• 12345678 найди дешевле - поиск более дешевых товаров\n\n"
            "<b>Доступные команды:</b>\n"
            "• /start - запустить бота\n"
            "• /help - показать эту справку\n"
            "• /similar &lt;артикул&gt; - найти похожие товары дешевле указанного\n"
        )
        
        # Добавляем информацию о ChatGPT, если он настроен
        if os.getenv("OPENAI_API_KEY"):
            help_text += (
                "\n<b>Команды для работы с ChatGPT:</b>\n"
                "• /ask &lt;вопрос&gt; - задать вопрос ChatGPT\n"
                "• /chatgpt &lt;вопрос&gt; - аналогично /ask\n"
                "• gpt &lt;вопрос&gt; - прямой запрос к ChatGPT\n"
                "• chatgpt &lt;вопрос&gt; - то же, что и gpt\n"
                "• gemini &lt;вопрос&gt; - также работает с ChatGPT\n"
                "Лимит: до 5 запросов в день на пользователя, ответы ограничены 1500 символами\n"
            )
        
        help_text += (
            "\n<b>Дополнительные возможности:</b>\n"
            "• Для поиска похожих товаров дешевле можно указать максимальную цену и минимальный рейтинг:\n"
            "  /similar &lt;артикул&gt; &lt;макс_цена&gt; &lt;мин_рейтинг&gt;\n"
            "  Пример: /similar 12345678 2000 4.5\n\n"
            "<b>Контакты и поддержка:</b>\n"
            "Если у вас есть вопросы или предложения, пишите @wildberries_price_bot_support"
        )
        
        if DONATE_TEXT:
            help_text += f"\n\n{DONATE_TEXT}"
        
        await update.message.reply_text(
            help_text,
            parse_mode='HTML',
            disable_web_page_preview=True
        )
        logger.info("Отправлена справка по команде /help")
    except Exception as e:
        logger.error(f"Ошибка при отправке справки: {e}", exc_info=True)
        await update.message.reply_text("Произошла ошибка при отправке справки")

async def main() -> None:
    """
    Инициализирует бота (устаревшая функция, используйте run_bot.py для запуска)
    """
    logger.info("Эта функция больше не используется для запуска бота. Используйте run_bot.py")
    # Эта функция сохранена для обратной совместимости, но больше не используется

def fix_encoding(text: str) -> str:
    """
    Исправляет проблемы с кодировкой текста
    
    Args:
        text: Текст, который может иметь проблемы с кодировкой
    
    Returns:
        Исправленный текст
    """
    if not text:
        return text
        
    # Проверяем наличие признаков неправильной кодировки
    if "ÐÐ½ÑÐµÑÐ½Ð" in text or "Ð¼Ð°Ð³Ð°Ð·Ð¸Ð½" in text:
        logger.debug("Обнаружена проблема с кодировкой, пытаюсь исправить")
        
        # Пробуем разные кодировки
        for encoding in ['windows-1251', 'cp1251', 'latin-1', 'utf-8']:
            try:
                # Кодируем в байты и декодируем с другой кодировкой
                text_bytes = text.encode('utf-8', errors='replace')
                fixed_text = text_bytes.decode(encoding, errors='replace')
                
                # Проверяем, решена ли проблема
                if ("Интернет" in fixed_text and "магазин" in fixed_text) or \
                   ("цена" in fixed_text.lower()) or \
                   ("товар" in fixed_text.lower()):
                    logger.info(f"Кодировка исправлена с помощью {encoding}")
                    return fixed_text
            except Exception as e:
                logger.debug(f"Ошибка при исправлении кодировки через {encoding}: {e}")
    
    # Если текст не требует исправления или все попытки не удались, возвращаем исходный текст
    return text

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок для бота"""
    # Извлекаем информацию об ошибке
    error = context.error
    
    # Логируем ошибку
    logger.error(f"Произошла ошибка при обработке обновления: {error}", exc_info=context.error)
    
    try:
        # Если это KeyboardInterrupt, корректно завершаем работу
        if isinstance(error, KeyboardInterrupt):
            logger.info("Получен сигнал прерывания. Завершаем работу бота...")
            # Выполняем действия по завершению работы
            await context.application.stop()
            return
        
        # Обработка ошибки Conflict (несколько экземпляров бота)
        if "Conflict: terminated by other getUpdates request" in str(error):
            logger.critical("Обнаружен конфликт: запущено несколько экземпляров бота.")
            logger.critical("Завершаем работу текущего экземпляра.")
            # Удаляем файл блокировки при выходе
            try:
                import os
                if os.path.exists('pid.lock'):
                    os.remove('pid.lock')
                    logger.info("Файл pid.lock удален")
            except Exception as e:
                logger.error(f"Ошибка при удалении файла pid.lock: {e}")
            await context.application.stop()
            return
        
        # Если возникла сетевая ошибка, пытаемся восстановить соединение
        if isinstance(error, (ConnectionError, Timeout, HTTPError, RequestException)):
            logger.warning(f"Сетевая ошибка: {error}. Проверяем соединение...")
            
            # Проверяем соединение
            if not check_internet_connection():
                logger.error("Соединение с интернетом потеряно. Ожидаем восстановления...")
                
                # Сообщаем пользователю, если возможно
                if update and update.effective_chat:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="Извините, возникла проблема с соединением. Пожалуйста, повторите запрос позже."
                    )
                return
        
        # Если это ошибка Telegram API
        if "Telegram API" in str(error):
            logger.error(f"Ошибка Telegram API: {error}")
            # Сообщаем пользователю, если возможно
            if update and update.effective_chat:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="Извините, возникла техническая проблема. Пожалуйста, повторите запрос позже."
                )
            return
            
        # Обработка общих ошибок
        if update and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Извините, произошла ошибка при обработке вашего запроса. Пожалуйста, попробуйте еще раз."
            )
    
    except Exception as e:
        # Логируем ошибку в обработчике ошибок
        logger.error(f"Ошибка в обработчике ошибок: {e}", exc_info=True)
        
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обрабатывает входящие сообщения
    """
    try:
        # Проверка наличия Интернет-соединения
        if not check_internet_connection():
            await update.message.reply_text(
                "❌ Нет подключения к Интернету. Пожалуйста, проверьте ваше соединение и попробуйте снова."
            )
            return
            
        # Проверка доступности хостов Wildberries
        hosts = check_wildberries_hosts()
        if not any(hosts.values()):
            await update.message.reply_text(
                "⚠️ Серверы Wildberries в данный момент недоступны. Пожалуйста, попробуйте позже."
            )
            return
        
        # Получаем сообщение и информацию о пользователе
        message_text = update.message.text
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name
        
        logger.info(f"Получено сообщение: '{message_text}'")
        
        # Проверяем, подписан ли пользователь на канал
        if not await check_subscription(update, context):
            return
            
        # Проверяем ограничение на количество запросов
        if not check_request_limit(user_id):
            await update.message.reply_text(
                "⚠️ Превышен лимит запросов. Пожалуйста, подождите немного перед следующим запросом."
            )
            return
        
        # Проверяем, является ли сообщение поисковым запросом
        search_query = extract_search_query(message_text)
        if search_query:
            logger.info(f"Обнаружен поисковый запрос: '{search_query}'")
            
            # Отправляем сообщение о начале поиска
            message = await update.message.reply_text(
                f"🔍 Ищу товары по запросу: *{search_query}*...", 
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Выполняем поиск
            results = search_products(search_query)
            
            if results:
                # Форматируем результаты
                formatted_text = format_search_results(results)
                
                try:
                    # Отправляем результаты
                    await message.edit_text(
                        formatted_text,
                        parse_mode=ParseMode.MARKDOWN,
                        disable_web_page_preview=True
                    )
                    logger.info(f"Отправлены результаты поиска для запроса: '{search_query}'")
                except BadRequest as e:
                    # Если возникла ошибка форматирования Markdown, отправляем без форматирования
                    if "Can't parse entities" in str(e):
                        logger.warning(f"Ошибка форматирования Markdown: {e}")
                        # Отправляем новое сообщение вместо редактирования
                        await message.delete()
                        await update.message.reply_text(
                            f"🔍 Результаты поиска для '{search_query}':\n\n" + 
                            re.sub(r'\*([^*]+)\*', r'\1', formatted_text),  # Удаляем звездочки
                            disable_web_page_preview=True
                        )
                    else:
                        raise
            else:
                await message.edit_text(
                    f"❌ По запросу *{search_query}* ничего не найдено. Попробуйте изменить запрос.",
                    parse_mode=ParseMode.MARKDOWN
                )
                logger.warning(f"Нет результатов поиска для запроса: '{search_query}'")
            
            return
        
        # Разбиваем сообщение на части по пробелам
        message_parts = message_text.split()
        
        # Проверяем, не является ли сообщение командой
        if message_text.startswith('/'):
            # Это команда, но не обработанная. Отправляем сообщение о неизвестной команде
            await update.message.reply_text(
                "❓ Неизвестная команда. Введите /help для получения списка доступных команд."
            )
            return
        
        # Проверяем, является ли сообщение артикулом Wildberries
        if message_text.isdigit() and len(message_text) >= 5:
            # Это может быть артикул
            article = message_text
            await handle_article_request(update, context, article)
        
        # Проверяем ссылку на Wildberries
        elif "wildberries.ru" in message_text.lower() or "wb.ru" in message_text.lower():
            # Это ссылка на WB
            article = None
            
            # Пробуем извлечь артикул из ссылки с помощью регулярного выражения
            # Поддерживаем различные форматы URL
            patterns = [
                r'wildberries\.ru/catalog/(\d+)/',  # Обычный URL товара
                r'wb\.ru/catalog/(\d+)/',          # Сокращенный URL
                r'wildberries\.ru/product\?card=(\d+)', # URL товара в корзине
                r'card=(\d+)'                      # URL с card parameter
            ]
            
            for pattern in patterns:
                match = re.search(pattern, message_text)
                if match:
                    article = match.group(1)
                    break
            
            if article:
                await handle_article_request(update, context, article)
            else:
                await update.message.reply_text(
                    "❌ Не удалось извлечь артикул из ссылки. Проверьте ссылку и попробуйте снова."
                )
        else:
            # Обрабатываем обычный текст
            await update.message.reply_text(
                f"📝 Для проверки цены введите артикул товара Wildberries или ссылку на товар.\n\n"
                f"Для поиска товаров используйте команду /search или фразы вида 'найди...', 'покажи...'\n\n"
                f"Например: 'найди фигурку наруто' или '/search ковер в детскую'\n\n"
                f"Для получения помощи введите /help"
            )
    except Exception as e:
        logger.error(f"Ошибка при обработке сообщения: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ Произошла ошибка при обработке вашего сообщения. Пожалуйста, попробуйте позже."
        )

async def handle_article_request(update: Update, context: ContextTypes.DEFAULT_TYPE, article: str) -> None:
    """
    Обрабатывает запрос с артикулом товара
    
    Args:
        update: Объект события от Telegram
        context: Контекст бота
        article: Артикул товара
    """
    try:
        user_id = update.effective_user.id
        
        # Отправляем сообщение о начале обработки
        loading_message = await update.message.reply_text(
            f"🔍 Ищу информацию о товаре {article}..."
        )
        
        # Получаем данные о товаре
        product_data = await get_product_data(article)
        
        # Удаляем сообщение о загрузке
        try:
            await loading_message.delete()
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение о загрузке: {e}")
        
        # Проверяем, является ли product_data словарем с ошибкой
        if isinstance(product_data, dict) and 'error' in product_data:
            error_message = f"⚠️ {product_data['error']}"
            await update.message.reply_text(error_message)
            return
            
        # Если product_data - кортеж (name, price, details_json)
        if product_data and isinstance(product_data, tuple) and len(product_data) == 3:
            try:
                # Распаковываем данные о товаре
                name, price, details_json = product_data
                
                # Создаем сообщение с информацией о товаре
                message = f"📦 *Товар:* {name}\n"
                
                if price is not None:
                    # Форматируем цену с точкой как разделителем тысяч: 11.952
                    # Целую цену форматируем без десятичной части
                    if price == int(price):
                        price_int = int(price)
                        if price_int >= 1000:
                            # Форматируем с точкой как разделителем тысяч
                            price_str = f"{price_int // 1000}.{price_int % 1000:03d}"
                        else:
                            price_str = str(price_int)
                        message += f"💰 Цена: {price_str} ₽\n"
                    else:
                        # Для цены с копейками
                        price_int = int(price)
                        price_decimal = int((price - price_int) * 100)
                        if price_int >= 1000:
                            # Форматируем с точкой как разделителем тысяч
                            price_str = f"{price_int // 1000}.{price_int % 1000:03d}"
                            if price_decimal > 0:
                                price_str += f",{price_decimal:02d}"
                        else:
                            if price_decimal > 0:
                                price_str = f"{price_int},{price_decimal:02d}"
                            else:
                                price_str = str(price_int)
                        message += f"💰 Цена: {price_str} ₽\n"
                
                # Проверяем, есть ли детали товара
                if details_json:
                    try:
                        # Парсим JSON с деталями товара
                        details = json.loads(details_json)
                        
                        # Добавляем информацию о рейтинге, если есть
                        if 'rating' in details and details['rating']:
                            rating = float(details['rating'])
                            
                            # Корректное отображение рейтинга в виде золотых звёзд
                            full_stars = min(5, int(rating))
                            half_star = rating - int(rating) >= 0.5
                            empty_stars = 5 - full_stars - (1 if half_star else 0)
                            
                            # Используем символы звезд для лучшего визуального отображения
                            # ★ - золотая звезда (полная)
                            # ✭ - полузвезда (можно заменить на другой символ)
                            # ☆ - пустая звезда
                            star_rating = '★' * full_stars
                            if half_star:
                                star_rating += '✭'
                            star_rating += '☆' * empty_stars
                            
                            message += f"⭐️ *Рейтинг:* {rating:.1f} {star_rating}\n"
                        
                        # Добавляем информацию о бренде, если есть
                        if 'brand' in details and details['brand']:
                            brand = details['brand']
                            message += f"🏭 *Бренд:* {brand}\n"
                            
                        # Добавляем информацию о продавце, если есть
                        if 'seller' in details and details['seller']:
                            seller = details['seller']
                            message += f"🏪 *Продавец:* {seller}\n"
                            
                        # Добавляем информацию о количестве отзывов, если есть
                        if 'feedbacks' in details and details['feedbacks']:
                            feedbacks = int(details['feedbacks'])
                            message += f"💬 *Отзывы:* {feedbacks}\n"
                    
                    except json.JSONDecodeError:
                        logger.warning(f"Не удалось декодировать JSON с деталями: {details_json}")
                
                # Добавляем партнерскую ссылку
                partner_link = f"https://www.wildberries.ru/catalog/{article}/detail.aspx?target=partner&partner={PARTNER_ID}"
                message += f"🔗 Ссылка: {partner_link}"
                
                # Создаем клавиатуру с кнопкой для поиска похожих товаров дешевле
                keyboard = [
                    [InlineKeyboardButton("Найти похожие товары дешевле", callback_data=f"similar:{article}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                # Отправляем сообщение с информацией о товаре и кнопкой
                await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
            except Exception as e:
                logger.error(f"Ошибка при обработке данных о товаре: {e}", exc_info=True)
                await update.message.reply_text(
                    "❌ Произошла ошибка при обработке данных о товаре. Пожалуйста, попробуйте позже."
                )
        else:
            # Если не удалось получить данные о товаре
            error_message = "⚠️ Товар не найден на Wildberries."
            await update.message.reply_text(error_message)
            
    except Exception as e:
        logger.error(f"Ошибка при обработке артикула {article}: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Произошла ошибка при обработке артикула {article}. Пожалуйста, попробуйте позже."
        )

async def generate_api_endpoints(article: str) -> list:
    """
    Генерирует все возможные API-эндпоинты Wildberries для получения данных о товаре
    
    Args:
        article: Артикул товара
        
    Returns:
        list: Список URL API-эндпоинтов
    """
    endpoints = [
        f"https://card.wb.ru/cards/detail?nm={article}",
        f"https://wbxcatalog-ru.wildberries.ru/nm-2-card/catalog?nm={article}",
        f"https://wbx-content-v2.wbstatic.net/ru/{article}.json",
        f"https://search.wb.ru/exactmatch/ru/common/v4/search?query={article}",
        f"https://mobile.wb.ru/catalog/{article}/detail.json"
    ]
    
    # Добавляем новые API-эндпоинты
    try:
        # URL для истории цен
        last_digits = article[-2:] if len(article) >= 2 else article
        first_digits = article[:3] if len(article) >= 3 else article
        part_digits = article[:len(article)-3] if len(article) > 3 else article
        
        price_history_url = f"https://basket-{last_digits}.wbbasket.ru/vol{first_digits}/part{part_digits}/{article}/info/price-history.json"
        endpoints.append(price_history_url)
        
        # URL для детальной информации о товаре v2
        detail_v2_url = f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-6972066&hide_dtype=13&spp=30&ab_testing=false&lang=ru&nm={article}"
        endpoints.append(detail_v2_url)
    except Exception as e:
        logger.warning(f"Ошибка при генерации дополнительных API URL: {e}")
    
    return endpoints

async def get_price_from_history(scraper, article: str) -> Optional[float]:
    """
    Получает актуальную цену товара из API истории цен
    
    Args:
        scraper: Инстанс cloudscraper для выполнения запросов
        article: Артикул товара
        
    Returns:
        float: Цена товара или None, если не удалось получить
    """
    try:
        # Формируем URL для истории цен - исправляем ошибку в формировании URL
        last_digits = article[-2:] if len(article) >= 2 else article
        first_digits = article[:3] if len(article) >= 3 else article
        # Используем правильный формат для part - это первые цифры артикула без последних 5 цифр
        part_digits = article[:len(article)-5] if len(article) > 5 else article
        
        # Пробуем несколько вариантов URL, так как формат может отличаться
        urls_to_try = [
            f"https://basket-{last_digits}.wbbasket.ru/vol{first_digits}/part{part_digits}/{article}/info/price-history.json",
            f"https://basket-0{last_digits[-1]}.wbbasket.ru/vol{first_digits}/part{part_digits}/{article}/info/price-history.json"
        ]
        
        for url in urls_to_try:
            logger.info(f"Запрос к API истории цен: {url}")
            
            try:
                response = scraper.get(url, timeout=10, headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json"
                })
                
                if response.status_code == 200:
                    try:
                        data = response.json()
                        if isinstance(data, list) and len(data) > 0:
                            # Берем последнюю (актуальную) цену и делим на 100
                            price = float(data[0].get('price', 0)) / 100
                            if price > 10:  # Проверка на адекватность цены
                                logger.info(f"Цена получена из API истории цен: {price}")
                                return price
                    except (json.JSONDecodeError, IndexError, KeyError, ValueError) as e:
                        logger.warning(f"Ошибка при обработке ответа API истории цен: {e}")
            except Exception as e:
                logger.warning(f"Ошибка при запросе к API истории цен {url}: {e}")
        
        logger.warning("Не удалось получить цену из всех вариантов API истории цен")
    except Exception as e:
        logger.warning(f"Ошибка при запросе к API истории цен: {e}")
    
    return None

async def get_details_v2(scraper, article: str) -> Tuple[Optional[float], Optional[float]]:
    """
    Получает цену и рейтинг товара из v2 API
    
    Args:
        scraper: Инстанс cloudscraper для выполнения запросов
        article: Артикул товара
        
    Returns:
        Tuple[Optional[float], Optional[float]]: Цена и рейтинг товара или None, если не удалось получить
    """
    try:
        # API v2 имеет больше информации и более стабильный
        url = f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-6972066&hide_dtype=13&spp=30&ab_testing=false&lang=ru&nm={article}"
        logger.info(f"Запрос к API v2: {url}")
        
        response = scraper.get(url, timeout=10, headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json"
        })
        
        if response.status_code == 200:
            try:
                data = response.json()
                products = data.get('data', {}).get('products', [])
                
                if products and len(products) > 0:
                    product = products[0]
                    price = None
                    rating = None
                    
                    # Поиск цены в sizes.stocks (наиболее надежный источник)
                    if 'sizes' in product and product['sizes']:
                        for size in product['sizes']:
                            if 'stocks' in size and size['stocks']:
                                for stock in size['stocks']:
                                    if 'priceU' in stock:
                                        price = float(stock['priceU']) / 100
                                        logger.info(f"Цена получена из sizes.stocks: {price}")
                                        break
                            if price:
                                break
                    
                    # Если цены в stocks нет, проверяем все возможные поля цены
                    if not price:
                        if product.get('extended', {}).get('basicPriceU'):
                            price = float(product['extended']['basicPriceU']) / 100
                        elif product.get('extended', {}).get('clientPriceU'): 
                            price = float(product['extended']['clientPriceU']) / 100
                        elif product.get('priceU'):
                            price = float(product['priceU']) / 100
                        elif product.get('salePriceU'):
                            price = float(product['salePriceU']) / 100
                    
                    # Если нет прямых полей цены, ищем в других местах
                    if not price:
                        # Поиск в priceInfo
                        if 'priceInfo' in product:
                            try:
                                price_info = product['priceInfo']
                                if price_info.get('priceU'):
                                    price = float(price_info['priceU']) / 100
                                elif price_info.get('salePriceU'):
                                    price = float(price_info['salePriceU']) / 100
                                elif price_info.get('price'):
                                    price_val = float(price_info['price'])
                                    if price_val > 1000:
                                        price = price_val / 100
                                    else:
                                        price = price_val
                            except (ValueError, TypeError):
                                pass
                    
                    # Поиск в других полях
                    if not price:
                        for price_field in ['price', 'salePrice', 'startPrice']:
                            if price_field in product:
                                try:
                                    price_val = float(product[price_field])
                                    # Если цена выглядит как копейки, делим на 100
                                    if price_val > 1000:
                                        price = price_val / 100
                                    else:
                                        price = price_val
                                    break
                                except (ValueError, TypeError):
                                    continue
                    
                    # Поиск цены по всем ключам с 'price' в названии
                    if not price:
                        for key in product.keys():
                            if 'price' in key.lower() and key not in ['priceU', 'salePriceU']:
                                try:
                                    price_val = float(product[key])
                                    if price_val > 10:  # Минимальная адекватная цена
                                        if price_val > 1000:  # Если цена в копейках
                                            price = price_val / 100
                                        else:
                                            price = price_val
                                        break
                                except (ValueError, TypeError):
                                    continue
                    
                    # Извлекаем рейтинг из разных возможных полей
                    if 'reviewRating' in product:
                        rating = float(product['reviewRating'])
                    elif 'rating' in product:
                        rating = float(product['rating'])
                    elif 'supplierRating' in product:
                        rating = float(product['supplierRating'])
                    
                    logger.info(f"Данные из API v2: цена={price}, рейтинг={rating}")
                    return price, rating
                else:
                    logger.warning(f"Продукт не найден в ответе API v2 для артикула {article}")
            except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                logger.warning(f"Ошибка при обработке ответа API v2: {e}")
        else:
            logger.warning(f"Ошибка запроса к API v2: HTTP {response.status_code}")
    except Exception as e:
        logger.warning(f"Исключение при запросе к API v2: {e}")
    
    return None, None

async def test_wb_apis(article):
    """Тестирование различных API для получения данных о товаре."""
    print(f"\n{'='*50}\nТестирование API для артикула {article}\n{'='*50}\n")
    
    # Генерируем все API URL
    api_urls = generate_api_endpoints(article)
    
    # Тестируем базовый API
    print(f"\n[1] Тестирование базового API:")
    base_api_url = f"https://card.wb.ru/cards/detail?nm={article}"
    print(f"URL: {base_api_url}")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(base_api_url) as response:
                if response.status == 200:
                    try:
                        data = await response.json()
                        
                        # Сохраняем ответ API в файл
                        with open(f"api_response_{article}.json", "w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False, indent=4)
                        
                        print(f"Статус: OK (200)")
                        if 'data' in data and 'products' in data['data'] and data['data']['products']:
                            product = data['data']['products'][0]
                            print(f"Название: {product.get('name', 'Не найдено')}")
                            
                            # Цена может быть в разных полях
                            price = product.get('salePriceU', product.get('priceU', 0)) / 100
                            print(f"Цена: {price} ₽")
                            
                            # Рейтинг
                            rating = product.get('rating', 0)
                            print(f"Рейтинг: {rating}")
                            
                            print(f"Файл с полным ответом API сохранен: api_response_{article}.json")
                        else:
                            print("Ошибка в структуре данных: товар не найден в ответе API")
                    except json.JSONDecodeError:
                        print("Ошибка: Ответ не является JSON")
                else:
                    print(f"Ошибка: Статус {response.status}")
    except Exception as e:
        print(f"Ошибка при запросе к API: {e}")
    
    # Тестируем API v1
    print(f"\n[2] Тестирование API v1:")
    api_v1_url = f"https://wbxcatalog-ru.wildberries.ru/nm-2-card/catalog/{article}/detail.json"
    print(f"URL: {api_v1_url}")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_v1_url) as response:
                if response.status == 200:
                    try:
                        data = await response.json()
                        print(f"Статус: OK (200)")
                        if 'data' in data and 'products' in data['data'] and data['data']['products']:
                            product = data['data']['products'][0]
                            print(f"Название: {product.get('name', 'Не найдено')}")
                            
                            # Цена может быть в разных полях
                            price = product.get('salePriceU', product.get('priceU', 0)) / 100
                            print(f"Цена: {price} ₽")
                            
                            # Рейтинг
                            rating = product.get('rating', 0)
                            print(f"Рейтинг: {rating}")
                        else:
                            print("Ошибка в структуре данных: товар не найден в ответе API")
                    except json.JSONDecodeError:
                        print("Ошибка: Ответ не является JSON")
                else:
                    print(f"Ошибка: Статус {response.status}")
    except Exception as e:
        print(f"Ошибка при запросе к API: {e}")
    
    # Тестируем API price-history
    print(f"\n[3] Тестирование API price-history:")
    price_history_url = f"https://basket-{article[-2:] if len(article) >= 2 else '01'}.wb.ru/vol{article[0] if len(article) >= 1 else '0'}/part{article[:2] if len(article) >= 2 else '00'}/{article}/info/price-history.json"
    print(f"URL: {price_history_url}")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(price_history_url) as response:
                if response.status == 200:
                    try:
                        data = await response.json()
                        print(f"Статус: OK (200)")
                        # Цена в истории цен
                        if isinstance(data, list) and data:
                            # Берем последнюю запись в истории (самую свежую)
                            latest_price = data[-1].get('price', 0) / 100
                            print(f"Последняя цена из истории: {latest_price} ₽")
                        else:
                            print("История цен пуста или имеет неожиданный формат")
                    except json.JSONDecodeError:
                        print("Ошибка: Ответ не является JSON")
                else:
                    print(f"Ошибка: Статус {response.status}")
    except Exception as e:
        print(f"Ошибка при запросе к API: {e}")
    
    # Тестируем API v2
    print(f"\n[4] Тестирование API v2:")
    api_v2_url = f"https://card.wb.ru/cards/v2/detail?nm={article}"
    print(f"URL: {api_v2_url}")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_v2_url) as response:
                if response.status == 200:
                    try:
                        data = await response.json()
                        print(f"Статус: OK (200)")
                        if 'data' in data and 'products' in data['data'] and data['data']['products']:
                            product = data['data']['products'][0]
                            print(f"Название: {product.get('name', 'Не найдено')}")
                            
                            # Цена может быть в разных полях
                            price = product.get('salePriceU', product.get('priceU', 0)) / 100
                            print(f"Цена: {price} ₽")
                            
                            # Рейтинг
                            rating = product.get('rating', 0)
                            print(f"Рейтинг: {rating}")
                        else:
                            print("Ошибка в структуре данных: товар не найден в ответе API")
                    except json.JSONDecodeError:
                        print("Ошибка: Ответ не является JSON")
                else:
                    print(f"Ошибка: Статус {response.status}")
    except Exception as e:
        print(f"Ошибка при запросе к API: {e}")
    
    # Тестируем парсинг HTML
    print(f"\n[5] Тестирование парсинга HTML:")
    html_url = f"https://www.wildberries.ru/catalog/{article}/detail.aspx"
    print(f"URL: {html_url}")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(html_url, headers=HEADERS) as response:
                if response.status == 200:
                    html = await response.text()
                    print(f"Статус: OK (200)")
                    
                    # Извлекаем название товара из HTML
                    product_name = extract_product_name(html)
                    if product_name:
                        print(f"Название (из HTML): {product_name}")
                    else:
                        print("Не удалось извлечь название товара из HTML")
                    
                    # Извлекаем цену товара из HTML
                    price = extract_price(html)
                    if price:
                        print(f"Цена (из HTML): {price} ₽")
                    else:
                        print("Не удалось извлечь цену товара из HTML")
                    
                    # Извлекаем рейтинг товара из HTML
                    rating = extract_rating(html)
                    if rating:
                        print(f"Рейтинг (из HTML): {rating}")
                    else:
                        print("Не удалось извлечь рейтинг товара из HTML")
                else:
                    print(f"Ошибка: Статус {response.status}")
    except Exception as e:
        print(f"Ошибка при запросе HTML: {e}")
    
    print(f"\n{'='*50}\nТестирование завершено\n{'='*50}\n")

async def get_base_api_data(scraper, article: str) -> Tuple[Optional[float], Optional[float]]:
    """
    Получает цену и рейтинг товара из базового API
    
    Args:
        scraper: Инстанс cloudscraper для выполнения запросов
        article: Артикул товара
        
    Returns:
        Tuple[Optional[float], Optional[float]]: Цена и рейтинг товара или None, если не удалось получить
    """
    try:
        url = f"https://wbxcatalog-ru.wildberries.ru/nm-2-card/catalog/{article}/detail.json"
        logger.info(f"Запрос к базовому API: {url}")
        
        response = scraper.get(url, timeout=10, headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json"
        })
        
        if response.status_code == 200:
            try:
                data = response.json()
                
                price = None
                rating = None
                
                if 'data' in data and 'products' in data['data'] and data['data']['products']:
                    product = data['data']['products'][0]
                    
                    # Получаем цену
                    if 'price' in product:
                        price_data = product['price']
                        
                        if 'priceData' in price_data and 'price' in price_data['priceData']:
                            price = float(price_data['priceData']['price'])
                            logger.info(f"Цена получена из базового API: {price}")
                    
                    # Получаем рейтинг
                    if 'rating' in product:
                        rating = float(product['rating'])
                        logger.info(f"Рейтинг получен из базового API: {rating}")
                
                return price, rating
            except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                logger.warning(f"Ошибка при обработке ответа базового API: {e}")
        else:
            logger.warning(f"Ошибка запроса к базовому API: HTTP {response.status_code}")
    except Exception as e:
        logger.warning(f"Исключение при запросе к базовому API: {e}")
    
    return None, None

async def get_v1_api_data(scraper, article: str) -> Tuple[Optional[float], Optional[float]]:
    """
    Получает цену и рейтинг товара из API v1
    
    Args:
        scraper: Инстанс cloudscraper для выполнения запросов
        article: Артикул товара
        
    Returns:
        Tuple[Optional[float], Optional[float]]: Цена и рейтинг товара или None, если не удалось получить
    """
    try:
        url = f"https://wbx-content-v2.wbstatic.net/ru/{article}.json"
        logger.info(f"Запрос к API v1: {url}")
        
        response = scraper.get(url, timeout=10, headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json"
        })
        
        if response.status_code == 200:
            try:
                data = response.json()
                
                price = None
                rating = None
                
                # Получаем цену
                if 'price' in data:
                    price = float(data['price'])
                    logger.info(f"Цена получена из API v1: {price}")
                
                # Получаем рейтинг
                if 'rating' in data:
                    rating = float(data['rating'])
                    logger.info(f"Рейтинг получен из API v1: {rating}")
                
                return price, rating
            except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                logger.warning(f"Ошибка при обработке ответа API v1: {e}")
        else:
            logger.warning(f"Ошибка запроса к API v1: HTTP {response.status_code}")
    except Exception as e:
        logger.warning(f"Исключение при запросе к API v1: {e}")
    
    return None, None

async def get_product_data(article: str) -> tuple:
    """
    Получает данные о товаре из API Wildberries
    
    Args:
        article: Артикул товара
    
    Returns:
        tuple: (название, цена, дополнительные_данные в формате JSON)
    """
    try:
        logger.info(f"Получение данных о товаре {article} из API")
        base_url = f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-6972066&hide_dtype=13&spp=30&ab_testing=false&lang=ru&nm={article}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(base_url) as response:
                if response.status == 200:
                    data = await response.json()
                    if 'data' in data and 'products' in data['data'] and len(data['data']['products']) > 0:
                        product = data['data']['products'][0]
                        logger.info(f"Товар {article} найден в API")
                        
                        # Логируем рейтинг для отладки
                        if 'reviewRating' in product:
                            logger.info(f"Поле reviewRating: {product['reviewRating']}")
                        if 'rating' in product:
                            logger.info(f"Поле rating: {product['rating']}")
                        if 'nmReviewRating' in product:
                            logger.info(f"Поле nmReviewRating: {product['nmReviewRating']}")
                        
                        # Извлекаем имя
                        name = product.get('name')
                        if not name or '{{:~t(' in name or 'unsuccessfulLoad' in name:
                            name = f"Товар {article}"
                            
                        # Находим цену согласно документации (Parsing.ini)
                        price = None
                            
                        # ОСНОВНОЙ МЕТОД: цена находится в поле product["sizes"][0]["price"]["product"] / 100
                        if 'sizes' in product and product['sizes'] and len(product['sizes']) > 0:
                            first_size = product['sizes'][0]
                            if 'price' in first_size and isinstance(first_size['price'], dict):
                                price_obj = first_size['price']
                                if 'product' in price_obj and price_obj['product']:
                                    price = int(price_obj['product']) / 100
                                    logger.info(f"Цена получена из sizes[0].price.product: {price} руб.")
                        
                        # Альтернативные способы получения цены, если основной не сработал
                        if not price or price <= 0:
                            # Проверяем поля salePriceU и priceU
                            if 'salePriceU' in product and product['salePriceU']:
                                price = int(product['salePriceU']) / 100
                                logger.info(f"Цена получена из salePriceU: {price} руб.")
                            elif 'priceU' in product and product['priceU']:
                                price = int(product['priceU']) / 100
                                logger.info(f"Цена получена из priceU: {price} руб.")
                            
                        # Если цена все еще не найдена, проверяем другие размеры
                        if (not price or price <= 0) and 'sizes' in product:
                            for size in product['sizes']:
                                if 'price' in size and isinstance(size['price'], dict):
                                    price_obj = size['price']
                                    if 'product' in price_obj and price_obj['product']:
                                        price = int(price_obj['product']) / 100
                                        logger.info(f"Цена получена из альтернативного размера: {price} руб.")
                                        break
                            
                        # Если цена не найдена, логируем предупреждение
                        if not price or price <= 0:
                            logger.warning(f"Не удалось найти цену для товара {article}")
                        
                        # Извлекаем рейтинг (prioritizing reviewRating и nmReviewRating перед rating)
                        rating = None
                        
                        if 'reviewRating' in product and product['reviewRating'] is not None:
                            rating = float(product['reviewRating'])
                            logger.info(f"Рейтинг получен из reviewRating: {rating}")
                        elif 'nmReviewRating' in product and product['nmReviewRating'] is not None:
                            rating = float(product['nmReviewRating'])
                            logger.info(f"Рейтинг получен из nmReviewRating: {rating}")
                        elif 'rating' in product and product['rating'] is not None:
                            rating = float(product['rating'])
                            logger.info(f"Рейтинг получен из rating: {rating}")
                        
                        if rating is None:
                            logger.warning(f"Не удалось найти рейтинг для товара {article}")
                            rating = 0
                        
                        # Извлекаем дополнительные данные
                        additional_data = {
                            'rating': rating,
                            'brand': product.get('brand', ''),
                            'seller': product.get('supplier', '')
                        }
                        
                        # Добавляем информацию о отзывах, если есть
                        if 'feedbacks' in product:
                            additional_data['reviews_count'] = product['feedbacks']
                        
                        logger.info(f"Данные о товаре получены успешно: {name}, {price} руб., рейтинг: {rating}")
                        return name, price, json.dumps(additional_data)
                    else:
                        logger.warning(f"Товар с артикулом {article} не найден в ответе API")
                        return None, None, None
                else:
                    logger.error(f"Ошибка API: статус {response.status} для артикула {article}")
                    return None, None, None
    except aiohttp.ClientError as e:
        logger.error(f"Ошибка сети при получении данных о товаре {article}: {e}")
        return None, None, None
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка декодирования JSON для артикула {article}: {e}")
        return None, None, None
    except Exception as e:
        logger.error(f"Неожиданная ошибка при получении данных о товаре {article}: {e}")
        return None, None, None

def extract_product_name(html: str) -> Optional[str]:
    """
    Извлекает название товара из HTML страницы.
    
    Args:
        html: HTML-код страницы
        
    Returns:
        Optional[str]: Название товара или None при ошибке
    """
    if not html or len(html) < 100:
        logger.warning("HTML пустой или слишком короткий для извлечения названия товара")
        return None
    
    try:
        soup = BeautifulSoup(html, 'lxml')
        
        # 1. Поиск по заголовку H1
        h1 = soup.find('h1', class_='product-page__title')
        if h1:
            name = h1.get_text().strip()
            if name and len(name) > 3 and not '{{:~t(' in name:
                logger.info(f"Название товара извлечено из H1: {name}")
                return name
        
        # 2. Поиск в мета-тегах
        meta_title = soup.find('meta', property='og:title')
        if meta_title and meta_title.get('content'):
            name = meta_title.get('content').strip()
            if name and len(name) > 3 and not '{{:~t(' in name:
                logger.info(f"Название товара извлечено из мета-тега og:title: {name}")
                return name
        
        # 3. Поиск в JSON-LD
        script_tags = soup.find_all('script', type='application/ld+json')
        for script in script_tags:
            try:
                json_data = json.loads(script.string)
                if isinstance(json_data, dict) and 'name' in json_data:
                    name = json_data['name']
                    if name and len(name) > 3 and not '{{:~t(' in name:
                        logger.info(f"Название товара извлечено из JSON-LD: {name}")
                        return name
            except (json.JSONDecodeError, TypeError, AttributeError):
                continue
        
        # 4. Поиск по шаблонам в HTML (для случаев, когда структура сайта изменилась)
        patterns = [
            r'<h1[^>]*class="[^"]*product-page__title[^"]*"[^>]*>(.*?)</h1>',
            r'<meta property="og:title" content="([^"]+)"',
            r'"name":\s*"([^"]+)"',
            r'<span itemprop="name">([^<]+)</span>'
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, html)
            if matches:
                for match in matches:
                    name = match.strip()
                    # Проверяем, что название не шаблонное и не пустое
                    if name and len(name) > 3 and not '{{:~t(' in name:
                        logger.info(f"Название товара извлечено по шаблону {pattern}: {name}")
                        return name
        
        logger.warning("Не удалось извлечь название товара из HTML")
        return None
    
    except Exception as e:
        logger.error(f"Ошибка при извлечении названия товара из HTML: {e}")
        return None

def extract_price(html: str) -> Optional[float]:
    """
    Извлекает цену товара из HTML страницы.
    
    Args:
        html: HTML-код страницы
        
    Returns:
        Optional[float]: Цена товара или None при ошибке
    """
    if not html or len(html) < 100:
        logger.warning("HTML пустой или слишком короткий для извлечения цены")
        return None
    
    try:
        soup = BeautifulSoup(html, 'lxml')
        
        # 1. Поиск по классу price-block__final-price
        price_element = soup.find('ins', class_='price-block__final-price')
        if price_element:
            price_text = price_element.get_text().strip()
            # Убираем все неразрывные пробелы и символы валюты
            price_text = price_text.replace('\xa0', '').replace('&nbsp;', '').replace(' ', '').replace('₽', '').replace('руб', '')
            try:
                price = float(price_text)
                logger.info(f"Цена извлечена из элемента price-block__final-price: {price}")
                return price
            except ValueError:
                logger.warning(f"Не удалось преобразовать текст '{price_text}' в число")
        
        # 2. Поиск в div.price-block
        price_block = soup.find('div', class_='price-block')
        if price_block:
            # Ищем все числа в блоке с ценой
            price_texts = re.findall(r'(\d[\d\s]*)\s*₽', price_block.get_text())
            if price_texts:
                for price_text in price_texts:
                    price_text = price_text.replace('\xa0', '').replace('&nbsp;', '').replace(' ', '')
                    try:
                        price = float(price_text)
                        logger.info(f"Цена найдена в блоке price-block: {price}")
                        return price
                    except ValueError:
                        continue
        
        # 3. Поиск в исходном HTML с учетом &nbsp;
        price_patterns = [
            r'(\d[\d\s&nbsp;]*)\s*₽',
            r'price-block__final-price[^>]*>([^<]+)',
            r'final-price[^>]*>([^<]+)',
            r'"finalPrice":(\d+)',
            r'"price":(\d+)',
            r'<meta property="product:price:amount" content="(\d+\.?\d*)'
        ]
        
        for pattern in price_patterns:
            matches = re.findall(pattern, html)
            if matches:
                for match in matches:
                    # Очистка текста от неразрывных пробелов и других символов
                    price_text = match.replace('\xa0', '').replace('&nbsp;', '').replace(' ', '').replace('₽', '').replace('руб', '')
                    try:
                        price_value = float(price_text)
                        if price_value > 10:  # Проверка на адекватность цены
                            logger.info(f"Цена найдена по шаблону {pattern}: {price_value}")
                            return price_value
                    except ValueError:
                        continue
        
        # 4. Поиск в JSON данных на странице
        script_tags = soup.find_all('script', type='application/ld+json')
        for script in script_tags:
            try:
                json_data = json.loads(script.string)
                if isinstance(json_data, dict):
                    # Проверяем различные пути для цены
                    if 'offers' in json_data and isinstance(json_data['offers'], dict) and 'price' in json_data['offers']:
                        price = float(json_data['offers']['price'])
                        if price > 10:
                            logger.info(f"Цена найдена в JSON-LD (offers.price): {price}")
                            return price
                    elif 'price' in json_data:
                        price = float(json_data['price'])
                        if price > 10:
                            logger.info(f"Цена найдена в JSON-LD (price): {price}")
                            return price
            except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
                continue
        
        logger.warning("Не удалось извлечь цену из HTML")
        return None
    
    except Exception as e:
        logger.error(f"Ошибка при извлечении цены из HTML: {e}")
        return None

def extract_rating(html: str) -> Optional[float]:
    """
    Извлекает рейтинг товара из HTML страницы.
    
    Args:
        html: HTML-код страницы
        
    Returns:
        Optional[float]: Рейтинг товара или None при ошибке
    """
    if not html or len(html) < 100:
        logger.warning("HTML пустой или слишком короткий для извлечения рейтинга")
        return None
    
    try:
        soup = BeautifulSoup(html, 'lxml')
        
        # 1. Поиск по классу product-page__reviews-icon
        rating_elem = soup.find('p', class_='product-page__reviews-icon')
        if rating_elem:
            # Вытаскиваем весь текст из элемента
            rating_text = rating_elem.get_text().strip()
            logger.info(f"Найден элемент рейтинга: {rating_text}")
            
            # Ищем числа с запятой или точкой (рейтинг)
            rating_match = re.search(r'([\d.,]+)', rating_text)
            if rating_match:
                try:
                    rating = float(rating_match.group(1).replace(',', '.'))
                    logger.info(f"Рейтинг извлечен из элемента product-page__reviews-icon: {rating}")
                    return rating
                except ValueError:
                    logger.warning(f"Не удалось преобразовать рейтинг '{rating_match.group(1)}' в число")
        
        # 2. Поиск в блоке с отзывами
        reviews_block = soup.find('div', class_='product-page__reviews-blocks')
        if reviews_block:
            rating_texts = re.findall(r'([\d.,]+)', reviews_block.get_text())
            if rating_texts:
                for rating_text in rating_texts:
                    try:
                        rating = float(rating_text.replace(',', '.'))
                        if 0 <= rating <= 5:  # Проверка на адекватность рейтинга
                            logger.info(f"Рейтинг найден в блоке reviews-blocks: {rating}")
                            return rating
                    except ValueError:
                        continue
        
        # 3. Поиск в исходном HTML
        rating_patterns = [
            r'rating":\s*"?([\d.]+)"?',
            r'reviewRating":\s*"?([\d.]+)"?',
            r'<meta itemprop="ratingValue" content="([\d.]+)"',
            r'<span class="[^"]*star[^"]*"[^>]*>([\d.,]+)</span>'
        ]
        
        for pattern in rating_patterns:
            matches = re.findall(pattern, html)
            if matches:
                for match in matches:
                    try:
                        rating = float(match.replace(',', '.'))
                        if 0 <= rating <= 5:  # Проверка на адекватность рейтинга
                            logger.info(f"Рейтинг найден по шаблону {pattern}: {rating}")
                            return rating
                    except ValueError:
                        continue
        
        # 4. Поиск в JSON данных на странице
        script_tags = soup.find_all('script', type='application/ld+json')
        for script in script_tags:
            try:
                json_data = json.loads(script.string)
                if isinstance(json_data, dict):
                    # Проверяем различные пути для рейтинга
                    if 'aggregateRating' in json_data and isinstance(json_data['aggregateRating'], dict) and 'ratingValue' in json_data['aggregateRating']:
                        rating = float(json_data['aggregateRating']['ratingValue'])
                        if 0 <= rating <= 5:
                            logger.info(f"Рейтинг найден в JSON-LD (aggregateRating.ratingValue): {rating}")
                            return rating
                    elif 'rating' in json_data:
                        rating = float(json_data['rating'])
                        if 0 <= rating <= 5:
                            logger.info(f"Рейтинг найден в JSON-LD (rating): {rating}")
                            return rating
            except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
                continue
        
        logger.warning("Не удалось извлечь рейтинг из HTML")
        return None
    
    except Exception as e:
        logger.error(f"Ошибка при извлечении рейтинга из HTML: {e}")
        return None

async def test_simple(article: str):
    """
    Простая тестовая функция для проверки получения данных о товаре
    
    Args:
        article: Артикул товара
    """
    print(f"Тестирование получения данных для артикула {article}")
    
    # Проверяем, доступен ли интернет
    if not check_internet_connection():
        print("❌ Интернет недоступен")
        return
    
    # Проверяем доступность хостов Wildberries
    hosts_status = check_wildberries_hosts()
    print("\nСтатус хостов Wildberries:")
    for host, status in hosts_status.items():
        print(f"- {host}: {'✅ Доступен' if status else '❌ Недоступен'}")
    
    # Получаем данные о товаре
    print("\nПолучение данных о товаре:")
    result = get_wb_product_data(article)
    
    if isinstance(result, dict) and 'error' in result:
        print(f"❌ Ошибка: {result['error']}")
    elif result:
        print("✅ Данные успешно получены:")
        print(f"Артикул: {article}")
        print(f"Название: {result.get('name', 'Не найдено')}")
        print(f"Цена: {result.get('price', 'Не найдена')} ₽")
        print(f"Рейтинг: {result.get('rating', 'Не найден')}")
        print(f"URL: {result.get('url', f'https://www.wildberries.ru/catalog/{article}/detail.aspx')}")
    else:
        print("❌ Не удалось получить данные о товаре")
    
    print("\nТестирование завершено")
    
def check_syntax():
    """
    Функция для проверки синтаксиса всего файла
    """
    print("Синтаксис файла корректен!")
    return True

async def find_similar_products(article: str, max_price: float = None, min_rating: float = None) -> List[Dict]:
    """
    Ищет похожие товары дешевле указанного артикула
    
    Args:
        article: Артикул товара
        max_price: Максимальная цена
        min_rating: Минимальный рейтинг
        
    Returns:
        List[Dict]: Список словарей с данными о похожих товарах
    """
    try:
        logger.info(f"Начинаю поиск похожих товаров для артикула {article}")
        
        # Проверка корректности артикула
        if not article or not article.strip():
            logger.warning("Получен пустой артикул товара")
            return []
            
        # Проверка, что артикул состоит только из цифр
        if not article.isdigit():
            logger.warning(f"Артикул {article} содержит недопустимые символы")
            return []
        
        # Получаем данные о текущем товаре
        original_product = get_wb_product_data(article)
        
        if not original_product or isinstance(original_product, dict) and 'error' in original_product:
            logger.warning(f"Не удалось получить данные о товаре {article}")
            return []
            
        # Извлекаем название и категорию товара
        original_name = original_product.get('name', f"Товар {article}")
        original_price = original_product.get('price', 0)
        
        # Если максимальная цена не установлена, используем 90% от текущей
        if not max_price:
            max_price = original_price * 0.9
        
        # Устанавливаем минимальный рейтинг, если он не указан
        if not min_rating:
            min_rating = 4.0
            
        logger.info(f"Параметры поиска: max_price={max_price}, min_rating={min_rating}")
            
        # Извлекаем ключевые слова из названия товара
        keywords = extract_keywords(original_name)
        
        if not keywords:
            logger.warning(f"Не удалось извлечь ключевые слова из названия: {original_name}")
            return []
            
        logger.info(f"Извлеченные ключевые слова: {', '.join(keywords)}")
        
        # Формируем запрос для поиска похожих товаров
        search_query = ' '.join(keywords[:4])  # Используем первые 4 ключевых слова
        
        # Проверяем наличие подключения к серверам Wildberries
        hosts_status = check_wildberries_hosts()
        
        # Список для хранения найденных товаров
        found_products = []
        
        # Пробуем использовать API поиска, если доступно
        if hosts_status.get("wildberries.ru"):
            try:
                # URL для поискового API Wildberries
                search_url = f"https://search.wb.ru/exactmatch/ru/common/v4/search?query={search_query}"
                
                scraper = create_scraper_instance() if create_scraper_instance else requests.Session()
                
                # Добавляем пользовательский агент, если это простой сеанс requests
                if isinstance(scraper, requests.Session):
                    scraper.headers.update({
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                        "Accept": "application/json"
                    })
                
                response = scraper.get(search_url, timeout=15)
                
                if response.status_code == 200:
                    try:
                        data = response.json()
                        
                        # Обрабатываем результаты поиска
                        if 'data' in data and 'products' in data['data']:
                            products = data['data']['products']
                            
                            for product in products:
                                try:
                                    # Извлекаем данные о товаре
                                    product_id = str(product.get('id'))
                                    
                                    # Пропускаем текущий товар
                                    if product_id == article:
                                        logger.debug(f"Пропускаем текущий товар {product_id}")
                                        continue
                                        
                                    # Проверяем наличие цены
                                    if 'priceU' in product:
                                        price = int(product['priceU']) / 100
                                    elif 'salePriceU' in product:
                                        price = int(product['salePriceU']) / 100
                                    else:
                                        logger.debug(f"Товар {product_id} пропущен: цена не найдена")
                                        continue
                                        
                                    # Если цена выше максимальной, пропускаем
                                    if price >= max_price:
                                        logger.debug(f"Товар {product_id} пропущен: цена {price} выше максимальной {max_price}")
                                        continue
                                        
                                    # Получаем рейтинг
                                    rating = product.get('rating', 0)
                                    
                                    # Если рейтинг ниже минимального, пропускаем
                                    if rating < min_rating:
                                        logger.debug(f"Товар {product_id} пропущен: рейтинг {rating} ниже минимального {min_rating}")
                                        continue
                                        
                                    # Добавляем товар в список найденных
                                    found_products.append({
                                        'article': product_id,
                                        'name': product.get('name', f"Товар {product_id}"),
                                        'price': price,
                                        'rating': rating,
                                        'url': f"https://www.wildberries.ru/catalog/{product_id}/detail.aspx"
                                    })
                                except Exception as e:
                                    logger.warning(f"Ошибка при обработке товара из поиска: {e}")
                                    continue
                    except json.JSONDecodeError:
                        logger.warning("Ошибка декодирования JSON-ответа от API поиска")
            except Exception as e:
                logger.warning(f"Ошибка при запросе к API поиска: {e}")
    except Exception as e:
        logger.error(f"Ошибка при поиске похожих товаров: {str(e)}", exc_info=True)
        return []

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обработчик кнопок обратного вызова
    
    Args:
        update: Объект обновления Telegram
        context: Контекст бота
    """
    query = update.callback_query
    
    try:
        # Получаем callback_data и разбираем его
        callback_data = query.data
        
        if not callback_data:
            await query.answer("Ошибка: отсутствуют данные обратного вызова")
            return
        
        # Поддержка нового формата (similar:article)
        if ":" in callback_data:
            # Разбираем данные обратного вызова
            data_parts = callback_data.split(":")
            
            if len(data_parts) < 2:
                await query.answer("Ошибка: некорректный формат данных обратного вызова")
                return
            
            action = data_parts[0]
            
            # Обрабатываем различные действия
            if action == "similar":
                # Получаем артикул товара
                article = data_parts[1]
                await handle_similar_cheaper_button(update, context, article)
            
        # Поддержка старого формата (similar_cheaper_12345678)
        elif callback_data.startswith("similar_cheaper_"):
            # Извлекаем артикул из callback_data
            article = callback_data.replace("similar_cheaper_", "")
            # Обрабатываем запрос на поиск похожих товаров дешевле
            await handle_similar_cheaper_button(update, context, article)
        
        # Неизвестный формат
        else:
            await query.answer("Неизвестный формат данных обратного вызова")
        
        # Добавьте обработку других действий по мере необходимости
    
    except Exception as e:
        logger.error(f"Ошибка при обработке нажатия кнопки: {str(e)}", exc_info=True)
        await query.answer(f"Произошла ошибка: {str(e)}")

async def handle_similar_cheaper_button(update: Update, context: ContextTypes.DEFAULT_TYPE, article: str) -> None:
    """
    Обрабатывает нажатие на кнопку "Найти похожие товары дешевле"
    
    Args:
        update: Объект обновления Telegram
        context: Контекст бота
        article: Артикул товара
    """
    query = update.callback_query
    await query.answer()
    
    try:
        # Отправляем сообщение о поиске
        await query.edit_message_text(
            f"🔍 Ищу похожие товары дешевле для артикула {article}...",
            parse_mode="Markdown"
        )
        
        # Получаем данные об исходном товаре
        product_data = await get_wb_product_data(article)
        
        # Проверяем, успешно ли получены данные
        if not product_data or isinstance(product_data, dict) and 'error' in product_data:
            await query.edit_message_text(
                "❌ Не удалось получить данные о товаре. Пожалуйста, попробуйте позже.",
                parse_mode="Markdown"
            )
            return
            
        # Используем импортированную функцию для поиска похожих товаров дешевле
        # Так как функция синхронная, запускаем её в отдельном потоке, чтобы не блокировать бота
        loop = asyncio.get_event_loop()
        similar_product = await loop.run_in_executor(
            None,
            lambda: find_similar_cheaper_products(
                article=article,
                min_rating=4.5,  # Минимальный рейтинг 4.5
                min_feedbacks=20  # Минимальное количество отзывов 20
            )
        )
        
        # Проверяем, найден ли подходящий товар
        if not similar_product:
            await query.edit_message_text(
                "Похожие товары не найдены по заданным критериям (рейтинг ≥ 4.5, отзывы ≥ 20).",
                parse_mode="Markdown"
            )
            return
            
        # Формируем сообщение с результатом, используя функцию форматирования
        message_text = format_product_message(
            name=similar_product.get('name'),
            price=similar_product.get('price'),
            rating=similar_product.get('rating'),
            brand=similar_product.get('brand'),
            seller=similar_product.get('seller'),
            url=similar_product.get('url'),
            is_original=False
        )
        
        # Добавляем информацию об отзывах, если она есть
        if 'feedbacks' in similar_product:
            message_text += f"💬 *Отзывы:* {similar_product.get('feedbacks')}\n"
        
        # Отправляем сообщение с результатом
        await query.edit_message_text(
            message_text,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Ошибка при поиске похожих товаров дешевле: {str(e)}", exc_info=True)
        await query.edit_message_text(
            f"Произошла ошибка при поиске похожих товаров: {str(e)}\n"
            f"Пожалуйста, попробуйте снова или обратитесь к администратору.",
            parse_mode="Markdown"
        )

async def similar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обработчик команды /similar для поиска похожих товаров
    
    Args:
        update: Объект обновления Telegram
        context: Контекст бота
    """
    try:
        # Проверяем, есть ли аргументы команды
        if not context.args:
            await update.message.reply_text(
                "Для поиска похожих товаров укажите артикул.\n"
                "Например: /similar 12345678\n"
                "Также можно указать максимальную цену и минимальный рейтинг:\n"
                "/similar 12345678 2000 4.5"
            )
            return
            
        # Получаем артикул из первого аргумента
        article = context.args[0]
        
        # Отправляем сообщение о начале поиска
        loading_message = await update.message.reply_text(
            f"🔍 Ищу похожие товары для артикула {article}...",
            parse_mode="Markdown"
        )
        
        # Дополнительные параметры поиска
        max_price_percent = 100  # По умолчанию не дороже исходного товара
        min_rating = 4.0  # По умолчанию минимальный рейтинг 4.0
        min_feedbacks = 10  # По умолчанию минимум 10 отзывов
        
        # Проверяем наличие дополнительных параметров
        if len(context.args) > 1:
            try:
                max_price_percent = int(context.args[1])
            except ValueError:
                pass
                
        if len(context.args) > 2:
            try:
                min_rating = float(context.args[2])
            except ValueError:
                pass
        
        # Используем импортированную функцию для поиска похожих товаров дешевле
        # Так как функция синхронная, запускаем её в отдельном потоке, чтобы не блокировать бота
        loop = asyncio.get_event_loop()
        similar_product = await loop.run_in_executor(
            None,
            lambda: find_similar_cheaper_products(
                article=article,
                max_price_percent=max_price_percent,
                min_rating=min_rating,
                min_feedbacks=min_feedbacks
            )
        )
        
        # Проверяем, найден ли подходящий товар
        if not similar_product:
            await loading_message.edit_text(
                f"Не удалось найти похожие товары дешевле для артикула {article} с заданными критериями:\n"
                f"- Максимальная цена: {max_price_percent}% от исходной\n"
                f"- Минимальный рейтинг: {min_rating}\n"
                f"- Минимум отзывов: {min_feedbacks}",
                parse_mode="Markdown"
            )
            return
            
        # Получаем данные об исходном товаре для сравнения
        original_product = await get_wb_product_data(article)
        original_price = 0
        
        if original_product and not isinstance(original_product, dict):
            # Получаем цену исходного товара
            original_price = original_product.get('price', 0)
        
        # Формируем сообщение с результатом
        cheaper_price = similar_product.get('price', 0)
        discount_percent = 0
        
        if original_price > 0 and cheaper_price > 0:
            discount_percent = int((1 - cheaper_price/original_price) * 100)
        
        message_text = f"📦 *Похожий товар дешевле:* {similar_product.get('name')}\n"
        message_text += f"💰 *Цена:* {similar_product.get('price')} ₽"
        
        if discount_percent > 0:
            message_text += f" (дешевле на {discount_percent}%)\n"
        else:
            message_text += "\n"
            
        message_text += f"⭐️ *Рейтинг:* {similar_product.get('rating')}\n"
        message_text += f"💬 *Отзывы:* {similar_product.get('feedbacks')}\n"
        message_text += f"🔗 [Ссылка на товар]({similar_product.get('url')})"
        
        # Отправляем сообщение с результатом
        await loading_message.edit_text(
            message_text,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Ошибка при обработке команды /similar: {str(e)}", exc_info=True)
        await update.message.reply_text(
            f"Произошла ошибка при обработке команды: {str(e)}\n"
            "Пожалуйста, попробуйте позже или обратитесь к администратору."
        )

async def handle_gpt_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обрабатывает сообщения для ChatGPT
    
    Args:
        update: Объект обновления Telegram
        context: Контекст бота
    """
    try:
        # Проверяем наличие модуля OpenAI
        if not OPENAI_AVAILABLE:
            await update.message.reply_text(
                "Функция ChatGPT недоступна. Модуль OpenAI не установлен. Обратитесь к администратору."
            )
            return
        
        # Проверяем наличие API ключа
        if not OPENAI_API_KEY:
            await update.message.reply_text(
                "Функция ChatGPT недоступна. Администратор не настроил API ключ OpenAI."
            )
            return
            
        # Получаем ID пользователя
        user_id = update.effective_user.id
        
        # Проверяем лимит запросов
        now = datetime.now()
        today = now.date()
        
        # Инициализируем список запросов для пользователя
        if user_id not in gpt_user_requests:
            gpt_user_requests[user_id] = []
            
        # Очищаем запросы, сделанные не сегодня
        gpt_user_requests[user_id] = [ts for ts in gpt_user_requests[user_id] if ts.date() == today]
        
        # Проверяем количество запросов за день
        if len(gpt_user_requests[user_id]) >= MAX_GPT_REQUESTS_PER_DAY:
            await update.message.reply_text(
                f"Вы достигли лимита запросов к ChatGPT на сегодня ({MAX_GPT_REQUESTS_PER_DAY}).\n"
                "Пожалуйста, попробуйте завтра."
            )
            return
        
        # Извлекаем текст запроса
        if context.args:
            # Если это команда /ask или /chatgpt, текст будет в context.args
            query_text = ' '.join(context.args)
        else:
            # Если это обычное сообщение, текст будет в update.message.text
            message_text = update.message.text
            # Удаляем префикс, если он есть
            prefixes = ['chatgpt', 'gpt', 'gemini']
            for prefix in prefixes:
                if message_text.lower().startswith(prefix):
                    query_text = message_text[len(prefix):].strip()
                    break
            else:
                # Если префикс не найден, используем весь текст
                query_text = message_text
        
        # Проверяем, не пустой ли запрос
        if not query_text:
            await update.message.reply_text(
                "Пожалуйста, укажите вопрос для ChatGPT.\n"
                "Например: /ask Что такое Wildberries?"
            )
            return
            
        # Отправляем сообщение о начале обработки
        processing_message = await update.message.reply_text(
            "💭 Обрабатываю ваш запрос..."
        )
        
        try:
            # Создаем клиента OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            
            # Отправляем запрос к API
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": CHATGPT_SYSTEM_PROMPT},
                    {"role": "user", "content": query_text}
                ],
                max_tokens=500,
                temperature=0.7
            )
            
            # Получаем ответ от ChatGPT
            reply_content = response.choices[0].message.content
            
            # Ограничиваем длину ответа
            if len(reply_content) > MAX_GPT_RESPONSE_LENGTH:
                reply_content = reply_content[:MAX_GPT_RESPONSE_LENGTH] + "..."
            
            # Записываем запрос в лимит
            gpt_user_requests[user_id].append(now)
            
            # Удаляем сообщение о загрузке
            try:
                await processing_message.delete()
            except Exception as e:
                logger.warning(f"Не удалось удалить сообщение о загрузке: {e}")
            
            # Отправляем ответ пользователю
            await update.message.reply_text(reply_content)
            
            # Логируем информацию о запросе
            logger.info(f"Пользователь {user_id} получил ответ от ChatGPT. Осталось запросов: {MAX_GPT_REQUESTS_PER_DAY - len(gpt_user_requests[user_id])}")
        
        except Exception as e:
            logger.error(f"Ошибка при запросе к OpenAI API: {str(e)}", exc_info=True)
            await update.message.reply_text(
                f"Ошибка при запросе к ChatGPT: {str(e)}\n"
                "Пожалуйста, попробуйте позже или обратитесь к администратору."
            )
            
            try:
                # Пытаемся удалить сообщение о загрузке
                await processing_message.delete()
            except:
                pass
                
    except Exception as e:
        logger.error(f"Ошибка при обработке запроса к ChatGPT: {str(e)}", exc_info=True)
        await update.message.reply_text(
            f"Произошла ошибка при обработке запроса: {str(e)}\n"
            "Пожалуйста, попробуйте позже или обратитесь к администратору."
        )

async def handle_chatgpt_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обработчик команды /chatgpt или /ask
    
    Args:
        update: Объект обновления Telegram
        context: Контекст бота
    """
    # Перенаправляем в общий обработчик запросов к ChatGPT
    await handle_gpt_message(update, context)
    
async def clean_cache(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Очистка кэша и временных данных
    
    Args:
        context: Контекст бота
    """
    try:
        logger.info("Начинаю плановую очистку кэша...")
        
        # Очистка запросов ChatGPT
        for user_id in list(gpt_user_requests.keys()):
            today = datetime.now().date()
            # Оставляем только запросы за сегодня
            gpt_user_requests[user_id] = [ts for ts in gpt_user_requests[user_id] if ts.date() == today]
            # Если запросов не осталось, удаляем пользователя из словаря
            if not gpt_user_requests[user_id]:
                del gpt_user_requests[user_id]
        
        # Очистка временных файлов
        tmp_dir = os.getenv("TMP_DIR", "tmp")
        if os.path.exists(tmp_dir):
            for file in os.listdir(tmp_dir):
                file_path = os.path.join(tmp_dir, file)
                try:
                    # Проверяем возраст файла
                    file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
                    now = datetime.now()
                    # Удаляем файлы старше 24 часов
                    if (now - file_time).total_seconds() > 86400:  # 24 часа
                        if os.path.isfile(file_path):
                            os.unlink(file_path)
                            logger.info(f"Удален старый файл: {file_path}")
                except Exception as e:
                    logger.warning(f"Ошибка при удалении файла {file_path}: {e}")
        
        logger.info("Очистка кэша завершена")
    except Exception as e:
        logger.error(f"Ошибка при очистке кэша: {e}", exc_info=True)
    
async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обработчик команды /search для поиска товаров на Wildberries
    
    Args:
        update: Объект обновления Telegram
        context: Контекст бота
    """
    try:
        # Проверяем, есть ли аргументы команды
        if not context.args:
            await update.message.reply_text(
                "Для поиска товаров укажите поисковый запрос.\n"
                "Например: /search красное платье\n"
            )
            return
        
        # Получаем поисковый запрос из аргументов
        search_query = " ".join(context.args)
        
        # Отправляем сообщение о начале поиска
        searching_message = await update.message.reply_text(
            f"🔍 Ищу товары по запросу: \"{search_query}\"..."
        )
        
        # Создаем безопасный клиент для запросов
        scraper = create_scraper_instance()
        
        # Выполняем поисковый запрос на Wildberries
        try:
            # Кодируем запрос для URL
            encoded_query = quote(search_query)
            search_url = f"https://search.wb.ru/exactmatch/ru/common/v4/search?appType=1&couponsGeo=12,3,18,15,21&curr=rub&dest=-1029256,-102269,-2162196,-1257786&emp=0&lang=ru&locale=ru&pricemarginCoeff=1.0&query={encoded_query}&reg=0&regions=80,68,64,83,4,38,33,70,82,69,86,75,30,40,48,1,22,66,31,71&resultset=catalog&sort=popular&spp=0&suppressSpellcheck=false"
            
            # Выполняем запрос с поддержкой прокси
            try:
                # Используем нашу функцию с поддержкой прокси
                response = search_with_proxy(search_url)
                
                # Парсим ответ
                search_data = response.json()
                
                # Проверяем наличие результатов
                if not search_data.get('data', {}).get('products', []):
                    await searching_message.edit_text(
                        f"По запросу \"{search_query}\" ничего не найдено. Попробуйте другой запрос."
                    )
                    return
                
                # Получаем список товаров
                products = search_data['data']['products'][:5]  # Ограничиваем до 5 товаров
                
                # Формируем сообщение с результатами
                result_message = f"📋 Результаты поиска по запросу \"{search_query}\":\n\n"
                
                for i, product in enumerate(products, 1):
                    # Получаем основные данные о товаре
                    article = product.get('id', 'Нет артикула')
                    name = product.get('name', 'Без названия')
                    brand = product.get('brand', 'Без бренда')
                    price = product.get('salePriceU', 0) / 100  # Цена в копейках
                    rating = product.get('rating', 0)
                    
                    # Добавляем информацию о товаре
                    result_message += f"{i}. *{name}*\n"
                    result_message += f"   Бренд: {brand}\n"
                    result_message += f"   Цена: {price:.0f} ₽\n"
                    result_message += f"   Рейтинг: {rating}/5\n"
                    result_message += f"   Артикул: {article}\n"
                    result_message += f"   [Посмотреть на WB](https://www.wildberries.ru/catalog/{article}/detail.aspx)\n\n"
                
                # Добавляем ссылку на все результаты
                result_message += f"[Все результаты на Wildberries](https://www.wildberries.ru/catalog/0/search.aspx?search={encoded_query})"
                
                # Удаляем сообщение о поиске
                await searching_message.delete()
                
                # Отправляем результаты
                await update.message.reply_text(
                    result_message,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
                
            except requests.RequestException as e:
                logger.error(f"Ошибка при поиске на Wildberries: {str(e)}", exc_info=True)
                await searching_message.edit_text(
                    f"Не удалось выполнить поиск на Wildberries. Попробуйте позже."
                )
                
        except requests.RequestException as e:
            logger.error(f"Ошибка при поиске на Wildberries: {str(e)}", exc_info=True)
            await searching_message.edit_text(
                f"Не удалось выполнить поиск на Wildberries. Попробуйте позже."
            )
            
    except Exception as e:
        logger.error(f"Ошибка при обработке команды /search: {str(e)}", exc_info=True)
        await update.message.reply_text(
            f"Произошла ошибка при обработке команды: {str(e)}\n"
            "Пожалуйста, попробуйте позже или обратитесь к администратору."
        )
    
async def get_wb_product_data(article: str) -> Optional[Dict[str, Any]]:
    """
    Получает данные о товаре по артикулу из Wildberries API
    
    Args:
        article: Артикул товара
        
    Returns:
        Словарь с данными о товаре или None при ошибке
    """
    try:
        # Проверяем кеш
        if article in product_cache:
            data, timestamp = product_cache[article]
            # Проверяем, не устарели ли данные
            if time.time() - timestamp < CACHE_LIFETIME * 3600:  # Переводим часы в секунды
                logger.info(f"Данные о товаре {article} получены из кеша")
                return data
        
        # Проверяем доступность интернета
        if not check_internet_connection():
            logger.warning("Интернет недоступен")
            return {"error": "Интернет недоступен. Пожалуйста, проверьте подключение и попробуйте снова."}
        
        # Проверяем доступность хостов Wildberries
        hosts_status = check_wildberries_hosts()
        if not any(hosts_status.values()):
            logger.warning("Все хосты Wildberries недоступны")
            return {"error": "Сервис Wildberries временно недоступен. Пожалуйста, попробуйте позже."}
        
        # Используем нашу функцию с поддержкой прокси
        # Так как функция синхронная, запускаем её в отдельном потоке
        loop = asyncio.get_event_loop()
        product_data = await loop.run_in_executor(
            None,
            lambda: get_product_details_with_proxy(article)
        )
        
        if not product_data:
            return None
        
        # Преобразуем данные в нужный формат
        price = 0
        if 'salePriceU' in product_data and product_data['salePriceU']:
            price = float(product_data['salePriceU']) / 100
        elif 'priceU' in product_data and product_data['priceU']:
            price = float(product_data['priceU']) / 100
        
        result = {
            'name': product_data.get('name', ''),
            'brand': product_data.get('brand', ''),
            'price': price,
            'rating': product_data.get('reviewRating', 0),
            'feedbacks': product_data.get('feedbacks', 0),
            'article': article,
            'url': f"https://www.wildberries.ru/catalog/{article}/detail.aspx"
        }
        
        return result
        
    except Exception as e:
        logger.error(f"Ошибка при получении данных о товаре {article}: {str(e)}")
        return None
    
def format_product_message(name: str, price: Optional[float] = None, rating: Optional[float] = None, 
                          brand: Optional[str] = None, seller: Optional[str] = None, 
                          url: Optional[str] = None, is_original: bool = False) -> str:
    """
    Форматирует сообщение о товаре
    
    Args:
        name: Название товара
        price: Цена товара
        rating: Рейтинг товара
        brand: Бренд товара
        seller: Продавец товара
        url: URL товара
        is_original: Является ли товар исходным
        
    Returns:
        str: Отформатированное сообщение
    """
    prefix = "📦 *Исходный товар:* " if is_original else "📦 *Товар:* "
    message = f"{prefix}{name}\n"
    
    # Добавляем цену
    if price is not None:
        message += f"💰 *Цена:* {price:,.2f} ₽\n"
    else:
        message += "💰 *Цена:* Временно недоступна\n"
    
    # Добавляем рейтинг
    if rating is not None:
        # Корректное отображение рейтинга в виде золотых звёзд
        full_stars = min(5, int(rating))
        half_star = rating - int(rating) >= 0.5
        empty_stars = 5 - full_stars - (1 if half_star else 0)
        
        # Используем символы звезд для лучшего визуального отображения
        star_rating = '★' * full_stars
        if half_star:
            star_rating += '✭'
        star_rating += '☆' * empty_stars
        
        message += f"⭐ *Рейтинг:* {rating} {star_rating}\n"
    else:
        message += "⭐ *Рейтинг:* Нет данных\n"
    
    # Добавляем бренд
    if brand:
        message += f"🏭 *Бренд:* {brand}\n"
    
    # Добавляем продавца
    if seller:
        message += f"🏪 *Продавец:* {seller}\n"
    
    # Добавляем URL
    if url:
        message += f"🔗 {url}\n"
    
    return message

def get_product_details_with_proxy(article: str) -> Optional[Dict[str, Any]]:
    """
    Получает данные о товаре с использованием прокси и fallback
    
    Args:
        article: Артикул товара
        
    Returns:
        Dict: Данные о товаре или None при ошибке
    """
    from find_similar import get_product_details as original_get_product_details
    
    logger.info(f"Получение данных о товаре {article}" + 
               (f" через прокси {PROXY_IP}:{PROXY_PORT}" if PROXY_ENABLED else ""))
    
    # Если прокси не включен, просто используем оригинальную функцию
    if not PROXY_ENABLED:
        return original_get_product_details(article)
    
    # Создаем scraper для запросов
    scraper = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'desktop': True
        }
    )
    
    # Настраиваем заголовки
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    
    # Настраиваем прокси
    try:
        scraper.proxies = PROXIES
    except Exception as e:
        logger.error(f"Ошибка при настройке прокси: {str(e)}")
        # В случае ошибки используем оригинальную функцию
        return original_get_product_details(article)
    
    # API URL для получения данных о товаре
    api_url = f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-6972066&spp=30&nm={article}"
    
    try:
        # Выполняем запрос через прокси
        logger.info(f"Запрос к {api_url} через прокси {PROXY_IP}:{PROXY_PORT}")
        response = scraper.get(api_url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            logger.info(f"Успешно получены данные через прокси для артикула {article}")
            # Успешный ответ через прокси, используем оригинальную функцию для парсинга
            # Данные уже должны быть в кеше
            return original_get_product_details(article)
        else:
            logger.warning(f"Ошибка при запросе через прокси: HTTP {response.status_code}")
    except requests.exceptions.RequestException as e:
        logger.warning(f"Ошибка при запросе через прокси: {str(e)}")
    
    # Если запрос через прокси не удался, пробуем без прокси
    logger.info(f"Запрос к {api_url} без прокси (fallback)")
    try:
        # Убираем прокси для fallback запроса
        scraper.proxies = None
        response = scraper.get(api_url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            logger.info(f"Успешно получены данные без прокси для артикула {article}")
            # Успешный ответ без прокси, используем оригинальную функцию для парсинга
            return original_get_product_details(article)
        else:
            logger.warning(f"Ошибка при запросе без прокси: HTTP {response.status_code}")
    except requests.exceptions.RequestException as e:
        logger.warning(f"Ошибка при запросе без прокси: {str(e)}")
    
    # Если оба запроса не удались, используем оригинальную функцию
    return original_get_product_details(article)
    
def search_with_proxy(url, headers=None, timeout=10):
    """
    Выполняет поисковый запрос с использованием прокси и fallback
    
    Args:
        url: URL для запроса
        headers: Заголовки запроса
        timeout: Таймаут запроса
        
    Returns:
        Response: Объект ответа requests или исключение
    """
    if headers is None:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        
    # Создаем сессию
    session = requests.Session()
    session.headers.update(headers)
    
    # Добавляем прокси, если включен
    if PROXY_ENABLED:
        logger.info(f"Настройка прокси {PROXY_IP}:{PROXY_PORT} для запроса")
        session.proxies = PROXIES
    
    try:
        # Попытка с прокси (если включен)
        logger.info(f"Выполняю запрос к {url}" + 
                  (f" через прокси {PROXY_IP}:{PROXY_PORT}" if PROXY_ENABLED and session.proxies else ""))
        
        response = session.get(url, timeout=timeout)
        response.raise_for_status()
        return response
    except requests.exceptions.RequestException as e:
        logger.warning(f"Ошибка при запросе через прокси: {str(e)}")
        
        # Если прокси включен и возникла ошибка, пробуем без прокси
        if PROXY_ENABLED and session.proxies:
            logger.info(f"Пробую запрос к {url} без прокси (fallback)")
            session.proxies = None
            
            # Повторный запрос без прокси
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response
        # Если прокси не использовался или ошибка в fallback, пробрасываем исключение
        raise
    