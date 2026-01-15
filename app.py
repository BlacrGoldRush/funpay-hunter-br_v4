import os
import logging
import requests
import re
from flask import Flask, request, jsonify
from bs4 import BeautifulSoup
from datetime import datetime
import threading
import time
from telegram import Bot
from telegram.error import TelegramError

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Конфигурация из переменных окружения
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '').strip()

# Глобальные переменные
found_items = {}
monitoring_active = False
monitoring_thread = None

def send_telegram_message(message, parse_mode='HTML'):
    """Отправка сообщения в Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("⚠️ Telegram не настроен, пропускаем отправку")
        return False
    
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode=parse_mode)
        logger.info(f"📨 Отправлено в Telegram: {message[:50]}...")
        return True
    except TelegramError as e:
        logger.error(f"❌ Ошибка Telegram: {e}")
        return False

def fast_parse_black_russia(url, category):
    """БЫСТРЫЙ парсинг для Render (таймаут 10 секунд)"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        }
        
        logger.info(f"⚡ Быстрый парсинг {category}...")
        
        # БЫСТРЫЙ запрос с коротким таймаутом
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            logger.error(f"❌ Ошибка HTTP: {response.status_code}")
            return []
        
        soup = BeautifulSoup(response.text, 'html.parser')
        items = []
        
        # Ищем ВСЕ карточки товаров - используем более гибкий подход
        # На FunPay могут быть разные структуры
        all_cards = []
        
        # Пробуем разные селекторы
        for selector in ['div.tc-item', 'a.tc-item', '.tc-item', '[class*="tc-item"]']:
            found = soup.select(selector)
            if found:
                all_cards.extend(found)
        
        # Убираем дубликаты
        unique_cards = []
        seen = set()
        for card in all_cards:
            card_hash = hash(str(card))
            if card_hash not in seen:
                seen.add(card_hash)
                unique_cards.append(card)
        
        logger.info(f"📦 Найдено уникальных карточек: {len(unique_cards)}")
        
        # Обрабатываем только первые 15 карточек (для скорости)
        for card in unique_cards[:15]:
            try:
                # 1. Извлекаем название
                title = ""
                title_elem = card.find('div', class_='tc-desc-text')
                if title_elem:
                    title = title_elem.get_text(strip=True)
                else:
                    # Альтернативный поиск названия
                    for elem in card.find_all(['div', 'span', 'h3', 'h4']):
                        if elem.get_text(strip=True):
                            title = elem.get_text(strip=True)
                            break
                
                if not title:
                    continue
                
                # 2. Фильтруем Black Russia (гибкая фильтрация)
                title_lower = title.lower()
                keywords = ['black russia', 'blackrussia', 'блек раша', 'блэк раша', 'br ', 'бр ']
                
                if not any(keyword in title_lower for keyword in keywords):
                    continue
                
                # 3. Извлекаем цену
                price = 0
                price_elem = card.find('div', class_='tc-price')
                if price_elem:
                    price_text = price_elem.get_text(strip=True)
                    digits = re.findall(r'\d+', price_text.replace(' ', ''))
                    if digits:
                        price = int(''.join(digits))
                
                if price < 10 or price > 50000:
                    continue
                
                # 4. Извлекаем ссылку
                link = url
                link_elem = card if card.name == 'a' else card.find('a')
                if link_elem and link_elem.get('href'):
                    href = link_elem['href']
                    if href.startswith('/'):
                        link = f"https://funpay.com{href}"
                    elif href.startswith('http'):
                        link = href
                
                # 5. Проверяем статус продавца (упрощенно)
                # На FunPay статус может быть в разных местах
                seller_online = False
                status_text = ""
                
                # Ищем статус в разных местах
                for status_class in ['media-user-status', 'online-status', 'status']:
                    status_elem = card.find('div', class_=status_class)
                    if status_elem:
                        status_text = status_elem.get_text(strip=True).lower()
                        if 'онлайн' in status_text or 'online' in status_text:
                            seller_online = True
                            break
                
                # Если не нашли статус, можем пропустить или считать офлайн
                if not seller_online:
                    # Можно раскомментировать, если хотим ТОЛЬКО онлайн
                    # continue
                    pass
                
                # 6. Создаем ID
                item_id = f"{hash(title)}_{price}"
                
                items.append({
                    'id': item_id,
                    'title': title[:100],
                    'price': price,
                    'link': link,
                    'category': category,
                    'seller_online': seller_online
                })
                
                logger.info(f"   ✅ '{title[:40]}...' - {price} руб. {'(онлайн)' if seller_online else ''}")
                
            except Exception as e:
                logger.debug(f"⚠️ Ошибка карточки: {e}")
                continue
        
        logger.info(f"🎯 Найдено подходящих товаров: {len(items)}")
        return items
        
    except requests.exceptions.Timeout:
        logger.error("⏱️ Таймаут запроса к FunPay (10 сек)")
        return []
    except requests.exceptions.RequestException as e:
        logger.error(f"🌐 Ошибка сети: {e}")
        return []
    except Exception as e:
        logger.error(f"💥 Неизвестная ошибка: {e}")
        return []

def check_new_items():
    """Проверка новых товаров"""
    global found_items
    
    if not monitoring_active:
        return
    
    logger.info("🔍 Проверка новых товаров...")
    
    urls_to_monitor = [
        ("https://funpay.com/chips/186/", "Black Russia - Вирты"),
    ]
    
    for url, category in urls_to_monitor:
        current_items = fast_parse_black_russia(url, category)
        
        for item in current_items:
            item_id = item['id']
            if item_id not in found_items:
                found_items[item_id] = item
                
                # Отправляем только если продавец онлайн
                if item.get('seller_online'):
                    message = (
                        f"🎮 <b>НОВОЕ ПРЕДЛОЖЕНИЕ</b>\n\n"
                        f"📦 {item['title']}\n"
                        f"💰 <b>Цена:</b> {item['price']} руб.\n"
                        f"🟢 <b>Продавец онлайн</b>\n"
                        f"🔗 <a href='{item['link']}'>Купить на FunPay</a>\n\n"
                        f"⏰ {datetime.now().strftime('%H:%M:%S')}"
                    )
                    send_telegram_message(message)
    
    logger.info(f"📊 Всего в памяти: {len(found_items)} товаров")

def monitoring_loop():
    """Цикл мониторинга"""
    global monitoring_active
    
    logger.info("🔄 Мониторинг запущен")
    
    while monitoring_active:
        try:
            check_new_items()
            # Ждем 30 секунд (вместо 60 для бесплатного Render)
            for _ in range(30):
                if not monitoring_active:
                    break
                time.sleep(1)
        except Exception as e:
            logger.error(f"❌ Ошибка мониторинга: {e}")
            time.sleep(10)

# ==================== FLASK ROUTES ====================

@app.route('/')
def index():
    status = "🟢 АКТИВЕН" if monitoring_active else "🔴 ОСТАНОВЛЕН"
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>FunPay Hunter для Black Russia</title>
        <meta charset="utf-8">
        <style>
            body {{ font-family: Arial; margin: 40px; }}
            .card {{ background: #f8f9fa; padding: 20px; border-radius: 10px; margin: 20px 0; }}
            .btn {{ display: inline-block; padding: 10px 20px; margin: 5px; color: white; text-decoration: none; border-radius: 5px; }}
            .btn-green {{ background: #28a745; }}
            .btn-blue {{ background: #007bff; }}
            .btn-red {{ background: #dc3545; }}
            .btn-orange {{ background: #fd7e14; }}
        </style>
    </head>
    <body>
        <h1>🚀 FunPay Hunter для Black Russia</h1>
        
        <div class="card">
            <h3>📊 Статус системы</h3>
            <p><strong>Мониторинг:</strong> {status}</p>
            <p><strong>Найдено товаров:</strong> {len(found_items)}</p>
            <p><strong>Время:</strong> {datetime.now().strftime("%H:%M:%S")}</p>
            <p><strong>Telegram:</strong> {'✅ Настроен' if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID else '❌ Не настроен'}</p>
        </div>
        
        <div>
            <a href="/test" class="btn btn-blue">🔍 Тест парсинга</a>
            <a href="/quick_test" class="btn btn-orange">⚡ Быстрый тест</a>
            <a href="/start_monitor" class="btn btn-green">▶️ Запустить мониторинг</a>
            <a href="/stop_monitor" class="btn btn-red">⏹️ Остановить мониторинг</a>
            <a href="/check" class="btn btn-blue">🔄 Проверить сейчас</a>
        </div>
        
        <div class="card">
            <h3>📋 Инструкция</h3>
            <ol>
                <li>Нажмите "Быстрый тест" для проверки подключения</li>
                <li>Нажмите "Тест парсинга" для полной проверки</li>
                <li>Запустите мониторинг</li>
                <li>Бот будет присылать уведомления в Telegram</li>
            </ol>
            <p><strong>Telegram команды:</strong> /start, /check, /monitor, /stop, /status</p>
        </div>
    </body>
    </html>
    '''

@app.route('/test')
def test():
    """Полный тест парсинга"""
    try:
        items = fast_parse_black_russia("https://funpay.com/chips/186/", "Black Russia")
        
        if items:
            html = f"<h2>✅ Найдено {len(items)} товаров:</h2>"
            for item in items:
                online_badge = "🟢 ОНЛАЙН" if item['seller_online'] else "🔴 ОФФЛАЙН"
                html += f'''
                <div style="border:1px solid #ddd; padding:15px; margin:10px; border-radius:5px;">
                    <h4>{item['title']}</h4>
                    <p><strong>Цена:</strong> {item['price']} руб.</p>
                    <p><strong>Статус:</strong> {online_badge}</p>
                    <p><a href="{item['link']}" target="_blank">Открыть на FunPay</a></p>
                </div>
                '''
        else:
            html = '''
            <div style="background:#f8d7da; padding:20px; border-radius:5px;">
                <h2>❌ Товары не найдены</h2>
                <p>Возможные причины:</p>
                <ul>
                    <li>Нет онлайн продавцов в данный момент</li>
                    <li>Страница FunPay недоступна</li>
                    <li>Изменена структура сайта</li>
                </ul>
                <p>Попробуйте <a href="/quick_test">быстрый тест</a> для проверки подключения.</p>
            </div>
            '''
        
        return f'''
        <!DOCTYPE html>
        <html>
        <head><title>Тест парсинга</title></head>
        <body style="font-family:Arial; margin:20px;">
            <a href="/">← Назад</a>
            {html}
        </body>
        </html>
        '''
    except Exception as e:
        return f"<h2>❌ Ошибка:</h2><pre>{e}</pre><p><a href='/'>Назад</a></p>"

@app.route('/quick_test')
def quick_test():
    """Быстрый тест подключения"""
    try:
        import time
        start_time = time.time()
        
        response = requests.get("https://funpay.com/chips/186/", timeout=5)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Быстрый анализ
        all_divs = len(soup.find_all('div'))
        tc_items = len(soup.find_all(class_='tc-item'))
        tc_desc = len(soup.find_all(class_='tc-desc-text'))
        
        end_time = time.time()
        
        return f'''
        <!DOCTYPE html>
        <html>
        <head><title>Быстрый тест</title></head>
        <body style="font-family:Arial; margin:20px;">
            <a href="/">← Назад</a>
            <h2>⚡ Быстрый тест подключения</h2>
            <div style="background:#d1ecf1; padding:20px; border-radius:5px;">
                <p><strong>Статус:</strong> ✅ Успешно</p>
                <p><strong>Время ответа:</strong> {(end_time-start_time):.2f} сек</p>
                <p><strong>Код ответа:</strong> {response.status_code}</p>
                <p><strong>Всего div элементов:</strong> {all_divs}</p>
                <p><strong>Элементов .tc-item:</strong> {tc_items}</p>
                <p><strong>Элементов .tc-desc-text:</strong> {tc_desc}</p>
                <p><strong>Размер страницы:</strong> {len(response.text)//1000} КБ</p>
            </div>
            <p><a href="/test">Полный тест парсинга →</a></p>
        </body>
        </html>
        '''
    except Exception as e:
        return f'''
        <!DOCTYPE html>
        <html>
        <body style="font-family:Arial; margin:20px;">
            <a href="/">← Назад</a>
            <h2>❌ Ошибка подключения</h2>
            <div style="background:#f8d7da; padding:20px; border-radius:5px;">
                <p><strong>Ошибка:</strong> {e}</p>
                <p>FunPay недоступен или блокирует запросы.</p>
                <p>Попробуйте позже или проверьте настройки сети.</p>
            </div>
        </body>
        </html>
        '''

@app.route('/start_monitor')
def start_monitor():
    """Запуск мониторинга"""
    global monitoring_active, monitoring_thread
    
    if not monitoring_active:
        monitoring_active = True
        monitoring_thread = threading.Thread(target=monitoring_loop)
        monitoring_thread.daemon = True
        monitoring_thread.start()
        
        send_telegram_message("✅ <b>Мониторинг запущен!</b>\nБот будет проверять новые предложения каждые 30 секунд.")
        
        return '''
        <!DOCTYPE html>
        <html>
        <body style="font-family:Arial; margin:20px;">
            <a href="/">← Назад</a>
            <h2>✅ Мониторинг запущен</h2>
            <p>Бот начал отслеживать новые предложения.</p>
            <p>Проверка каждые 30 секунд.</p>
            <p>Вы получите уведомление в Telegram при появлении новых товаров.</p>
        </body>
        </html>
        '''
    else:
        return '''
        <!DOCTYPE html>
        <html>
        <body style="font-family:Arial; margin:20px;">
            <a href="/">← Назад</a>
            <h2>⚠️ Мониторинг уже запущен</h2>
        </body>
        </html>
        '''

@app.route('/stop_monitor')
def stop_monitor():
    """Остановка мониторинга"""
    global monitoring_active
    monitoring_active = False
    send_telegram_message("⏸️ <b>Мониторинг остановлен</b>")
    
    return '''
    <!DOCTYPE html>
    <html>
    <body style="font-family:Arial; margin:20px;">
        <a href="/">← Назад</a>
        <h2>⏸️ Мониторинг остановлен</h2>
        <p>Бот больше не проверяет новые предложения.</p>
    </body>
    </html>
    '''

@app.route('/check')
def manual_check():
    """Ручная проверка"""
    check_new_items()
    return f'''
    <!DOCTYPE html>
    <html>
    <body style="font-family:Arial; margin:20px;">
        <a href="/">← Назад</a>
        <h2>🔍 Проверка выполнена</h2>
        <p>Проверено на наличие новых предложений.</p>
        <p>Найдено товаров: {len(found_items)}</p>
    </body>
    </html>
    '''

@app.route('/webhook', methods=['POST'])
def webhook():
    """Webhook для Telegram"""
    try:
        data = request.get_json()
        
        if 'message' in data and 'text' in data['message']:
            text = data['message']['text']
            chat_id = data['message']['chat']['id']
            
            if str(chat_id) != TELEGRAM_CHAT_ID:
                return jsonify({'status': 'error'}), 403
            
            if text == '/start':
                send_telegram_message(
                    "🚀 <b>FunPay Hunter для Black Russia</b>\n\n"
                    "Я отслеживаю новые предложения на FunPay.\n"
                    "Только онлайн продавцы, мгновенные уведомления.\n\n"
                    "<b>Команды:</b>\n"
                    "/check - проверить сейчас\n"
                    "/monitor - запустить авто-проверку\n"
                    "/stop - остановить\n"
                    "/status - статус\n"
                    "/help - помощь"
                )
            
            elif text == '/check':
                send_telegram_message("🔍 Проверяю...")
                check_new_items()
                send_telegram_message(f"✅ Проверено. Товаров в памяти: {len(found_items)}")
            
            elif text == '/monitor':
                global monitoring_active
                if not monitoring_active:
                    monitoring_active = True
                    thread = threading.Thread(target=monitoring_loop)
                    thread.daemon = True
                    thread.start()
                    send_telegram_message("✅ Мониторинг запущен! Проверка каждые 30 сек.")
                else:
                    send_telegram_message("⚠️ Мониторинг уже запущен")
            
            elif text == '/stop':
                monitoring_active = False
                send_telegram_message("⏸️ Мониторинг остановлен")
            
            elif text == '/status':
                status = "🟢 АКТИВЕН" if monitoring_active else "🔴 ОСТАНОВЛЕН"
                send_telegram_message(
                    f"📊 <b>Статус</b>\n\n"
                    f"Мониторинг: {status}\n"
                    f"Товаров: {len(found_items)}\n"
                    f"Время: {datetime.now().strftime('%H:%M:%S')}"
                )
            
            elif text == '/help':
                send_telegram_message(
                    "❓ <b>Помощь</b>\n\n"
                    "Бот отслеживает Black Russia на FunPay.\n"
                    "Только онлайн продавцы, цена 10-50000 руб.\n\n"
                    "Веб-интерфейс: откройте в браузере адрес вашего сервиса на Render."
                )
        
        return jsonify({'status': 'ok'})
    
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({'status': 'error'}), 500

@app.route('/health')
def health():
    return jsonify({
        'status': 'ok',
        'monitoring': monitoring_active,
        'items': len(found_items),
        'time': datetime.now().isoformat()
    })

# Запуск приложения
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
