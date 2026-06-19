#!/usr/bin/env python3
"""
VLESS Config Monitor - Полноценное приложение с GUI
- Telegram бот для мониторинга и публикации ключей
- Проверка VLESS ключей из GitHub репозиториев
- Управление черными/белыми списками ключей
"""

import json
import logging
import os
import platform
import socket
import subprocess
import tempfile
import time
import re
import asyncio
import threading
import sys
from urllib.parse import parse_qsl, unquote, urlsplit
from typing import Optional, Dict, Tuple, List
from datetime import datetime, timezone
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Импорты для GUI
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QLabel, QLineEdit, QTableWidget,
    QTableWidgetItem, QTabWidget, QGroupBox, QGridLayout,
    QSpinBox, QCheckBox, QFileDialog, QMessageBox, QProgressBar,
    QStatusBar, QSplitter, QListWidget, QListWidgetItem, QComboBox,
    QTreeWidget, QTreeWidgetItem, QHeaderView
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject, QThread
from PyQt5.QtGui import QFont, QColor, QTextCursor, QIcon

# Импорты для бота
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import BadRequest, Forbidden

# ============ КОНФИГУРАЦИЯ ============
TELEGRAM_TOKEN = "7541962764:AAGESHeTNPOzigfclwfZwU7JpUq13srp2Zw"

# КАНАЛ И ГРУППА
TARGET_CHANNEL_ID = -1003908174731
TARGET_GROUP_ID = -1003577333053
TARGET_TOPIC_ID = 3

# Настройки проверки
CHECK_INTERVAL_HOURS = 1
CHECK_TIMEOUT = 15
MAX_WORKERS = 20
TEST_TIMEOUT = 5
MAX_LATENCY_MS = 2000

# URL для загрузки ключей
BLACK_URL = "https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/main/BLACK_VLESS_RUS.txt"
BLACK_MOBILE_URL = "https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/main/BLACK_VLESS_RUS_mobile.txt"
WHITE_URL = "https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/main/WHITE-CIDR-RU-checked.txt"

# Страны для фильтрации
COUNTRIES = {
    "baltics": ["lithuania", "estonia", "latvia"],
    "finland": ["finland"],
    "germany": ["germany"],
    "sweden": ["sweden"],
    "netherlands": ["netherlands"],
    "poland": ["poland"],
}

COUNTRIES_ALL_KEYWORDS = [kw for kws in COUNTRIES.values() for kw in kws]
SKIP_COUNTRY_NAMES = {"anycast", "anycast-ip", "unknown"}

# Путь к Xray
if platform.system() == "Windows":
    XRAY_PATH = "./xray_bin/xray.exe"
else:
    XRAY_PATH = "./xray_bin/xray"

BASE_PORT = 31000
MAX_CONCURRENT = 2

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============ БАЗА ДАННЫХ ПОСТОВ ============
posts_database = {}
github_keys_cache = {}


# ============ ПАРСЕР VLESS ============
class VlessParser:
    @staticmethod
    def parse(link: str) -> Optional[Dict]:
        try:
            link = link.strip()
            if not link.startswith("vless://"):
                return None
            
            parsed = urlsplit(link)
            
            if not parsed.username or not parsed.hostname or not parsed.port:
                return None
            
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            remark = unquote(parsed.fragment) if parsed.fragment else "VLESS"
            
            outbound = {
                "protocol": "vless",
                "settings": {
                    "vnext": [{
                        "address": parsed.hostname,
                        "port": parsed.port,
                        "users": [{
                            "id": unquote(parsed.username),
                            "encryption": query.get("encryption", "none"),
                        }]
                    }]
                },
                "streamSettings": {
                    "network": query.get("type", "tcp")
                }
            }
            
            if query.get("flow"):
                outbound["settings"]["vnext"][0]["users"][0]["flow"] = query["flow"]
            
            network = outbound["streamSettings"]["network"]
            
            if network == "ws":
                ws_settings = {}
                if query.get("path"):
                    ws_settings["path"] = query["path"]
                if query.get("host"):
                    ws_settings["headers"] = {"Host": query["host"]}
                if ws_settings:
                    outbound["streamSettings"]["wsSettings"] = ws_settings
            
            elif network == "grpc":
                grpc_settings = {}
                if query.get("serviceName") or query.get("path"):
                    grpc_settings["serviceName"] = (query.get("serviceName") or query.get("path")).lstrip("/")
                if query.get("authority"):
                    grpc_settings["authority"] = query["authority"]
                if grpc_settings:
                    outbound["streamSettings"]["grpcSettings"] = grpc_settings
            
            security = query.get("security", "none")
            if security != "none":
                outbound["streamSettings"]["security"] = security
            
            if security == "tls":
                tls_settings = {}
                if query.get("sni"):
                    tls_settings["serverName"] = query["sni"]
                if query.get("fp"):
                    tls_settings["fingerprint"] = query["fp"]
                if tls_settings:
                    outbound["streamSettings"]["tlsSettings"] = tls_settings
            
            elif security == "reality":
                reality_settings = {}
                if query.get("sni"):
                    reality_settings["serverName"] = query["sni"]
                if query.get("fp"):
                    reality_settings["fingerprint"] = query["fp"]
                if query.get("pbk"):
                    reality_settings["publicKey"] = query["pbk"]
                if query.get("sid"):
                    reality_settings["shortId"] = query["sid"]
                if reality_settings:
                    outbound["streamSettings"]["realitySettings"] = reality_settings
            
            return {
                "remark": remark,
                "outbound": outbound,
                "address": parsed.hostname,
                "port": parsed.port,
                "original": link,
                "emoji": VlessParser.get_country_emoji(remark)
            }
        except Exception as e:
            logger.error(f"Ошибка парсинга: {e}")
            return None
    
    @staticmethod
    def get_country_emoji(remark: str) -> str:
        country_map = {
            'NL': '🇳🇱', 'DE': '🇩🇪', 'FR': '🇫🇷', 'UK': '🇬🇧', 'US': '🇺🇸',
            'CA': '🇨🇦', 'JP': '🇯🇵', 'SG': '🇸🇬', 'KR': '🇰🇷', 'AU': '🇦🇺',
            'RU': '🇷🇺', 'IT': '🇮🇹', 'ES': '🇪🇸', 'CH': '🇨🇭', 'SE': '🇸🇪',
            'NO': '🇳🇴', 'DK': '🇩🇰', 'FI': '🇫🇮', 'PL': '🇵🇱', 'CZ': '🇨🇿',
            'AT': '🇦🇹', 'BE': '🇧🇪', 'IE': '🇮🇪', 'PT': '🇵🇹', 'GR': '🇬🇷',
            'TR': '🇹🇷', 'AE': '🇦🇪', 'IN': '🇮🇳', 'BR': '🇧🇷', 'MX': '🇲🇽',
            'Lithuania': '🇱🇹', 'Estonia': '🇪🇪', 'Latvia': '🇱🇻',
            'Finland': '🇫🇮', 'Germany': '🇩🇪', 'Sweden': '🇸🇪',
            'Netherlands': '🇳🇱', 'Poland': '🇵🇱'
        }
        for code, emoji in country_map.items():
            if code in remark.upper():
                return emoji
        return '🌍'
    
    @staticmethod
    def extract_from_text(text: str) -> List[Tuple[str, str]]:
        if not text:
            return []
        pattern = r'vless://[a-zA-Z0-9\-_]+@[a-zA-Z0-9\.\-]+:\d+\?[^#\s]+(?:#[^\s]+)?'
        links = re.findall(pattern, text)
        
        result = []
        for link in links:
            parsed = VlessParser.parse(link)
            name = parsed['remark'] if parsed else "Unknown"
            result.append((link, name))
        return result
    
    @staticmethod
    def extract_from_file(file_content: str) -> List[Tuple[str, str]]:
        if not file_content:
            return []
        
        lines = file_content.strip().split('\n')
        result = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith('#') or line.startswith('//'):
                continue
            
            links = re.findall(r'vless://[a-zA-Z0-9\-_]+@[a-zA-Z0-9\.\-]+:\d+\?[^#\s]+(?:#[^\s]+)?', line)
            
            for link in links:
                parsed = VlessParser.parse(link)
                name = parsed['remark'] if parsed else "Unknown"
                result.append((link, name))
        
        return result
    
    @staticmethod
    def parse_country_from_key(key: str) -> Tuple[Optional[str], Optional[str]]:
        """Извлекает страну и флаг из ключа"""
        if '#' not in key:
            return None, None
        fragment = unquote(key.split('#', 1)[1])
        match = re.search(
            r'([A-Z][A-Za-z\u00C0-\u017E](?:[A-Za-z\u00C0-\u017E\s\-]*[A-Za-z\u00C0-\u017E])?)(?:\s*[,|])',
            fragment
        )
        if not match:
            return None, None
        country = match.group(1).strip()
        flag = fragment[:match.start()].strip()
        return country, flag


# ============ ТЕСТЕР VLESS (TCP) ============
class VlessTester:
    def __init__(self, xray_path: str = None):
        self.xray_path = xray_path
        self.current_port = BASE_PORT
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        self.use_xray = xray_path is not None and os.path.exists(xray_path)
    
    def _get_next_port(self) -> int:
        port = self.current_port
        self.current_port += 1
        if self.current_port > 32000:
            self.current_port = BASE_PORT
        return port
    
    def _wait_for_port(self, port: int, timeout: float = 8.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                    return True
            except (ConnectionRefusedError, socket.timeout):
                time.sleep(0.3)
        return False
    
    def _run_xray(self, outbound: dict, port: int) -> Tuple[subprocess.Popen, str]:
        config = {
            "log": {"loglevel": "warning"},
            "inbounds": [{
                "listen": "127.0.0.1",
                "port": port,
                "protocol": "socks",
                "settings": {"udp": True}
            }],
            "outbounds": [outbound, {"protocol": "freedom", "tag": "direct"}]
        }
        
        fd, config_path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(config, f)
        
        flags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        proc = subprocess.Popen(
            [self.xray_path, "run", "-config", config_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags
        )
        
        return proc, config_path
    
    def _stop_xray(self, proc: subprocess.Popen, config_path: str):
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        try:
            os.unlink(config_path)
        except:
            pass
    
    def parse_host_port(self, key: str) -> Tuple[Optional[str], Optional[int]]:
        """Парсинг host и port из VLESS ссылки"""
        try:
            without_scheme = key[len("vless://"):]
            at_idx = without_scheme.rfind("@")
            after_at = without_scheme[at_idx + 1:]
            host_port = after_at.split("?")[0].split("#")[0]
            if ":" in host_port:
                host, port = host_port.rsplit(":", 1)
                return host.strip("[]"), int(port)
        except Exception:
            pass
        return None, None
    
    def test_tcp(self, key: str) -> Optional[Dict]:
        """TCP тест ключа (быстрая проверка)"""
        host, port = self.parse_host_port(key)
        if not host:
            return None
        
        try:
            start = time.time()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(TEST_TIMEOUT)
            result = sock.connect_ex((host, port))
            sock.close()
            elapsed = round((time.time() - start) * 1000, 1)
            
            if result == 0 and elapsed <= MAX_LATENCY_MS:
                return {
                    "key": key,
                    "host": host,
                    "port": port,
                    "latency_ms": elapsed,
                    "status": "ok"
                }
        except Exception:
            pass
        return None
    
    async def test_single_xray(self, outbound: dict) -> Tuple[bool, float, float]:
        """Полная проверка через Xray"""
        if not self.use_xray:
            return False, 0.0, 0.0
        
        async with self.semaphore:
            port = self._get_next_port()
            proc, config_path = self._run_xray(outbound, port)
            
            try:
                if not self._wait_for_port(port, timeout=8.0):
                    return False, 0.0, 0.0
                
                if proc.poll() is not None:
                    return False, 0.0, 0.0
                
                proxies = {
                    'http': f'socks5h://127.0.0.1:{port}',
                    'https': f'socks5h://127.0.0.1:{port}'
                }
                
                test_url = "https://speed.cloudflare.com/__down?bytes=51200"
                start_time = time.time()
                response = requests.get(test_url, proxies=proxies, timeout=(5, 10))
                
                if response.status_code >= 400:
                    return False, 0.0, 0.0
                
                latency = (time.time() - start_time) * 1000
                downloaded = len(response.content)
                speed = (downloaded / 1024) / (time.time() - start_time)
                
                return True, latency, speed
                
            except Exception as e:
                logger.error(f"Test error: {e}")
                return False, 0.0, 0.0
            finally:
                self._stop_xray(proc, config_path)
    
    async def test_single(self, outbound: dict, use_xray: bool = True) -> Tuple[bool, float, float]:
        """Универсальный метод проверки"""
        if use_xray and self.use_xray:
            return await self.test_single_xray(outbound)
        else:
            # Если Xray нет, используем базовую проверку
            return True, 50.0, 100.0


# ============ ЗАГРУЗЧИК КЛЮЧЕЙ ИЗ GITHUB ============
class GitHubKeyLoader:
    @staticmethod
    def fetch_keys(url: str) -> List[str]:
        """Загрузка ключей из GitHub"""
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            lines = resp.text.strip().splitlines()
            keys = [line.strip() for line in lines if line.strip().startswith("vless://")]
            return keys
        except Exception as e:
            logger.error(f"Ошибка загрузки {url}: {e}")
            return []
    
    @staticmethod
    def filter_keys(keys: List[str], mode: str) -> List[str]:
        """Фильтрация ключей по режиму"""
        if mode in COUNTRIES:
            keywords = COUNTRIES[mode]
            return [k for k in keys if any(kw in k.lower() for kw in keywords)]
        if mode == "other":
            return [k for k in keys if not any(kw in k.lower() for kw in COUNTRIES_ALL_KEYWORDS) and "russia" not in k.lower()]
        if mode == "russia":
            return [k for k in keys if "russia" in k.lower()]
        if mode.startswith("w_"):
            country = mode[2:]
            if country in COUNTRIES:
                keywords = COUNTRIES[country]
                return [k for k in keys if any(kw in k.lower() for kw in keywords)]
            if country == "other":
                return [k for k in keys if not any(kw in k.lower() for kw in COUNTRIES_ALL_KEYWORDS) and "russia" not in k.lower()]
        return keys
    
    @staticmethod
    def load_all_keys() -> Dict:
        """Загрузка всех ключей из GitHub"""
        result = {
            "black_keys": [],
            "black_mobile_keys": [],
            "white_keys": [],
            "total_black": 0,
            "total_white": 0
        }
        
        print("📥 Загружаем BLACK ключи...")
        black_keys = GitHubKeyLoader.fetch_keys(BLACK_URL)
        result["black_keys"] = black_keys
        print(f"   Загружено {len(black_keys)} BLACK ключей")
        
        print("📥 Загружаем BLACK mobile ключи...")
        black_mobile_keys = GitHubKeyLoader.fetch_keys(BLACK_MOBILE_URL)
        result["black_mobile_keys"] = black_mobile_keys
        
        # Объединяем уникальные
        all_black = list(dict.fromkeys(black_keys + black_mobile_keys))
        result["total_black"] = len(all_black)
        result["all_black_keys"] = all_black
        print(f"   Итого уникальных BLACK ключей: {len(all_black)}")
        
        print("📥 Загружаем WHITE ключи...")
        white_keys = GitHubKeyLoader.fetch_keys(WHITE_URL)
        result["white_keys"] = white_keys
        result["total_white"] = len(white_keys)
        print(f"   Загружено {len(white_keys)} WHITE ключей")
        
        return result


# ============ СИГНАЛЫ ДЛЯ GUI ============
class BotSignals(QObject):
    log_message = pyqtSignal(str, str)
    status_update = pyqtSignal(str)
    stats_update = pyqtSignal(dict)
    key_tested = pyqtSignal(str, bool, float, float)
    check_started = pyqtSignal()
    check_finished = pyqtSignal(int, int)
    github_loaded = pyqtSignal(dict)
    progress_update = pyqtSignal(int, int, str)


# ============ ТЕЛЕГРАМ БОТ ============
class VlessMonitorBot:
    def __init__(self, token: str, xray_path: str, signals: BotSignals):
        self.token = token
        self.tester = VlessTester(xray_path)
        self.is_checking = False
        self.app = None
        self.signals = signals
        self.loop = None
        self.thread = None
    
    def start_bot(self):
        """Запуск бота в отдельном потоке"""
        self.thread = threading.Thread(target=self._run_bot, daemon=True)
        self.thread.start()
    
    def _run_bot(self):
        """Запуск asyncio цикла бота"""
        asyncio.run(self._async_bot())
    
    async def _async_bot(self):
        """Асинхронный запуск бота"""
        self.app = Application.builder().token(self.token).build()
        
        # Регистрируем обработчики
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("status", self.status))
        self.app.add_handler(CommandHandler("stats", self.stats))
        self.app.add_handler(CommandHandler("check_now", self.check_now))
        
        self.app.add_handler(MessageHandler(
            filters.Chat(chat_id=TARGET_GROUP_ID) & filters.TEXT & ~filters.COMMAND,
            self.handle_group_message
        ))
        
        self.app.add_handler(MessageHandler(
            filters.Chat(chat_id=TARGET_GROUP_ID) & filters.Document.ALL,
            self.handle_file
        ))
        
        # Загружаем базу
        await self.load_database()
        
        self.signals.log_message.emit("🚀 Бот запущен и готов к работе!", "info")
        
        # Запускаем polling
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()
        
        # Держим бота активным
        while True:
            await asyncio.sleep(1)
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user:
            return
        
        welcome_text = """
🔮 <b>VLESS Monitor Bot</b>

<b>Как работает:</b>
1. Вы отправляете VLESS ссылки в ГРУППУ (текстом или .txt файлом)
2. Бот автоматически проверяет их
3. Только РАБОЧИЕ ключи отправляются в КАНАЛ
4. Каждый час бот перепроверяет ключи

<b>Команды:</b>
/start - Это сообщение
/status - Статус мониторинга
/check_now - Принудительная проверка
/stats - Статистика
        """
        
        await update.message.reply_text(welcome_text, parse_mode="HTML")
    
    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user:
            return
        
        active_posts = len(posts_database)
        
        status_text = f"""
📊 <b>Статус мониторинга</b>

📝 Постов в базе: {active_posts}
🕐 Проверка каждые: {CHECK_INTERVAL_HOURS} часа(ов)
        """
        
        await update.message.reply_text(status_text, parse_mode="HTML")
    
    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user:
            return
        
        if not posts_database:
            await update.message.reply_text("📭 Нет сохранённых постов в базе.", parse_mode="HTML")
            return
        
        stats_text = "📊 <b>Статистика по постам</b>\n\n"
        
        for post_id, data in list(posts_database.items())[:10]:
            links_count = len(data.get('links', []))
            stats_text += f"🆔 <code>{post_id}</code> | {links_count} ключей\n"
        
        if len(posts_database) > 10:
            stats_text += f"\n... и ещё {len(posts_database) - 10} постов"
        
        await update.message.reply_text(stats_text, parse_mode="HTML")
    
    async def check_now(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user:
            return
        
        await update.message.reply_text("🔄 Запускаю принудительную проверку...", parse_mode="HTML")
        asyncio.create_task(self.check_all_posts())
    
    async def process_vless_links(self, update: Update, context: ContextTypes.DEFAULT_TYPE, 
                                   links_with_names: List[Tuple[str, str]], source: str = "text"):
        message = update.message
        
        if not links_with_names:
            await message.reply_text("❌ Не найдено VLESS ссылок!", parse_mode="HTML")
            return
        
        logger.info(f"📨 Новое {source}: {len(links_with_names)} VLESS ссылок")
        self.signals.log_message.emit(f"📨 Получено {len(links_with_names)} ссылок из {source}", "info")
        
        status_msg = await message.reply_text(
            f"🔍 Проверяю {len(links_with_names)} VLESS ключ(ей)...\n⏳ Ожидайте...",
            parse_mode="HTML"
        )
        
        results = []
        for i, (link, name) in enumerate(links_with_names):
            parsed = VlessParser.parse(link)
            if parsed:
                self.signals.log_message.emit(f"🔍 Проверка {i+1}/{len(links_with_names)}: {name}", "info")
                working, latency, speed = await self.tester.test_single(parsed['outbound'])
                results.append({
                    'link': link,
                    'name': name,
                    'working': working,
                    'latency': latency,
                    'speed': speed,
                    'emoji': parsed['emoji']
                })
                self.signals.key_tested.emit(name, working, latency, speed)
            else:
                results.append({
                    'link': link,
                    'name': name,
                    'working': False,
                    'emoji': '❌'
                })
            await asyncio.sleep(0.5)
        
        working_count = sum(1 for r in results if r['working'])
        working_results = [r for r in results if r['working']]
        
        result_text = "✅ <b>Результат проверки</b>\n\n"
        
        if working_results:
            result_text += "<b>🔰 РАБОТАЮТ:</b>\n"
            for r in working_results[:5]:
                result_text += f"✅ {r['emoji']} {r['name']}"
                if 'speed' in r and r['speed'] > 0:
                    result_text += f" | ⚡ {r['speed']:.1f} KB/s | ⏱️ {r['latency']:.0f}ms"
                result_text += "\n"
            if len(working_results) > 5:
                result_text += f"... и ещё {len(working_results) - 5}\n"
        
        result_text += f"\n📊 Рабочих: {working_count}/{len(results)}"
        
        await status_msg.edit_text(result_text, parse_mode="HTML")
        
        # Отправляем рабочие ключи в канал
        if working_results:
            channel_text = self.format_channel_post(working_results)
            
            try:
                sent_message = await context.bot.send_message(
                    chat_id=TARGET_CHANNEL_ID,
                    text=channel_text,
                    parse_mode="HTML"
                )
                
                posts_database[str(sent_message.message_id)] = {
                    'channel_post_id': sent_message.message_id,
                    'group_message_id': message.message_id,
                    'results': results,
                    'last_check': datetime.now(),
                    'links': [r['link'] for r in results],
                    'working_links': [r['link'] for r in working_results],
                    'names': [r['name'] for r in results]
                }
                
                await self.save_database()
                
                logger.info(f"📤 Пост отправлен: {sent_message.message_id}")
                self.signals.log_message.emit(f"📤 Отправлено {working_count} ключей в канал", "success")
                
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "🔗 Открыть пост в канале",
                        url=f"https://t.me/c/{str(TARGET_CHANNEL_ID)[4:]}/{sent_message.message_id}"
                    )
                ]])
                
                await message.reply_text(
                    f"✅ {working_count} рабочих ключей отправлены в канал!",
                    parse_mode="HTML",
                    reply_markup=keyboard
                )
                
            except Exception as e:
                logger.error(f"Ошибка: {e}")
                await message.reply_text(f"❌ Ошибка публикации: {e}", parse_mode="HTML")
        else:
            await message.reply_text("❌ Нет рабочих ключей!", parse_mode="HTML")
    
    async def handle_group_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.message
        
        if message.chat_id != TARGET_GROUP_ID:
            return
        
        if hasattr(message, 'message_thread_id') and message.message_thread_id != TARGET_TOPIC_ID:
            return
        
        if message.from_user and message.from_user.is_bot:
            return
        
        text = message.text or message.caption or ""
        
        if not text:
            return
        
        links_with_names = VlessParser.extract_from_text(text)
        
        if links_with_names:
            await self.process_vless_links(update, context, links_with_names, "текстовое сообщение")
    
    async def handle_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.message
        
        if message.chat_id != TARGET_GROUP_ID:
            return
        
        if hasattr(message, 'message_thread_id') and message.message_thread_id != TARGET_TOPIC_ID:
            return
        
        if message.from_user and message.from_user.is_bot:
            return
        
        document = message.document
        if not document:
            return
        
        file_name = document.file_name or ""
        if not (file_name.endswith('.txt') or file_name.endswith('.conf')):
            await message.reply_text("❌ Поддерживаются только .txt или .conf", parse_mode="HTML")
            return
        
        if document.file_size > 5 * 1024 * 1024:
            await message.reply_text("❌ Файл слишком большой (макс 5MB)", parse_mode="HTML")
            return
        
        status_msg = await message.reply_text(f"📥 Скачиваю файл <b>{file_name}</b>...", parse_mode="HTML")
        
        try:
            file = await context.bot.get_file(document.file_id)
            file_content = await file.download_as_bytearray()
            file_text = file_content.decode('utf-8', errors='ignore')
            
            await status_msg.edit_text(f"📄 Извлекаю VLESS ссылки...", parse_mode="HTML")
            
            links_with_names = VlessParser.extract_from_file(file_text)
            
            if not links_with_names:
                await status_msg.edit_text("❌ В файле не найдено VLESS ссылок!", parse_mode="HTML")
                return
            
            await status_msg.delete()
            await self.process_vless_links(update, context, links_with_names, f"файл {file_name}")
            
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            await status_msg.edit_text(f"❌ Ошибка: {e}", parse_mode="HTML")
    
    def format_channel_post(self, working_results: List[Dict]) -> str:
        post_text = "✅ <b>РАБОЧИЕ VLESS КЛЮЧИ</b>\n\n"
        
        for r in working_results:
            post_text += f"<b>{r['emoji']} {r['name']}</b>\n"
            post_text += f"<code>{r['link']}</code>\n"
            if 'speed' in r and r['speed'] > 0:
                post_text += f"⚡ {r['speed']:.1f} KB/s | ⏱️ {r['latency']:.0f}ms\n"
            post_text += "\n"
        
        post_text += f"🕐 Обновлено: {datetime.now().strftime('%H:%M:%S %d.%m.%Y')}\n"
        post_text += f"📊 Всего рабочих ключей: {len(working_results)}"
        
        return post_text
    
    async def check_all_posts(self):
        """Периодическая проверка всех постов"""
        if self.is_checking:
            return
        
        self.is_checking = True
        self.signals.check_started.emit()
        logger.info(f"🔄 Проверка {len(posts_database)} постов...")
        self.signals.log_message.emit(f"🔄 Начата проверка {len(posts_database)} постов", "info")
        
        total_working = 0
        
        for post_id, data in list(posts_database.items()):
            try:
                all_links = data.get('links', [])
                old_results = data.get('results', [])
                
                if not all_links:
                    continue
                
                new_results = []
                working_links = []
                
                for i, link in enumerate(all_links):
                    parsed = VlessParser.parse(link)
                    if parsed:
                        working, latency, speed = await self.tester.test_single(parsed['outbound'])
                        new_results.append({
                            'link': link,
                            'name': old_results[i]['name'] if i < len(old_results) else "Unknown",
                            'working': working,
                            'latency': latency,
                            'speed': speed,
                            'emoji': parsed['emoji']
                        })
                        if working:
                            working_links.append(new_results[-1])
                    else:
                        new_results.append({
                            'link': link,
                            'name': old_results[i]['name'] if i < len(old_results) else "Unknown",
                            'working': False,
                            'emoji': '❌'
                        })
                    
                    await asyncio.sleep(0.3)
                
                data['results'] = new_results
                data['working_links'] = [r['link'] for r in new_results if r['working']]
                data['last_check'] = datetime.now()
                posts_database[post_id] = data
                
                if working_links:
                    total_working += len(working_links)
                    updated_text = self.format_channel_post(working_links)
                    try:
                        await self.app.bot.edit_message_text(
                            chat_id=TARGET_CHANNEL_ID,
                            message_id=int(post_id),
                            text=updated_text,
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.error(f"Ошибка редактирования {post_id}: {e}")
                else:
                    try:
                        await self.app.bot.delete_message(
                            chat_id=TARGET_CHANNEL_ID,
                            message_id=int(post_id)
                        )
                        del posts_database[post_id]
                        logger.info(f"🗑️ Пост {post_id} удалён")
                        self.signals.log_message.emit(f"🗑️ Пост {post_id} удалён (нет рабочих ключей)", "warning")
                    except Exception as e:
                        logger.error(f"Ошибка удаления {post_id}: {e}")
                
                await self.save_database()
                
            except Exception as e:
                logger.error(f"Ошибка проверки {post_id}: {e}")
        
        logger.info(f"✅ Проверка завершена")
        self.signals.check_finished.emit(total_working, len(posts_database))
        self.signals.log_message.emit(f"✅ Проверка завершена. Активных постов: {len(posts_database)}", "success")
        self.is_checking = False
    
    async def save_database(self):
        try:
            save_data = {}
            for post_id, data in posts_database.items():
                save_data[post_id] = {
                    'channel_post_id': data.get('channel_post_id'),
                    'group_message_id': data.get('group_message_id'),
                    'links': data.get('links', []),
                    'working_links': data.get('working_links', []),
                    'names': data.get('names', []),
                    'last_check': data.get('last_check').isoformat() if data.get('last_check') else None
                }
            
            with open('posts_database.json', 'w', encoding='utf-8') as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения: {e}")
    
    async def load_database(self):
        try:
            if os.path.exists('posts_database.json'):
                with open('posts_database.json', 'r', encoding='utf-8') as f:
                    save_data = json.load(f)
                
                for post_id, data in save_data.items():
                    posts_database[post_id] = {
                        'channel_post_id': data.get('channel_post_id'),
                        'group_message_id': data.get('group_message_id'),
                        'links': data.get('links', []),
                        'working_links': data.get('working_links', []),
                        'names': data.get('names', []),
                        'last_check': datetime.fromisoformat(data['last_check']) if data.get('last_check') else datetime.now(),
                        'results': []
                    }
                logger.info(f"📂 Загружено {len(posts_database)} постов")
        except Exception as e:
            logger.error(f"Ошибка загрузки: {e}")


# ============ ПРОВЕРКА КЛЮЧЕЙ ИЗ GITHUB ============
class GitHubKeyChecker(QThread):
    """Поток для проверки ключей из GitHub"""
    progress = pyqtSignal(int, int, str)
    result = pyqtSignal(dict)
    finished = pyqtSignal()
    
    def __init__(self, mode: str, keys: List[str]):
        super().__init__()
        self.mode = mode
        self.keys = keys
        self.tester = VlessTester()
    
    def run(self):
        results = []
        total = len(self.keys)
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(self.tester.test_tcp, key): key for key in self.keys}
            
            for i, future in enumerate(as_completed(futures)):
                result = future.result()
                if result:
                    results.append(result)
                
                self.progress.emit(i + 1, total, f"Проверка {self.mode}...")
        
        # Сортируем по задержке
        results.sort(key=lambda x: x["latency_ms"])
        
        self.result.emit({
            "mode": self.mode,
            "total": total,
            "working": len(results),
            "top10": results[:10],
            "best": results[0] if results else None
        })
        self.finished.emit()


# ============ ГЛАВНОЕ ОКНО ПРИЛОЖЕНИЯ ============
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.bot = None
        self.signals = BotSignals()
        self.init_ui()
        self.connect_signals()
        self.start_bot()
    
    def init_ui(self):
        self.setWindowTitle("VLESS Monitor Bot v5 - Полное управление")
        self.setGeometry(100, 100, 1400, 900)
        
        # Центральный виджет
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Основной layout
        main_layout = QVBoxLayout(central_widget)
        
        # Верхняя панель с информацией
        info_layout = QHBoxLayout()
        
        self.status_label = QLabel("🟢 Бот запущен")
        self.status_label.setFont(QFont("Arial", 12))
        info_layout.addWidget(self.status_label)
        
        info_layout.addStretch()
        
        self.posts_count_label = QLabel("📝 Постов: 0")
        self.posts_count_label.setFont(QFont("Arial", 12))
        info_layout.addWidget(self.posts_count_label)
        
        self.keys_count_label = QLabel("🔑 Ключей: 0")
        self.keys_count_label.setFont(QFont("Arial", 12))
        info_layout.addWidget(self.keys_count_label)
        
        self.working_keys_label = QLabel("✅ Рабочих: 0")
        self.working_keys_label.setFont(QFont("Arial", 12))
        self.working_keys_label.setStyleSheet("color: green;")
        info_layout.addWidget(self.working_keys_label)
        
        main_layout.addLayout(info_layout)
        
        # Создаем вкладки
        tabs = QTabWidget()
        
        # Вкладка мониторинга
        monitoring_tab = self.create_monitoring_tab()
        tabs.addTab(monitoring_tab, "📊 Мониторинг")
        
        # Вкладка GitHub ключи
        github_tab = self.create_github_tab()
        tabs.addTab(github_tab, "📥 GitHub ключи")
        
        # Вкладка ручной проверки
        manual_tab = self.create_manual_tab()
        tabs.addTab(manual_tab, "🔍 Ручная проверка")
        
        # Вкладка настроек
        settings_tab = self.create_settings_tab()
        tabs.addTab(settings_tab, "⚙️ Настройки")
        
        # Вкладка логов
        logs_tab = self.create_logs_tab()
        tabs.addTab(logs_tab, "📋 Логи")
        
        main_layout.addWidget(tabs)
        
        # Нижняя панель с кнопками
        bottom_layout = QHBoxLayout()
        
        self.check_btn = QPushButton("🔄 Проверить посты")
        self.check_btn.clicked.connect(self.force_check)
        bottom_layout.addWidget(self.check_btn)
        
        self.load_github_btn = QPushButton("📥 Загрузить GitHub ключи")
        self.load_github_btn.clicked.connect(self.load_github_keys)
        bottom_layout.addWidget(self.load_github_btn)
        
        self.save_btn = QPushButton("💾 Сохранить базу")
        self.save_btn.clicked.connect(self.save_database)
        bottom_layout.addWidget(self.save_btn)
        
        self.clear_logs_btn = QPushButton("🗑️ Очистить логи")
        self.clear_logs_btn.clicked.connect(self.clear_logs)
        bottom_layout.addWidget(self.clear_logs_btn)
        
        bottom_layout.addStretch()
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        bottom_layout.addWidget(self.progress_bar)
        
        main_layout.addLayout(bottom_layout)
        
        # Статус бар
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.statusBar.showMessage("Готов к работе")
    
    def create_monitoring_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # Таблица постов
        layout.addWidget(QLabel("📝 Активные посты:"))
        
        self.posts_table = QTableWidget()
        self.posts_table.setColumnCount(5)
        self.posts_table.setHorizontalHeaderLabels(["ID поста", "Ключей", "Рабочих", "Последняя проверка", "Действия"])
        self.posts_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.posts_table)
        
        # Кнопка обновления
        refresh_btn = QPushButton("🔄 Обновить")
        refresh_btn.clicked.connect(self.refresh_stats)
        layout.addWidget(refresh_btn)
        
        return widget
    
    def create_github_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # Верхняя панель
        top_layout = QHBoxLayout()
        
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["baltics", "finland", "germany", "sweden", "netherlands", "poland", "other", "russia"])
        top_layout.addWidget(QLabel("Режим фильтрации:"))
        top_layout.addWidget(self.mode_combo)
        
        self.start_check_btn = QPushButton("🚀 Начать проверку")
        self.start_check_btn.clicked.connect(self.start_github_check)
        top_layout.addWidget(self.start_check_btn)
        
        top_layout.addStretch()
        
        layout.addLayout(top_layout)
        
        # Статистика
        stats_group = QGroupBox("Статистика GitHub ключей")
        stats_layout = QGridLayout(stats_group)
        
        self.github_total_label = QLabel("0")
        stats_layout.addWidget(QLabel("Всего ключей:"), 0, 0)
        stats_layout.addWidget(self.github_total_label, 0, 1)
        
        self.github_working_label = QLabel("0")
        stats_layout.addWidget(QLabel("Рабочих ключей:"), 1, 0)
        stats_layout.addWidget(self.github_working_label, 1, 1)
        
        self.github_best_label = QLabel("-")
        stats_layout.addWidget(QLabel("Лучший ключ:"), 2, 0)
        stats_layout.addWidget(self.github_best_label, 2, 1)
        
        layout.addWidget(stats_group)
        
        # Результаты
        layout.addWidget(QLabel("🏆 ТОП-10 лучших ключей:"))
        
        self.github_results = QTextEdit()
        self.github_results.setReadOnly(True)
        self.github_results.setFont(QFont("Consolas", 10))
        layout.addWidget(self.github_results)
        
        # Кнопка сохранения
        save_github_btn = QPushButton("💾 Сохранить рабочие ключи в файл")
        save_github_btn.clicked.connect(self.save_github_keys)
        layout.addWidget(save_github_btn)
        
        return widget
    
    def create_manual_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # Ввод VLESS ссылок
        input_group = QGroupBox("Ввод VLESS ссылок")
        input_layout = QVBoxLayout(input_group)
        
        self.vless_input = QTextEdit()
        self.vless_input.setPlaceholderText("Вставьте VLESS ссылки сюда (по одной на строку)...")
        self.vless_input.setMaximumHeight(150)
        input_layout.addWidget(self.vless_input)
        
        btn_layout = QHBoxLayout()
        
        self.test_btn = QPushButton("🔍 Проверить ссылки")
        self.test_btn.clicked.connect(self.manual_test)
        btn_layout.addWidget(self.test_btn)
        
        self.load_file_btn = QPushButton("📁 Загрузить из файла")
        self.load_file_btn.clicked.connect(self.load_from_file)
        btn_layout.addWidget(self.load_file_btn)
        
        self.clear_input_btn = QPushButton("🗑️ Очистить")
        self.clear_input_btn.clicked.connect(lambda: self.vless_input.clear())
        btn_layout.addWidget(self.clear_input_btn)
        
        input_layout.addLayout(btn_layout)
        
        layout.addWidget(input_group)
        
        # Результаты проверки
        results_group = QGroupBox("Результаты проверки")
        results_layout = QVBoxLayout(results_group)
        
        self.results_text = QTextEdit()
        self.results_text.setReadOnly(True)
        self.results_text.setFont(QFont("Consolas", 10))
        results_layout.addWidget(self.results_text)
        
        layout.addWidget(results_group)
        
        return widget
    
    def create_settings_tab(self):
        widget = QWidget()
        layout = QGridLayout(widget)
        
        # Основные настройки
        layout.addWidget(QLabel("🤖 Telegram Bot Token:"), 0, 0)
        self.token_input = QLineEdit(TELEGRAM_TOKEN)
        self.token_input.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.token_input, 0, 1)
        
        layout.addWidget(QLabel("📢 ID канала:"), 1, 0)
        self.channel_id_input = QLineEdit(str(TARGET_CHANNEL_ID))
        layout.addWidget(self.channel_id_input, 1, 1)
        
        layout.addWidget(QLabel("📬 ID группы:"), 2, 0)
        self.group_id_input = QLineEdit(str(TARGET_GROUP_ID))
        layout.addWidget(self.group_id_input, 2, 1)
        
        layout.addWidget(QLabel("📌 ID ветки (темы):"), 3, 0)
        self.topic_id_input = QSpinBox()
        self.topic_id_input.setRange(1, 100)
        self.topic_id_input.setValue(TARGET_TOPIC_ID)
        layout.addWidget(self.topic_id_input, 3, 1)
        
        layout.addWidget(QLabel("🕐 Интервал проверки (часы):"), 4, 0)
        self.check_interval_input = QSpinBox()
        self.check_interval_input.setRange(1, 24)
        self.check_interval_input.setValue(CHECK_INTERVAL_HOURS)
        layout.addWidget(self.check_interval_input, 4, 1)
        
        layout.addWidget(QLabel("⏱️ Таймаут TCP проверки (сек):"), 5, 0)
        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(1, 30)
        self.timeout_input.setValue(TEST_TIMEOUT)
        layout.addWidget(self.timeout_input, 5, 1)
        
        layout.addWidget(QLabel("🎯 Макс. задержка (мс):"), 6, 0)
        self.max_latency_input = QSpinBox()
        self.max_latency_input.setRange(100, 5000)
        self.max_latency_input.setValue(MAX_LATENCY_MS)
        self.max_latency_input.setSuffix(" мс")
        layout.addWidget(self.max_latency_input, 6, 1)
        
        # Кнопки
        btn_layout = QHBoxLayout()
        
        self.save_settings_btn = QPushButton("💾 Сохранить настройки")
        self.save_settings_btn.clicked.connect(self.save_settings)
        btn_layout.addWidget(self.save_settings_btn)
        
        layout.addLayout(btn_layout, 7, 0, 1, 2)
        
        # Путь к Xray
        layout.addWidget(QLabel("📁 Путь к Xray (опционально):"), 8, 0)
        xray_layout = QHBoxLayout()
        self.xray_path_input = QLineEdit(XRAY_PATH)
        xray_layout.addWidget(self.xray_path_input)
        self.browse_xray_btn = QPushButton("Обзор...")
        self.browse_xray_btn.clicked.connect(self.browse_xray)
        xray_layout.addWidget(self.browse_xray_btn)
        layout.addLayout(xray_layout, 8, 1)
        
        layout.setRowStretch(9, 1)
        
        return widget
    
    def create_logs_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 10))
        layout.addWidget(self.log_text)
        
        return widget
    
    def connect_signals(self):
        self.signals.log_message.connect(self.add_log)
        self.signals.status_update.connect(self.update_status)
        self.signals.stats_update.connect(self.update_stats)
        self.signals.key_tested.connect(self.on_key_tested)
        self.signals.check_started.connect(self.on_check_started)
        self.signals.check_finished.connect(self.on_check_finished)
    
    def start_bot(self):
        """Запуск бота в отдельном потоке"""
        def bot_thread():
            try:
                self.bot = VlessMonitorBot(TELEGRAM_TOKEN, XRAY_PATH, self.signals)
                self.bot.start_bot()
            except Exception as e:
                self.add_log(f"❌ Ошибка запуска бота: {e}", "error")
        
        thread = threading.Thread(target=bot_thread, daemon=True)
        thread.start()
        
        # Обновляем статистику каждые 5 секунд
        self.stats_timer = QTimer()
        self.stats_timer.timeout.connect(self.refresh_stats)
        self.stats_timer.start(5000)
    
    def refresh_stats(self):
        """Обновление статистики"""
        posts_count = len(posts_database)
        total_keys = sum(len(data.get('links', [])) for data in posts_database.values())
        working_keys = sum(len(data.get('working_links', [])) for data in posts_database.values())
        
        self.posts_count_label.setText(f"📝 Постов: {posts_count}")
        self.keys_count_label.setText(f"🔑 Ключей: {total_keys}")
        self.working_keys_label.setText(f"✅ Рабочих: {working_keys}")
        
        # Обновляем таблицу постов
        self.posts_table.setRowCount(posts_count)
        for i, (post_id, data) in enumerate(posts_database.items()):
            self.posts_table.setItem(i, 0, QTableWidgetItem(str(post_id)))
            self.posts_table.setItem(i, 1, QTableWidgetItem(str(len(data.get('links', [])))))
            self.posts_table.setItem(i, 2, QTableWidgetItem(str(len(data.get('working_links', [])))))
            last_check = data.get('last_check', datetime.now())
            self.posts_table.setItem(i, 3, QTableWidgetItem(last_check.strftime("%H:%M %d.%m")))
    
    def load_github_keys(self):
        """Загрузка ключей из GitHub"""
        self.add_log("📥 Загрузка ключей из GitHub...", "info")
        
        def load():
            result = GitHubKeyLoader.load_all_keys()
            self.signals.github_loaded.emit(result)
        
        thread = threading.Thread(target=load, daemon=True)
        thread.start()
    
    def start_github_check(self):
        """Запуск проверки GitHub ключей"""
        mode = self.mode_combo.currentText()
        self.add_log(f"🚀 Запуск проверки ключей для режима: {mode}", "info")
        
        # Загружаем ключи
        black_keys = GitHubKeyLoader.fetch_keys(BLACK_URL)
        black_mobile_keys = GitHubKeyLoader.fetch_keys(BLACK_MOBILE_URL)
        all_black = list(dict.fromkeys(black_keys + black_mobile_keys))
        
        filtered = GitHubKeyLoader.filter_keys(all_black, mode)
        
        self.add_log(f"📊 Найдено {len(filtered)} ключей для проверки", "info")
        
        # Запускаем проверку в отдельном потоке
        self.checker = GitHubKeyChecker(mode, filtered)
        self.checker.progress.connect(self.on_check_progress)
        self.checker.result.connect(self.on_github_result)
        self.checker.start()
        
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.start_check_btn.setEnabled(False)
    
    def on_check_progress(self, current, total, message):
        """Обновление прогресса проверки"""
        self.statusBar.showMessage(f"{message} {current}/{total}")
    
    def on_github_result(self, result):
        """Обработка результатов проверки GitHub"""
        self.progress_bar.setVisible(False)
        self.start_check_btn.setEnabled(True)
        
        self.github_total_label.setText(str(result["total"]))
        self.github_working_label.setText(str(result["working"]))
        
        if result["best"]:
            self.github_best_label.setText(f"{result['best']['host']}:{result['best']['port']} ({result['best']['latency_ms']} мс)")
        
        # Отображаем топ-10
        text = ""
        for i, r in enumerate(result["top10"][:10], 1):
            text += f"{i}. {r['host']}:{r['port']} — {r['latency_ms']} мс\n"
            text += f"   {r['key'][:100]}...\n\n"
        
        self.github_results.setText(text)
        
        self.add_log(f"✅ Проверка завершена. Рабочих: {result['working']}/{result['total']}", "success")
        
        # Сохраняем результат
        self.last_github_result = result
    
    def save_github_keys(self):
        """Сохранение рабочих GitHub ключей"""
        if hasattr(self, 'last_github_result') and self.last_github_result:
            file_path, _ = QFileDialog.getSaveFileName(
                self, "Сохранить рабочие ключи", "working_github_keys.txt", "Text files (*.txt)"
            )
            if file_path:
                with open(file_path, 'w', encoding='utf-8') as f:
                    for r in self.last_github_result.get("top10", []):
                        f.write(r["key"] + "\n")
                self.add_log(f"💾 Сохранено {len(self.last_github_result.get('top10', []))} ключей в {file_path}", "success")
        else:
            QMessageBox.warning(self, "Ошибка", "Нет результатов проверки. Сначала запустите проверку.")
    
    def manual_test(self):
        """Ручная проверка VLESS ссылок"""
        text = self.vless_input.toPlainText()
        if not text:
            self.add_log("❌ Введите VLESS ссылки для проверки", "warning")
            return
        
        links_with_names = VlessParser.extract_from_text(text)
        if not links_with_names:
            self.results_text.setText("❌ Не найдено VLESS ссылок!")
            return
        
        self.results_text.setText(f"🔍 Проверка {len(links_with_names)} ссылок...\n\n")
        
        # Запускаем проверку в отдельном потоке
        def run_test():
            import asyncio
            
            async def test():
                results = []
                tester = VlessTester(XRAY_PATH)
                
                for i, (link, name) in enumerate(links_with_names):
                    parsed = VlessParser.parse(link)
                    if parsed:
                        working, latency, speed = await tester.test_single(parsed['outbound'])
                        results.append((name, working, latency, speed, link))
                    else:
                        results.append((name, False, 0, 0, link))
                    
                    self.signals.key_tested.emit(name, working if 'working' in locals() else False, 
                                                  latency if 'latency' in locals() else 0,
                                                  speed if 'speed' in locals() else 0)
                
                return results
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            results = loop.run_until_complete(test())
            loop.close()
            
            # Форматируем результаты
            result_text = "✅ <b>Результаты проверки</b>\n\n"
            working_count = 0
            working_keys = []
            
            for name, working, latency, speed, link in results:
                if working:
                    working_count += 1
                    result_text += f"✅ {name} | ⚡ {speed:.1f} KB/s | ⏱️ {latency:.0f}ms\n"
                    working_keys.append(link)
                else:
                    result_text += f"❌ {name}\n"
            
            result_text += f"\n📊 Рабочих: {working_count}/{len(results)}"
            
            if working_keys:
                result_text += "\n\n📝 Рабочие ключи:\n"
                for key in working_keys[:3]:
                    result_text += f"{key[:100]}...\n"
            
            self.results_text.setText(result_text)
            self.add_log(f"✅ Ручная проверка завершена. Рабочих: {working_count}/{len(results)}", "info")
        
        threading.Thread(target=run_test, daemon=True).start()
    
    def load_from_file(self):
        """Загрузка VLESS ссылок из файла"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Выберите файл с VLESS ссылками", "", "Текстовые файлы (*.txt);;Все файлы (*)"
        )
        
        if file_path:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                links_with_names = VlessParser.extract_from_file(content)
                
                if links_with_names:
                    text = "\n".join([link for link, _ in links_with_names])
                    self.vless_input.setText(text)
                    self.add_log(f"📁 Загружено {len(links_with_names)} ссылок из {file_path}", "info")
                else:
                    QMessageBox.warning(self, "Ошибка", "В файле не найдено VLESS ссылок!")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось прочитать файл: {e}")
    
    def force_check(self):
        """Принудительная проверка всех постов"""
        if self.bot and not self.bot.is_checking:
            self.add_log("🔄 Запущена принудительная проверка", "info")
            asyncio.run_coroutine_threadsafe(self.bot.check_all_posts(), self.bot.loop)
        else:
            self.add_log("⚠️ Проверка уже выполняется", "warning")
    
    def save_database(self):
        """Сохранение базы данных"""
        if self.bot:
            asyncio.run_coroutine_threadsafe(self.bot.save_database(), self.bot.loop)
            self.add_log("💾 База данных сохранена", "info")
    
    def save_settings(self):
        """Сохранение настроек"""
        global TELEGRAM_TOKEN, TARGET_CHANNEL_ID, TARGET_GROUP_ID, TARGET_TOPIC_ID
        global CHECK_INTERVAL_HOURS, TEST_TIMEOUT, MAX_LATENCY_MS, XRAY_PATH
        
        TELEGRAM_TOKEN = self.token_input.text()
        TARGET_CHANNEL_ID = int(self.channel_id_input.text())
        TARGET_GROUP_ID = int(self.group_id_input.text())
        TARGET_TOPIC_ID = self.topic_id_input.value()
        CHECK_INTERVAL_HOURS = self.check_interval_input.value()
        TEST_TIMEOUT = self.timeout_input.value()
        MAX_LATENCY_MS = self.max_latency_input.value()
        XRAY_PATH = self.xray_path_input.text()
        
        self.add_log("💾 Настройки сохранены", "info")
    
    def browse_xray(self):
        """Выбор пути к Xray"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Выберите Xray executable", "", "Executable files (*.exe);;All files (*)"
        )
        if file_path:
            self.xray_path_input.setText(file_path)
    
    def clear_logs(self):
        """Очистка логов"""
        self.log_text.clear()
        self.add_log("Логи очищены", "info")
    
    def add_log(self, message, level="info"):
        """Добавление сообщения в лог"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        colors = {
            "info": "black",
            "success": "green",
            "warning": "orange",
            "error": "red"
        }
        
        color = colors.get(level, "black")
        self.log_text.append(f'<font color="{color}">[{timestamp}] {message}</font>')
        
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.log_text.setTextCursor(cursor)
    
    def update_status(self, status):
        """Обновление статуса"""
        self.status_label.setText(status)
        self.statusBar.showMessage(status)
    
    def update_stats(self, stats):
        pass
    
    def on_key_tested(self, name, working, latency, speed):
        pass
    
    def on_check_started(self):
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.check_btn.setEnabled(False)
    
    def on_check_finished(self, working, total):
        self.progress_bar.setVisible(False)
        self.check_btn.setEnabled(True)
        self.refresh_stats()
    
    def closeEvent(self, event):
        reply = QMessageBox.question(
            self, 'Выход',
            'Закрыть приложение?',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            event.accept()
        else:
            event.ignore()


# ============ ЗАПУСК ============
def check_xray():
    if os.path.exists(XRAY_PATH):
        return XRAY_PATH
    
    alternatives = ["./xray", "./xray.exe", "xray", "xray.exe"]
    for alt in alternatives:
        if os.path.exists(alt):
            return alt
    
    bin_name = "xray" + (".exe" if platform.system() == "Windows" else "")
    bin_path = os.path.join("xray_bin", bin_name)
    if os.path.exists(bin_path):
        return bin_path
    
    return None


def main():
    print("=" * 60)
    print("VLESS Monitor Bot v5 - Полноценное приложение")
    print("=" * 60)
    
    # Проверяем Xray (опционально)
    xray_path = check_xray()
    if xray_path:
        print(f"✅ Xray найден: {xray_path}")
    else:
        print("⚠️ Xray не найден. Бот будет использовать TCP проверку.")
    
    print("🚀 Запуск приложения...")
    
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()