#!/usr/bin/env python3
"""
Скрипт для очистки оперативной памяти
Автозапуск: каждые 5 минут или при достижении 900 МБ
"""

import os
import sys
import time
import signal
import subprocess
import logging
from datetime import datetime

# Настройки
CHECK_INTERVAL = 300  # 5 минут в секундах
MEMORY_THRESHOLD = 900  # МБ
LOG_FILE = "/tmp/ram_cleaner.log"

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def get_memory_usage():
    """Получить использование памяти в МБ"""
    try:
        with open('/proc/meminfo', 'r') as f:
            meminfo = dict((i.split()[0].rstrip(':'), int(i.split()[1]))
                          for i in f.readlines())
        
        total = meminfo['MemTotal'] / 1024  # КБ -> МБ
        free = meminfo['MemFree'] / 1024
        available = meminfo['MemAvailable'] / 1024 if 'MemAvailable' in meminfo else free
        used = total - available
        
        return {
            'total_mb': round(total, 1),
            'used_mb': round(used, 1),
            'free_mb': round(free, 1),
            'available_mb': round(available, 1),
            'usage_percent': round((used / total) * 100, 1)
        }
    except Exception as e:
        logger.error(f"Ошибка чтения meminfo: {e}")
        return None

def clear_memory():
    """Очистка кеша памяти"""
    try:
        # Синхронизация диска
        os.sync()
        
        # Очистка кеша страниц
        with open('/proc/sys/vm/drop_caches', 'w') as f:
            f.write('3\n')  # 1 - pagecache, 2 - dentries/inodes, 3 - все
        
        # Очистка буферов
        subprocess.run(['sync'], check=False)
        
        logger.info("✅ Кеш памяти очищен")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка очистки памяти: {e}")
        return False

def clear_swap():
    """Очистка swap (если используется)"""
    try:
        with open('/proc/swaps', 'r') as f:
            swaps = f.readlines()[1:]  # Пропускаем заголовок
        
        if swaps:
            # Отключаем и включаем swap
            subprocess.run(['swapoff', '-a'], check=False)
            subprocess.run(['swapon', '-a'], check=False)
            logger.info("✅ Swap очищен")
            return True
    except Exception as e:
        logger.error(f"❌ Ошибка очистки swap: {e}")
    
    return False

def kill_memory_hogs():
    """Поиск и завершение процессов, жрущих память"""
    try:
        result = subprocess.run(
            ['ps', 'aux', '--sort=-%mem'],
            capture_output=True,
            text=True,
            check=False
        )
        
        processes = []
        for line in result.stdout.strip().split('\n')[1:11]:  # Топ-10 процессов
            if line:
                parts = line.split()
                if len(parts) >= 11:
                    pid = parts[1]
                    user = parts[0]
                    mem = float(parts[3])
                    cmd = ' '.join(parts[10:])
                    
                    if mem > 10.0:  # Процессы использующие >10% памяти
                        processes.append({
                            'pid': pid,
                            'user': user,
                            'mem': mem,
                            'cmd': cmd[:50]
                        })
        
        if processes:
            logger.info("📊 Топ процессов по памяти:")
            for p in processes:
                logger.info(f"   PID {p['pid']} ({p['user']}): {p['mem']}% - {p['cmd']}")
        
        return processes
    except Exception as e:
        logger.error(f"Ошибка поиска процессов: {e}")
        return []

def clean_system():
    """Полная очистка системы"""
    mem_before = get_memory_usage()
    if not mem_before:
        return False
    
    logger.info(f"🧹 Начало очистки. Память: {mem_before['used_mb']}/{mem_before['total_mb']} МБ ({mem_before['usage_percent']}%)")
    
    # Очищаем память
    clear_memory()
    clear_swap()
    
    # Ждем обновления статистики
    time.sleep(2)
    
    # Проверяем результат
    mem_after = get_memory_usage()
    if mem_after:
        freed = mem_before['used_mb'] - mem_after['used_mb']
        logger.info(f"✅ Очистка завершена. Освобождено: {freed:.1f} МБ")
        logger.info(f"📊 После: {mem_after['used_mb']}/{mem_after['total_mb']} МБ ({mem_after['usage_percent']}%)")
        return freed > 0
    return False

def signal_handler(signum, frame):
    """Обработчик сигналов"""
    logger.info(f"Получен сигнал {signum}, завершение...")
    sys.exit(0)

def main():
    """Основная функция"""
    logger.info("🚀 Запуск RAM cleaner")
    logger.info(f"⚙ Настройки: интервал {CHECK_INTERVAL} сек, порог {MEMORY_THRESHOLD} МБ")
    
    # Регистрация обработчиков сигналов
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    last_clean_time = 0
    
    while True:
        try:
            current_time = time.time()
            mem_info = get_memory_usage()
            
            if mem_info:
                # Проверка по порогу
                if mem_info['used_mb'] >= MEMORY_THRESHOLD:
                    logger.warning(f"⚠ Превышен порог памяти: {mem_info['used_mb']} МБ >= {MEMORY_THRESHOLD} МБ")
                    clean_system()
                    last_clean_time = current_time
                
                # Проверка по таймеру (каждые 5 минут)
                elif current_time - last_clean_time >= CHECK_INTERVAL:
                    logger.info(f"⏰ Плановый запуск очистки")
                    clean_system()
                    last_clean_time = current_time
                
                else:
                    # Только логирование состояния
                    time_until_next = int(CHECK_INTERVAL - (current_time - last_clean_time))
                    logger.debug(f"📊 Память: {mem_info['used_mb']} МБ ({mem_info['usage_percent']}%). Следующая проверка через {time_until_next} сек")
            
            # Ждем перед следующей проверкой
            time.sleep(60)  # Проверка каждую минуту
            
        except Exception as e:
            logger.error(f"❌ Ошибка в основном цикле: {e}")
            time.sleep(60)

if __name__ == "__main__":
    # Запуск как демон
    if len(sys.argv) > 1 and sys.argv[1] == "--daemon":
        # Демонизация
        try:
            pid = os.fork()
            if pid > 0:
                sys.exit(0)
        except OSError as e:
            logger.error(f"Ошибка демонизации: {e}")
            sys.exit(1)
        
        # Изменяем рабочую директорию
        os.chdir('/')
        
        # Закрываем стандартные дескрипторы
        sys.stdout.flush()
        sys.stderr.flush()
        
        # Перенаправляем вывод
        with open('/dev/null', 'r') as f:
            os.dup2(f.fileno(), sys.stdin.fileno())
        with open('/tmp/ram_cleaner.out', 'a+') as f:
            os.dup2(f.fileno(), sys.stdout.fileno())
        with open('/tmp/ram_cleaner.err', 'a+') as f:
            os.dup2(f.fileno(), sys.stderr.fileno())
    
    main()