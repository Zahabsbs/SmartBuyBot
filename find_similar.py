#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import time
import logging
import argparse
import requests
import json
import re
from typing import List, Dict, Any, Optional
import asyncio

# Настраиваем логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("find_similar")

def get_product_details(article: str) -> Optional[Dict[str, Any]]:
    """
    Получает информацию о товаре по артикулу из Wildberries API
    
    Args:
        article: Артикул товара
        
    Returns:
        Словарь с информацией о товаре или None при ошибке
    """
    # Импортируем переменные настроек прокси
    try:
        from wb_bot import PROXY_ENABLED, PROXIES, PROXY_IP, PROXY_PORT
        proxy_enabled = PROXY_ENABLED
        proxies = PROXIES
    except ImportError:
        # Если не удалось импортировать из wb_bot, используем локальные настройки
        proxy_enabled = False
        proxies = None
    
    # Формируем URL для запроса деталей о товаре
    url = f"https://card.wb.ru/cards/detail?spp=30&regions=68,83,4,38,80,33,70,82,86,30,69,22,66,31,40,1,48&dest=-1257786&nm={article}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Origin": "https://www.wildberries.ru",
        "Referer": "https://www.wildberries.ru/",
    }
    
    try:
        logger.info(f"Получаем данные о товаре {article}" + 
                   (f" через прокси {PROXY_IP}:{PROXY_PORT}" if proxy_enabled else ""))
        
        # Пробуем с прокси, если он включен
        if proxy_enabled:
            try:
                response = requests.get(url, headers=headers, proxies=proxies, timeout=10)
                response.raise_for_status()
            except requests.RequestException as e:
                logger.warning(f"Ошибка при запросе через прокси: {str(e)}. Пробуем без прокси.")
                # Fallback на запрос без прокси при ошибке
                response = requests.get(url, headers=headers, timeout=10)
                response.raise_for_status()
        else:
            # Запрос без прокси
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
        
        data = response.json()
        
        # Проверяем наличие данных о товаре
        if 'data' in data and 'products' in data['data'] and data['data']['products']:
            product = data['data']['products'][0]
            logger.info(f"Получены данные о товаре {article}: {product.get('name', 'Неизвестное название')}")
            return product
        else:
            logger.warning(f"Не найдены данные для товара с артикулом {article}")
            return None
    except requests.RequestException as e:
        logger.error(f"Ошибка сети при получении данных о товаре {article}: {str(e)}")
        return None
    except (KeyError, IndexError, ValueError, json.JSONDecodeError) as e:
        logger.error(f"Ошибка при обработке данных о товаре {article}: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при получении данных о товаре {article}: {str(e)}")
        return None

def extract_category_and_keywords(name: str) -> tuple:
    """
    Извлекает категорию товара и ключевые слова из названия
    
    Args:
        name: Название товара
        
    Returns:
        Кортеж (категория, ключевые слова)
    """
    if not name:
        return "", []
    
    # Очищаем название от специфических деталей и цифровых значений
    clean_name = re.sub(r'\d+\s*шт|\d+х\d+|\d+\s*вт|\d+\s*мм|\d+\s*см|\d+\s*м|подсветка|питание usb|на заказ|набор|\(.*?\)|,.*', '', name, flags=re.IGNORECASE)
    
    # Удаляем избыточные пробелы
    clean_name = re.sub(r'\s+', ' ', clean_name).strip()
    
    # Разбиваем на слова и фильтруем короткие и незначимые
    stop_words = {'для', 'или', 'как', 'что', 'при', 'под', 'над', 'по', 'из', 'без', 'за', 'с', 'на', 'во', 'от', 'до', 'к', 'про', 'же'}
    words = [word.strip() for word in clean_name.split() if len(word.strip()) > 2 and word.lower() not in stop_words]
    
    if not words:
        return "", []
    
    # Определяем категорию (обычно первые 2-3 слова)
    # Пытаемся найти устойчивые словосочетания, характерные для категорий товаров
    category_patterns = [
        (r'колонк[иа]\s+для\s+компьютера', 'колонки для компьютера'),
        (r'акустическ[ая][яиую]\s+систем[аы]', 'акустическая система'),
        (r'портативн[ая][яиую]\s+колонк[аи]', 'портативная колонка'),
        (r'компьютерн[ая][яиую]\s+акустик[аи]', 'компьютерная акустика')
    ]
    
    category = ""
    for pattern, replacement in category_patterns:
        if re.search(pattern, clean_name, flags=re.IGNORECASE):
            category = replacement
            break
    
    # Если не нашли по шаблонам, используем первые слова
    if not category:
        category_words = words[:3] if len(words) >= 3 else words
        category = " ".join(category_words)
    
    # Выделяем ключевые слова с весами значимости
    keywords = []
    important_features = ['bluetooth', 'беспроводн', 'стерео', 'сабвуфер', 'портативн', 'игров']
    
    for word in words:
        word_lower = word.lower()
        # Добавляем слово с весом, если оно характеризует особенности товара
        if any(feature in word_lower for feature in important_features):
            keywords.insert(0, word)  # Добавляем в начало как более важное
        else:
            keywords.append(word)
    
    # Убираем дубликаты, сохраняя порядок
    keywords = list(dict.fromkeys(keywords))
    
    return category, keywords

def get_similar_products(article: str, limit: int = 30) -> List[Dict[str, Any]]:
    """
    Получает список похожих товаров по артикулу с улучшенным алгоритмом поиска
    
    Args:
        article: Артикул товара
        limit: Максимальное количество товаров
        
    Returns:
        Список словарей с данными о похожих товарах
    """
    # Получаем данные о товаре
    product_data = get_product_details(article)
    if not product_data:
        logger.warning(f"Не удалось получить данные о товаре {article}")
        return []
    
    try:
        # Получаем ключевые параметры товара
        brand = product_data.get('brand', '')
        name = product_data.get('name', '')
        subject_id = product_data.get('subjectId')  # Идентификатор категории товара
        
        if not name:
            logger.warning(f"Не найдено название товара для артикула {article}")
            return []
            
        logger.info(f"Получаем похожие товары для: {brand} - {name}")
        
        # Получаем категорию и ключевые слова из названия
        category, keywords = extract_category_and_keywords(name)
        
        # Составляем поисковые запросы по убыванию специфичности
        search_queries = []
        
        # 1. Самый специфичный запрос: бренд + категория + первое ключевое слово
        if brand and category and keywords:
            search_queries.append(f"{brand} {category} {keywords[0]}")
        
        # 2. Бренд + категория
        if brand and category:
            search_queries.append(f"{brand} {category}")
        
        # 3. Категория + основные ключевые слова (без бренда)
        if category and keywords:
            main_keywords = " ".join(keywords[:3]) if len(keywords) >= 3 else " ".join(keywords)
            search_queries.append(f"{category} {main_keywords}")
        
        # 4. Запрос с конкретными техническими характеристиками (если они есть в названии)
        specs_match = re.search(r'(\d+(?:\.\d+)?\s*(?:вт|w|ватт|мм|м|см)|\d+\.\d+|\d+x\d+)', name, flags=re.IGNORECASE)
        if specs_match and category:
            specs = specs_match.group(1)
            search_queries.append(f"{category} {specs}")
        
        # 5. Только бренд (запасной вариант)
        if brand:
            search_queries.append(brand)
        
        # Удаляем повторяющиеся запросы, оставляя уникальные
        search_queries = list(dict.fromkeys(search_queries))
        
        # URL для поиска товаров
        search_url = "https://search.wb.ru/exactmatch/ru/common/v4/search"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Origin": "https://www.wildberries.ru",
            "Referer": "https://www.wildberries.ru/",
        }
        
        # Получаем настройки прокси из wb_bot.py
        try:
            from wb_bot import PROXY_ENABLED, PROXIES, PROXY_IP, PROXY_PORT
            proxy_enabled = PROXY_ENABLED
            proxies = PROXIES
        except ImportError:
            proxy_enabled = False
            proxies = None
        
        all_results = []
        result_ids = set()  # Для отслеживания уже найденных товаров
        
        # Перебираем все поисковые запросы, пока не найдем достаточное количество товаров
        for query_idx, search_query in enumerate(search_queries):
            if len(all_results) >= limit:
                break
                
            logger.info(f"Поисковый запрос #{query_idx+1}: '{search_query}'")
            
            params = {
                "appType": "1",
                "curr": "rub",
                "dest": "-1257786",
                "query": search_query,
                "resultset": "catalog",
                "sort": "popular",
                "spp": "0",
                "suppressSpellcheck": "false"
            }
            
            # Добавляем параметр subjectId, если он есть
            if subject_id:
                params["subject"] = str(subject_id)
            
            try:
                # Выполняем запрос с поддержкой прокси
                if proxy_enabled:
                    try:
                        logger.info(f"Выполняем запрос с прокси {PROXY_IP}:{PROXY_PORT}")
                        response = requests.get(search_url, params=params, headers=headers, proxies=proxies, timeout=10)
                        response.raise_for_status()
                    except requests.RequestException as e:
                        logger.warning(f"Ошибка при запросе через прокси: {str(e)}. Пробуем без прокси.")
                        # Fallback на запрос без прокси
                        response = requests.get(search_url, params=params, headers=headers, timeout=10)
                        response.raise_for_status()
                else:
                    # Запрос без прокси
                    response = requests.get(search_url, params=params, headers=headers, timeout=10)
                    response.raise_for_status()
                
                data = response.json()
                
                # Проверяем наличие товаров в ответе
                if 'data' in data and 'products' in data['data']:
                    products = data['data']['products']
                    
                    # Подсчитываем количество новых товаров (не включая текущий артикул)
                    new_products = [p for p in products if str(p.get('id')) != str(article) and p.get('id') not in result_ids]
                    logger.info(f"Найдено {len(new_products)} новых товаров по запросу '{search_query}'")
                    
                    # Обрабатываем каждый товар
                    for product in new_products:
                        # Пропускаем товары, которые уже добавлены
                        product_id = product.get('id')
                        if product_id in result_ids:
                            continue
                        
                        # Проверяем наличие цены
                        price = 0
                        if 'salePriceU' in product and product['salePriceU']:
                            price = float(product['salePriceU']) / 100
                        elif 'priceU' in product and product['priceU']:
                            price = float(product['priceU']) / 100
                        
                        # Пропускаем товары с нулевой или нереалистичной ценой
                        if price <= 10:
                            continue
                        
                        # Проверяем релевантность товара
                        product_name = product.get('name', '').lower()
                        product_brand = product.get('brand', '').lower()
                        original_brand = brand.lower()
                        original_name = name.lower()
                        
                        # Определяем релевантность товара
                        relevance_score = 0
                        
                        # 1. Совпадение бренда = +3 балла
                        if original_brand and product_brand and original_brand in product_brand:
                            relevance_score += 3
                        
                        # 2. Совпадение категории = +2 балла
                        if category and category.lower() in product_name:
                            relevance_score += 2
                        
                        # 3. Совпадение ключевых слов = +1 балл за каждое
                        for keyword in keywords:
                            if keyword.lower() in product_name:
                                relevance_score += 1
                                
                        # 4. Штраф за сильное несоответствие в размерах/характеристиках
                        original_specs = re.findall(r'(\d+(?:\.\d+)?\s*(?:вт|w|ватт)|\d+x\d+)', original_name, flags=re.IGNORECASE)
                        product_specs = re.findall(r'(\d+(?:\.\d+)?\s*(?:вт|w|ватт)|\d+x\d+)', product_name, flags=re.IGNORECASE)
                        
                        # Если у обоих товаров есть спецификации, но они сильно отличаются
                        if original_specs and product_specs and original_specs[0] != product_specs[0]:
                            # Извлекаем числовые значения
                            try:
                                orig_value = float(re.search(r'\d+(?:\.\d+)?', original_specs[0]).group())
                                prod_value = float(re.search(r'\d+(?:\.\d+)?', product_specs[0]).group())
                                
                                # Если разница больше 50%
                                if orig_value > 0 and (abs(orig_value - prod_value) / orig_value) > 0.5:
                                    relevance_score -= 2
                            except (AttributeError, ValueError):
                                pass
                        
                        # Добавляем только товары с минимальной релевантностью
                        min_relevance = 3 if query_idx == 0 else 2
                        if relevance_score >= min_relevance:
                            # Добавляем товар в результаты
                            result_ids.add(product_id)
                            
                            # Извлекаем рейтинг и отзывы
                            rating = product.get('reviewRating', 0)
                            if not rating and 'rating' in product:
                                rating = product.get('rating', 0)
                            
                            feedbacks = product.get('feedbacks', 0)
                            
                            # Создаем объект товара
                            result_item = {
                                'id': product_id,
                                'name': product.get('name', ''),
                                'brand': product.get('brand', ''),
                                'price': price,
                                'rating': rating,
                                'feedbacks': feedbacks,
                                'relevance': relevance_score,
                                'url': f"https://www.wildberries.ru/catalog/{product_id}/detail.aspx"
                            }
                            
                            all_results.append(result_item)
                            
                            # Ограничиваем количество результатов
                            if len(all_results) >= limit:
                                break
            
            except requests.RequestException as e:
                logger.warning(f"Ошибка сети при выполнении поискового запроса '{search_query}': {str(e)}")
                continue
            except (KeyError, IndexError, ValueError, json.JSONDecodeError) as e:
                logger.warning(f"Ошибка при обработке результатов поиска для запроса '{search_query}': {str(e)}")
                continue
            except Exception as e:
                logger.warning(f"Непредвиденная ошибка при обработке запроса '{search_query}': {str(e)}")
                continue
        
        # Сортируем результаты по релевантности (в порядке убывания)
        sorted_results = sorted(all_results, key=lambda x: x.get('relevance', 0), reverse=True)
        
        if sorted_results:
            logger.info(f"Всего найдено {len(sorted_results)} релевантных товаров для артикула {article}")
            return sorted_results
        else:
            logger.warning(f"Не найдено релевантных товаров для артикула {article}")
            return []
        
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при получении похожих товаров для {article}: {str(e)}")
        return []

async def find_similar_products(article: str, limit: int = 30, max_price=None, min_rating=None) -> List[Dict[str, Any]]:
    """
    Асинхронная обертка для функции get_similar_products.
    Используется для вызова синхронной функции из асинхронного кода.
    
    Args:
        article: Артикул товара
        limit: Максимальное количество товаров
        max_price: Максимальная цена товара (если указано)
        min_rating: Минимальный рейтинг товара (если указано)
        
    Returns:
        Список словарей с данными о похожих товарах
    """
    # Вызываем синхронную функцию через run_in_executor
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: get_similar_products(article, limit))
    
    # Если заданы ограничения по цене или рейтингу, фильтруем результаты
    if max_price is not None or min_rating is not None:
        filtered_results = []
        for product in result:
            price = product.get('price', 0)
            rating = product.get('rating', 0)
            
            if (max_price is None or price <= max_price) and (min_rating is None or rating >= min_rating):
                filtered_results.append(product)
        
        return filtered_results
    
    return result

def find_similar_cheaper_products(article: str, max_price_percent: int = 100, min_rating: float = 4.0, min_feedbacks: int = 10) -> Optional[Dict[str, Any]]:
    """
    Находит похожие товары с ценой не выше указанного процента от цены исходного товара
    
    Args:
        article: Артикул товара
        max_price_percent: Максимальный процент от исходной цены (100% означает не дороже исходного товара)
        min_rating: Минимальный рейтинг товара
        min_feedbacks: Минимальное количество отзывов
        
    Returns:
        Самый дешевый похожий товар, удовлетворяющий условиям, или None
    """
    # Получаем данные о товаре
    product_data = get_product_details(article)
    if not product_data:
        logger.warning(f"Не удалось получить данные о товаре {article}")
        return None
    
    try:
        # Проверяем цену товара
        price = 0
        if 'salePriceU' in product_data and product_data['salePriceU']:
            price = float(product_data['salePriceU']) / 100
        elif 'priceU' in product_data and product_data['priceU']:
            price = float(product_data['priceU']) / 100
            
        if price <= 0:
            logger.warning(f"Некорректная цена товара {article}: {price}")
            return None
            
        logger.info(f"Найден товар {article}: цена {price:.2f} ₽")
        
        # Получаем похожие товары с увеличенным лимитом для лучшего выбора
        similar_products = get_similar_products(article, limit=100)  
        
        if not similar_products:
            logger.warning(f"Не найдены похожие товары для {article}")
            return None
            
        logger.info(f"Найдено {len(similar_products)} похожих товаров")
        
        # Фильтруем товары по цене, рейтингу и количеству отзывов
        max_price = price * max_price_percent / 100
        
        # Сортируем по релевантности и цене
        filtered_products = []
        highly_relevant = []
        medium_relevant = []
        
        for product in similar_products:
            # Пропускаем товар с тем же артикулом
            if product["id"] == int(article):
                continue
                
            # Проверяем цену
            if product["price"] > max_price:
                continue
                
            # Проверяем минимальный рейтинг и количество отзывов
            product_rating = product.get("rating", 0) or 0
            product_feedbacks = product.get("feedbacks", 0) or 0
            
            if product_rating < min_rating or product_feedbacks < min_feedbacks:
                continue
                
            # Определяем уровень релевантности товара
            relevance = product.get("relevance", 0)
            
            if relevance >= 5:  # Высокая релевантность
                highly_relevant.append(product)
            elif relevance >= 3:  # Средняя релевантность
                medium_relevant.append(product)
            else:  # Низкая релевантность - пропускаем
                continue
        
        # Сортируем товары внутри каждой группы по цене
        highly_relevant.sort(key=lambda p: p["price"])
        medium_relevant.sort(key=lambda p: p["price"])
        
        # Объединяем результаты, сначала высокорелевантные, потом среднерелевантные
        filtered_products = highly_relevant + medium_relevant
        
        # Выводим логи для диагностики
        logger.info(f"После фильтрации (макс. цена {max_price:.2f} ₽, мин. рейтинг {min_rating}, мин. отзывов {min_feedbacks}) осталось {len(filtered_products)} товаров")
        logger.info(f"Высокорелевантных: {len(highly_relevant)}, среднерелевантных: {len(medium_relevant)}")
        
        # Возвращаем самый дешевый товар или None, если ничего не найдено
        if filtered_products:
            best_product = filtered_products[0]
            discount_percent = int((1 - best_product["price"]/price) * 100)
            logger.info(f"Найден более дешевый товар: {best_product['name']}, цена: {best_product['price']} (дешевле на {discount_percent}%)")
            return best_product
        else:
            logger.info(f"Не найдено похожих товаров, соответствующих критериям")
            return None
    except Exception as e:
        logger.error(f"Ошибка при поиске похожих товаров для {article}: {str(e)}")
        return None

def format_price(price: float) -> str:
    """
    Форматирует цену для вывода (с разделителями тысяч)
    
    Args:
        price: Цена в рублях
        
    Returns:
        Отформатированная строка с ценой
    """
    try:
        if price == int(price):
            # Целая цена
            price_int = int(price)
            return f"{price_int:,}".replace(',', ' ')
        else:
            # Цена с копейками
            return f"{price:,.2f}".replace(',', ' ').replace('.', ',')
    except Exception:
        return str(price)

def parse_arguments():
    """
    Парсит аргументы командной строки
    
    Returns:
        Объект с аргументами командной строки
    """
    parser = argparse.ArgumentParser(description='Поиск похожих товаров дешевле указанного на Wildberries')
    
    parser.add_argument('article', type=str, help='Артикул товара на Wildberries')
    parser.add_argument('-p', '--percent', type=int, default=100, 
                       help='Максимальный процент от исходной цены (100%% означает не дороже исходного товара)')
    parser.add_argument('-r', '--rating', type=float, default=4.0, 
                       help='Минимальный рейтинг товара (от 0 до 5)')
    parser.add_argument('-f', '--feedbacks', type=int, default=10, 
                       help='Минимальное количество отзывов')
    
    return parser.parse_args()

def main():
    """
    Основная функция программы
    """
    # Парсим аргументы командной строки
    args = parse_arguments()
    
    article = args.article
    max_price_percent = args.percent
    min_rating = args.rating
    min_feedbacks = args.feedbacks
    
    print(f"Поиск похожих товаров дешевле {article}...")
    print(f"Параметры: макс. цена {max_price_percent}%, мин. рейтинг {min_rating}, мин. отзывов {min_feedbacks}")
    
    # Получаем информацию о товаре
    start_time = time.time()
    product = get_product_details(article)
    
    if not product:
        print(f"Ошибка: Товар с артикулом {article} не найден")
        return 1
    
    # Выводим информацию о товаре
    price = 0
    if 'salePriceU' in product and product['salePriceU']:
        price = float(product['salePriceU']) / 100
    elif 'priceU' in product and product['priceU']:
        price = float(product['priceU']) / 100
        
    print("\nИнформация о товаре:")
    print(f"  Название: {product.get('name', 'Н/Д')}")
    print(f"  Бренд: {product.get('brand', 'Н/Д')}")
    print(f"  Цена: {format_price(price)} ₽")
    print(f"  Рейтинг: {product.get('reviewRating', 'Н/Д')}")
    print(f"  Отзывы: {product.get('feedbacks', 'Н/Д')}")
    print(f"  URL: https://www.wildberries.ru/catalog/{article}/detail.aspx")
    
    # Ищем похожие товары дешевле
    print("\nПоиск похожих товаров дешевле...")
    
    cheaper = find_similar_cheaper_products(
        article=article,
        max_price_percent=max_price_percent,
        min_rating=min_rating,
        min_feedbacks=min_feedbacks
    )
    
    elapsed_time = time.time() - start_time
    
    if cheaper:
        cheaper_price = cheaper.get('price')
        discount_percent = int((1 - cheaper_price/price) * 100)
        
        print(f"\nНайден более дешевый похожий товар (за {elapsed_time:.2f} сек):")
        print(f"  Название: {cheaper.get('name')}")
        print(f"  Бренд: {cheaper.get('brand')}")
        print(f"  Цена: {format_price(cheaper_price)} ₽ (дешевле на {discount_percent}%)")
        print(f"  Рейтинг: {cheaper.get('rating')}")
        print(f"  Отзывы: {cheaper.get('feedbacks')}")
        print(f"  URL: {cheaper.get('url')}")
        return 0
    else:
        print(f"\nНе найдено похожих товаров дешевле артикула {article} с заданными критериями")
        print(f"Поиск занял {elapsed_time:.2f} сек")
        return 1

if __name__ == "__main__":
    sys.exit(main()) 