import logging
from telegram import Update
from telegram.ext import ContextTypes

# Импортируем функции обработки кнопок из product_handlers
from product_handlers import handle_similar_cheaper_button

# Настройка логирования
logger = logging.getLogger(__name__)

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обработчик нажатий на кнопки в сообщениях
    """
    query = update.callback_query
    data = query.data
    
    logger.info(f"Получен callback query: {data}")
    
    try:
        # Обрабатываем нажатие на кнопку "Найти похожие товары дешевле"
        if data.startswith("similar_cheaper_"):
            article = data.replace("similar_cheaper_", "")
            logger.info(f"Запрос на поиск похожих товаров дешевле для артикула {article}")
            await handle_similar_cheaper_button(update, context, article)
            return
            
        # Другие обработчики кнопок могут быть добавлены здесь
            
    except Exception as e:
        logger.error(f"Ошибка при обработке callback query: {str(e)}", exc_info=True)
        await query.answer("Произошла ошибка при обработке запроса. Пожалуйста, попробуйте снова.") 