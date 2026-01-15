# Конфигурация Gunicorn для Render
import multiprocessing

# Количество воркеров
workers = 1

# Таймауты (увеличиваем для Render)
timeout = 30  # 30 секунд на запрос
keepalive = 5

# Логирование
accesslog = '-'
errorlog = '-'
loglevel = 'info'

# Перезапуск воркеров
max_requests = 100
max_requests_jitter = 20

# Бинд порта
bind = "0.0.0.0:10000"
