import logging
import json
import re
import asyncio
from typing import Dict, Any, Optional, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

# Импортируем функцию поиска похожих товаров из отдельного модуля
from similar_products import find_similar_cheaper_products

# Настройка логирования
logger = logging.getLogger(__name__)

# Загрузка настроек
try:
    import os
    from dotenv import load_dotenv
    load_dotenv()
    PARTNER_ID = os.getenv("PARTNER_ID", "wildberries")  # Партнерский ID
except ImportError:
    PARTNER_ID = "wildberries"  # Значение по умолчанию

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
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Используем функцию из модуля similar_products для поиска
        similar_product = await find_similar_cheaper_products(
            article=article,
            min_rating=4.5,  # Минимальный рейтинг
            min_feedbacks=20  # Минимальное количество отзывов
        )
        
        # Проверяем, найден ли подходящий товар
        if not similar_product:
            await query.edit_message_text(
                "Похожие товары не найдены по заданным критериям (рейтинг ≥ 4.5, отзывы ≥ 20).",
                parse_mode=ParseMode.MARKDOWN
            )
            return
            
        # Формируем сообщение с результатом
        message_text = f"📦 *Похожий товар:* {similar_product.get('name')}\n"
        message_text += f"💰 *Цена:* {similar_product.get('price')} ₽\n"
        message_text += f"⭐️ *Рейтинг:* {similar_product.get('rating')}\n"
        message_text += f"💬 *Отзывы:* {similar_product.get('feedbacks')}\n"
        message_text += f"🔗 [Ссылка на товар]({similar_product.get('url')})"
        
        # Отправляем сообщение с результатом
        await query.edit_message_text(
            message_text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Ошибка при поиске похожих товаров дешевле: {str(e)}", exc_info=True)
        await query.edit_message_text(
            f"Произошла ошибка при поиске похожих товаров: {str(e)}\n"
            f"Пожалуйста, попробуйте снова или обратитесь к администратору.",
            parse_mode=ParseMode.MARKDOWN
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
        # Отправляем сообщение о начале обработки
        loading_message = await update.message.reply_text(
            f"🔍 Ищу информацию о товаре {article}..."
        )
        
        # Получаем данные о товаре - функция get_product_data должна быть импортирована из wb_bot.py
        from wb_bot import get_product_data
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
                
                if price:
                    # Форматируем цену с точкой как разделителем тысяч
                    if price == int(price):
                        price_int = int(price)
                        if price_int >= 1000:
                            price_str = f"{price_int // 1000}.{price_int % 1000:03d}"
                        else:
                            price_str = str(price_int)
                        message += f"💰 *Цена:* {price_str} ₽\n"
                    else:
                        # Для цены с копейками
                        price_int = int(price)
                        price_decimal = int((price - price_int) * 100)
                        if price_int >= 1000:
                            price_str = f"{price_int // 1000}.{price_int % 1000:03d}"
                            if price_decimal > 0:
                                price_str += f",{price_decimal:02d}"
                        else:
                            if price_decimal > 0:
                                price_str = f"{price_int},{price_decimal:02d}"
                            else:
                                price_str = str(price_int)
                        message += f"💰 *Цена:* {price_str} ₽\n"
                
                # Добавляем детали, если они есть
                if details_json:
                    try:
                        details = json.loads(details_json)
                        
                        if 'rating' in details and details['rating']:
                            rating = details['rating']
                            rating_str = f"{rating:.1f}"
                            
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
                            
                            message += f"⭐ *Рейтинг:* {rating_str} {star_rating}\n"
                            
                        if 'brand' in details and details['brand']:
                            message += f"🏭 *Бренд:* {details['brand']}\n"
                            
                        if 'seller' in details and details['seller']:
                            message += f"🏪 *Продавец:* {details['seller']}\n"
                            
                        if 'feedbacks' in details or 'reviews_count' in details:
                            reviews_count = details.get('feedbacks', details.get('reviews_count', 0))
                            message += f"💬 *Отзывы:* {reviews_count}\n"
                        
                    except json.JSONDecodeError:
                        logger.warning(f"Не удалось декодировать JSON с деталями: {details_json}")
                
                # Добавляем партнерскую ссылку
                partner_link = f"https://www.wildberries.ru/catalog/{article}/detail.aspx?target=partner&partner={PARTNER_ID}"
                message += f"🔗 [Ссылка на товар]({partner_link})"
                
                # Создаем клавиатуру с кнопкой для поиска похожих товаров дешевле
                keyboard = [
                    [InlineKeyboardButton("Найти похожие товары дешевле", callback_data=f"similar_cheaper_{article}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                # Отправляем сообщение с информацией о товаре и кнопкой
                await update.message.reply_text(
                    message, 
                    parse_mode=ParseMode.MARKDOWN, 
                    reply_markup=reply_markup
                )
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

def extract_article_from_url(url: str) -> Optional[str]:
    """
    Извлекает артикул товара из URL Wildberries
    
    Args:
        url: URL страницы товара
        
    Returns:
        str: Артикул товара или None, если не удалось извлечь
    """
    patterns = [
        r'wildberries\.ru/catalog/(\d+)/',  # Обычный URL товара
        r'wb\.ru/catalog/(\d+)/',           # Сокращенный URL
        r'wildberries\.ru/product\?card=(\d+)',  # URL товара в корзине
        r'card=(\d+)'                       # URL с card parameter
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    return None 