import logging
import requests
import json
import re
import random
import time
from typing import List, Dict, Any, Optional, Union

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Константы
MAX_RETRIES = 3
DEFAULT_RESULTS_COUNT = 4

# Список User-Agent для рандомизации
USER_AGENTS = [
    # Chrome на Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # Firefox на Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    # Edge на Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    # Chrome на Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Firefox на Linux
    "Mozilla/5.0 (X11; Linux x86_64; rv:123.0) Gecko/20100101 Firefox/123.0",
    # Safari на macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
]

def get_random_user_agent() -> str:
    """
    Возвращает случайный User-Agent из списка
    
    Returns:
        str: Случайный User-Agent
    """
    return random.choice(USER_AGENTS)

def search_products(query: str, page: int = 1, results_count: int = DEFAULT_RESULTS_COUNT, 
                    use_proxy: bool = False, proxy_list: List[str] = None) -> List[Dict[str, Any]]:
    """
    Поиск товаров на Wildberries
    
    Args:
        query: Поисковый запрос
        page: Номер страницы результатов (начиная с 1)
        results_count: Количество результатов, которые надо вернуть
        use_proxy: Использовать ли прокси для запроса
        proxy_list: Список прокси для использования (если use_proxy=True)
        
    Returns:
        List[Dict]: Список словарей с данными о товарах
    """
    logger.info(f"Поиск товаров по запросу: '{query}', страница {page}")
    
    # Параметры запроса к API
    params = {
        "ab_testing": "false",
        "appType": "1",
        "curr": "rub",
        "dest": "-6972066",
        "hide_dtype": "13",
        "lang": "ru",
        "page": page,
        "query": query,
        "resultset": "catalog",
        "sort": "popular",
        "spp": "30",
        "suppressSpellcheck": "false"
    }
    
    # URL API поиска
    url = "https://search.wb.ru/exactmatch/ru/common/v9/search"
    
    # Заголовки запроса с рандомным User-Agent
    headers = {
        "User-Agent": get_random_user_agent(),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
        "Origin": "https://www.wildberries.ru",
        "Referer": "https://www.wildberries.ru/",
    }
    
    # Список для хранения результатов
    results = []
    
    # Настройка прокси, если требуется
    proxies = None
    if use_proxy and proxy_list and len(proxy_list) > 0:
        proxy = random.choice(proxy_list)
        proxies = {
            "http": proxy,
            "https": proxy
        }
        logger.info(f"Используется прокси: {proxy}")
    
    try:
        # Делаем запрос с повторными попытками
        for attempt in range(MAX_RETRIES):
            try:
                # При каждой попытке обновляем User-Agent
                headers["User-Agent"] = get_random_user_agent()
                
                response = requests.get(
                    url, 
                    params=params, 
                    headers=headers, 
                    timeout=10,
                    proxies=proxies
                )
                
                # Явно устанавливаем кодировку
                response.encoding = 'utf-8'
                
                # Проверяем успешность запроса
                if response.status_code == 200:
                    # Парсим JSON-ответ
                    data = response.json()
                    
                    # Отладочное логирование структуры ответа (только первый товар)
                    if 'data' in data and 'products' in data['data'] and len(data['data']['products']) > 0:
                        first_product = data['data']['products'][0]
                        logger.debug(f"Структура первого товара: {json.dumps(first_product, ensure_ascii=False, indent=2)[:500]}...")
                    
                    # Проверяем наличие товаров в ответе
                    if 'data' in data and 'products' in data['data']:
                        products = data['data']['products']
                        
                        # Обрабатываем каждый товар
                        for product in products[:results_count]:
                            product_id = product.get('id')
                            
                            # Улучшенный алгоритм получения цены с учетом структуры из Parsing.ini
                            price = 0
                            
                            # 1. Проверяем цену внутри sizes->price->product (согласно Parsing.ini)
                            if 'sizes' in product and product['sizes'] and len(product['sizes']) > 0:
                                for size in product['sizes']:
                                    if 'price' in size and 'product' in size['price']:
                                        size_price = size['price']['product']
                                        if isinstance(size_price, (int, float)) and size_price > 0:
                                            # Цена обычно хранится в копейках
                                            price = float(size_price) / 100
                                            break
                            
                            # 2. Если цену не нашли, ищем в других полях
                            if price <= 0:
                                price_fields = [
                                    ('salePriceU', 100),  # Основное поле с ценой со скидкой в копейках
                                    ('priceU', 100),      # Основное поле с ценой в копейках
                                    ('salePrice', 1),     # Цена со скидкой в рублях
                                    ('price', 1)          # Цена в рублях
                                ]
                                
                                # Проверяем основные поля цены
                                for field, divisor in price_fields:
                                    if field in product and product[field] and isinstance(product[field], (int, float)) and product[field] > 0:
                                        price = float(product[field]) / divisor
                                        if price > 10:  # Минимальная проверка на реалистичность цены
                                            break
                                
                                # Проверяем вложенные структуры, если цена еще не найдена или нереалистична
                                if price <= 10:
                                    # Проверяем в extended
                                    if 'extended' in product and product['extended']:
                                        for field, divisor in price_fields:
                                            if field in product['extended'] and product['extended'][field] and isinstance(product['extended'][field], (int, float)) and product['extended'][field] > 0:
                                                price = float(product['extended'][field]) / divisor
                                                if price > 10:
                                                    break
                                    
                                    # Проверяем в sale
                                    if price <= 10 and 'sale' in product and product['sale']:
                                        for field, divisor in price_fields:
                                            if field in product['sale'] and product['sale'][field] and isinstance(product['sale'][field], (int, float)) and product['sale'][field] > 0:
                                                price = float(product['sale'][field]) / divisor
                                                if price > 10:
                                                    break
                            
                            # Улучшенное получение рейтинга
                            rating = 0
                            if 'reviewRating' in product and product['reviewRating'] and isinstance(product['reviewRating'], (int, float)):
                                rating = float(product['reviewRating'])
                            elif 'rating' in product and product['rating'] and isinstance(product['rating'], (int, float)):
                                rating = float(product['rating'])
                                
                            # Более надежное получение отзывов
                            feedbacks = 0
                            feedback_fields = ['feedbacks', 'feedbackCount', 'reviewCount', 'totalReviews']
                            for field in feedback_fields:
                                if field in product and product[field] and isinstance(product[field], (int, float)):
                                    feedbacks = int(product[field])
                                    break
                            
                            # Формируем данные о товаре с улучшенной обработкой пустых полей
                            product_data = {
                                'id': product_id,
                                'name': product.get('name', 'Товар без названия'),
                                'price': price,
                                'brand': product.get('brand', 'Бренд не указан'),
                                'rating': rating,
                                'feedbacks': feedbacks,
                                'url': f"https://www.wildberries.ru/catalog/{product_id}/detail.aspx",
                                'pic_url': get_product_image_url(product)
                            }
                            
                            results.append(product_data)
                        
                        logger.info(f"Найдено {len(results)} товаров по запросу '{query}'")
                        
                        # Добавляем случайную задержку от 0.5 до 2 секунд
                        delay = random.uniform(0.5, 2.0)
                        logger.debug(f"Задержка после запроса: {delay:.2f} сек")
                        time.sleep(delay)
                        
                        return results
                    else:
                        logger.warning(f"Нет товаров в ответе API для запроса: '{query}'")
                        return []
                
                elif response.status_code == 429:
                    logger.warning(f"Слишком много запросов (429). Попытка {attempt + 1}/{MAX_RETRIES}")
                    if attempt < MAX_RETRIES - 1:
                        # Увеличиваем задержку с каждой попыткой (от 1 до 3 секунд)
                        delay = 1 * (attempt + 1) + random.uniform(0, 2)
                        logger.debug(f"Задержка перед повторной попыткой: {delay:.2f} сек")
                        time.sleep(delay)
                    continue
                
                else:
                    logger.error(f"Ошибка API: HTTP {response.status_code} для запроса: '{query}'")
                    if attempt < MAX_RETRIES - 1:
                        # Задержка перед повторной попыткой
                        time.sleep(random.uniform(1, 3))
                        continue
                    return []
            
            except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
                logger.error(f"Ошибка при запросе к API: {e}")
                if attempt < MAX_RETRIES - 1:
                    # Задержка перед повторной попыткой
                    time.sleep(random.uniform(1, 3))
                    continue
                return []
        
        # Если все попытки неудачны
        logger.error(f"Исчерпаны все попытки запроса для '{query}'")
        return []
    
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при поиске товаров: {e}")
        return []

def get_product_image_url(product: Dict[str, Any]) -> str:
    """
    Формирует URL изображения товара
    
    Args:
        product: Словарь с данными о товаре
        
    Returns:
        str: URL изображения товара
    """
    try:
        product_id = product.get('id')
        if not product_id:
            return ""
        
        # Преобразуем ID в строку
        id_str = str(product_id)
        
        # Формируем URL согласно формату Wildberries
        basket = id_str[:len(id_str)-4]
        vol = id_str[-4:-2] if len(id_str) > 4 else "0"
        part = id_str[-2:] if len(id_str) > 2 else "0"
        
        # URL для первого изображения
        return f"https://basket-{basket}.wb.ru/vol{vol}/part{part}/{id_str}/images/big/1.jpg"
    
    except Exception as e:
        logger.error(f"Ошибка при формировании URL изображения: {e}")
        return ""

def extract_search_query(text: str) -> Optional[str]:
    """
    Извлекает поисковый запрос из текста пользователя
    
    Args:
        text: Текст сообщения пользователя
        
    Returns:
        Optional[str]: Поисковый запрос или None, если запрос не найден
    """
    # Списки ключевых слов и фраз для разных языков
    search_keywords = [
        # Русский
        r'найди', r'найти', r'поищи', r'поиск', r'ищу', 
        r'покажи', r'хочу купить', r'хочу найти', r'где купить',
        r'подбери', r'посоветуй', r'подскажи', r'помоги найти',
        # Английский
        r'find', r'search', r'looking for', r'show me', 
        r'want to buy', r'where to buy', r'recommend', r'suggest'
    ]
    
    # Шаблоны для извлечения запроса после ключевых слов
    patterns = [
        # Русские шаблоны
        r'найди\s+([\w\s\d\-.,"\'«»]+)',
        r'поищи\s+([\w\s\d\-.,"\'«»]+)',
        r'найти\s+([\w\s\d\-.,"\'«»]+)',
        r'ищу\s+([\w\s\d\-.,"\'«»]+)',
        r'покажи\s+([\w\s\d\-.,"\'«»]+)',
        r'хочу\s+купить\s+([\w\s\d\-.,"\'«»]+)',
        r'хочу\s+найти\s+([\w\s\d\-.,"\'«»]+)',
        r'где\s+купить\s+([\w\s\d\-.,"\'«»]+)',
        r'подбери\s+([\w\s\d\-.,"\'«»]+)',
        r'посоветуй\s+([\w\s\d\-.,"\'«»]+)',
        r'подскажи\s+([\w\s\d\-.,"\'«»]+)',
        r'помоги\s+найти\s+([\w\s\d\-.,"\'«»]+)',
        # Английские шаблоны
        r'find\s+([\w\s\d\-.,"\'«»]+)',
        r'search\s+for\s+([\w\s\d\-.,"\'«»]+)',
        r'looking\s+for\s+([\w\s\d\-.,"\'«»]+)',
        r'show\s+me\s+([\w\s\d\-.,"\'«»]+)',
        r'want\s+to\s+buy\s+([\w\s\d\-.,"\'«»]+)',
        r'where\s+to\s+buy\s+([\w\s\d\-.,"\'«»]+)',
        r'recommend\s+([\w\s\d\-.,"\'«»]+)',
    ]
    
    # Проверяем наличие ключевых слов
    has_search_keyword = False
    for keyword in search_keywords:
        if re.search(fr'\b{keyword}\b', text.lower()):
            has_search_keyword = True
            break
    
    # Если есть ключевое слово, пытаемся извлечь запрос
    if has_search_keyword:
        for pattern in patterns:
            match = re.search(pattern, text.lower())
            if match:
                query = match.group(1).strip()
                # Очищаем от знаков препинания в конце
                query = re.sub(r'[.,!?:;]$', '', query)
                return query
    
    # Если не нашли по шаблонам, но есть ключевое слово, пробуем простой алгоритм
    if has_search_keyword:
        for keyword in search_keywords:
            parts = re.split(fr'\b{keyword}\b', text.lower(), maxsplit=1)
            if len(parts) > 1 and parts[1].strip():
                query = parts[1].strip()
                # Очищаем от знаков препинания в конце
                query = re.sub(r'[.,!?:;]$', '', query)
                return query
    
    # Возвращаем None, если запрос не найден
    return None

def format_search_results(results: List[Dict[str, Any]], include_images: bool = False) -> str:
    """
    Форматирует результаты поиска для отображения в Telegram
    
    Args:
        results: Список словарей с данными о товарах
        include_images: Включать ли URL изображений в результат
        
    Returns:
        str: Отформатированный текст результатов поиска
    """
    if not results:
        return "❌ По вашему запросу ничего не найдено."
    
    formatted_text = "🔍 *Результаты поиска:*\n\n"
    
    for i, product in enumerate(results):
        name = product.get('name', 'Название неизвестно')
        price = product.get('price', 0)
        brand = product.get('brand', 'Бренд неизвестен')
        rating = product.get('rating', 0)
        feedbacks = product.get('feedbacks', 0)
        url = product.get('url', '')
        
        # Форматируем цену с улучшенной проверкой
        if price and price > 10:  # Проверка на реалистичность цены
            formatted_price = f"{price:,.0f}".replace(',', ' ')
            price_text = f"💰 Цена: *{formatted_price} ₽*\n"
        else:
            price_text = "💰 Цена: *не указана*\n"
        
        # Улучшенное форматирование рейтинга в виде звезд
        if rating and rating > 0:
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
            
            # Добавляем количество отзывов, если они есть
            if feedbacks and feedbacks > 0:
                rating_text = f"⭐ Рейтинг: {star_rating} {rating:.1f} ({feedbacks} отзывов)\n"
            else:
                rating_text = f"⭐ Рейтинг: {star_rating} {rating:.1f}\n"
        else:
            rating_text = "⭐ Рейтинг: ☆☆☆☆☆ нет оценок\n"
        
        # Форматируем бренд с проверкой
        brand_text = f"🏭 Бренд: {brand}\n" if brand and brand != 'Бренд не указан' else ""
        
        # Формируем сообщение для товара
        product_text = (
            f"*{i+1}. {name}*\n"
            f"{price_text}"
            f"{brand_text}"
            f"{rating_text}"
            f"🔗 [Перейти к товару]({url})\n"
        )
        
        # Добавляем URL изображения, если нужно
        if include_images and product.get('pic_url'):
            product_text += f"🖼 [Фото товара]({product.get('pic_url')})\n"
        
        formatted_text += product_text + "\n"
    
    return formatted_text

# Если модуль запущен напрямую, проводим тестовый поиск
if __name__ == "__main__":
    import sys
    
    # Определяем поисковый запрос
    test_query = "наушники jbl"
    if len(sys.argv) > 1:
        test_query = " ".join(sys.argv[1:])
    
    print(f"🔍 Тестовый поиск: '{test_query}'")
    print("⏳ Выполняется запрос к API Wildberries...")
    
    # Выполняем поиск
    results = search_products(test_query)
    
    # Выводим результаты
    if results:
        print(f"✅ Найдено {len(results)} товаров")
        
        # Вывод отладочной информации о первом товаре
        if results:
            first_product = results[0]
            print("\n📌 Детали первого найденного товара:")
            print(f"ID: {first_product.get('id')}")
            print(f"Название: {first_product.get('name')}")
            print(f"Цена: {first_product.get('price')}")
            print(f"Бренд: {first_product.get('brand')}")
            print(f"Рейтинг: {first_product.get('rating')}")
            print(f"Отзывы: {first_product.get('feedbacks')}")
            print(f"URL: {first_product.get('url')}")
            
            # Пытаемся вывести все доступные поля
            print("\n🔍 Все поля первого товара:")
            for key, value in first_product.items():
                print(f"{key}: {value}")
        
        # Выводим форматированные результаты
        print("\n📋 Форматированные результаты поиска:")
        formatted_results = format_search_results(results)
        print(formatted_results)
    else:
        print("❌ Ничего не найдено или произошла ошибка.") 