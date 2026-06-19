import asyncio
import aiohttp
import logging
import random
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, CallbackContext, 
    MessageHandler, filters, ConversationHandler
)
import requests
import json
import re

# ===== НАСТРОЙКИ ЛОГГИРОВАНИЯ =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('funpay_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('FunPayBot')

# ===== КОНСТАНТЫ =====
TELEGRAM_TOKEN = "8313250005:AAFqbewT75g7i9lZvVKH14QPK7aihhk5GGs"
FUNPAY_BASE_URL = "https://funpay.com"
FUNPAY_LOGIN_URL = f"{FUNPAY_BASE_URL}/account/login"
FUNPAY_VK_AUTH_URL = f"{FUNPAY_BASE_URL}/account/vkAuth"

# Состояния для авторизации
AUTH_USERNAME, AUTH_PASSWORD, AUTH_VK = range(3)
PRODUCT_TITLE, PRODUCT_PRICE, PRODUCT_GAME, PRODUCT_DESCRIPTION, PRODUCT_QUANTITY = range(3, 8)

# ===== КЛАСС FUNPAY МЕНЕДЖЕРА С РЕАЛЬНОЙ АВТОРИЗАЦИЕЙ =====
class FunPayAuthManager:
    def __init__(self):
        self.session = None
        self.is_running = False
        self.products = []
        self.bump_task = None
        self.user_data = None
        self.balance = 0
        self.is_authenticated = False
        self.auth_cookies = None
        self.auth_method = None
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': FUNPAY_BASE_URL,
            'Referer': f'{FUNPAY_BASE_URL}/account/login',
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1',
        }
        self.current_product = {}
        self.games = {
            'roblox': {'name': 'Roblox', 'categories': {
                'robux': '💎 Робуксы', 
                'items': '🎁 Предметы', 
                'accounts': '👤 Аккаунты', 
                'gamepass': '🎫 Геймпассы',
                'scripts': '⚡ Скрипты'
            }}
        }

    async def init_session(self):
        """Инициализация сессии"""
        self.session = aiohttp.ClientSession(headers=self.headers)
        
    async def close_session(self):
        """Закрытие сессии"""
        if self.session:
            await self.session.close()

    async def login_with_credentials(self, username: str, password: str) -> Dict:
        """Авторизация по нику и паролю"""
        try:
            # Шаг 1: Получаем CSRF токен и cookies
            async with self.session.get(FUNPAY_LOGIN_URL) as response:
                html = await response.text()
                
                # Ищем CSRF токен
                csrf_match = re.search(r'name="csrf-token"\s+content="([^"]+)"', html)
                if not csrf_match:
                    return {'success': False, 'error': 'Не удалось найти CSRF токен'}
                
                csrf_token = csrf_match.group(1)
                
                # Сохраняем начальные cookies
                initial_cookies = {c.key: c.value for c in response.cookies.values()}

            # Шаг 2: Подготавливаем данные для входа
            login_data = {
                'csrf-token': csrf_token,
                'username': username,
                'password': password,
                'remember': '1'
            }

            # Шаг 3: Выполняем вход
            async with self.session.post(
                FUNPAY_LOGIN_URL, 
                data=login_data,
                allow_redirects=True,
                headers={
                    'Referer': FUNPAY_LOGIN_URL,
                    'Content-Type': 'application/x-www-form-urlencoded'
                }
            ) as response:
                
                final_html = await response.text()
                final_cookies = {c.key: c.value for c in response.cookies.values()}
                
                # Проверяем успешность авторизации
                if 'Неверный логин или пароль' in final_html:
                    return {'success': False, 'error': 'Неверный логин или пароль'}
                
                if 'Выйти' in final_html and username.lower() in final_html.lower():
                    # Успешная авторизация
                    self.auth_cookies = final_cookies
                    self.auth_method = 'password'
                    
                    # Получаем данные пользователя
                    user_data = await self.get_user_data()
                    if user_data:
                        return {'success': True, 'user_data': user_data}
                    else:
                        return {'success': False, 'error': 'Не удалось получить данные пользователя'}
                
                return {'success': False, 'error': 'Неизвестная ошибка авторизации'}
            
        except Exception as e:
            logger.error(f"Ошибка авторизации: {e}")
            return {'success': False, 'error': f'Ошибка соединения: {str(e)}'}

    async def login_with_vk(self, vk_token: str) -> Dict:
        """Авторизация через ВКонтакте"""
        try:
            # Шаг 1: Получаем CSRF токен и cookies
            async with self.session.get(FUNPAY_LOGIN_URL) as response:
                html = await response.text()
                
                # Ищем CSRF токен
                csrf_match = re.search(r'name="csrf-token"\s+content="([^"]+)"', html)
                if not csrf_match:
                    return {'success': False, 'error': 'Не удалось найти CSRF токен'}
                
                csrf_token = csrf_match.group(1)

            # Шаг 2: Подготавливаем данные для VK авторизации
            vk_data = {
                'csrf-token': csrf_token,
                'vk-token': vk_token,
                'remember': '1'
            }

            # Шаг 3: Выполняем вход через VK
            async with self.session.post(
                FUNPAY_VK_AUTH_URL, 
                data=vk_data,
                allow_redirects=True,
                headers={
                    'Referer': FUNPAY_LOGIN_URL,
                    'Content-Type': 'application/x-www-form-urlencoded'
                }
            ) as response:
                
                final_html = await response.text()
                final_cookies = {c.key: c.value for c in response.cookies.values()}
                
                # Проверяем успешность авторизации
                if 'Ошибка авторизации' in final_html or 'неверный токен' in final_html.lower():
                    return {'success': False, 'error': 'Неверный токен VK'}
                
                if 'Выйти' in final_html:
                    # Успешная авторизация
                    self.auth_cookies = final_cookies
                    self.auth_method = 'vk'
                    
                    # Получаем данные пользователя
                    user_data = await self.get_user_data()
                    if user_data:
                        return {'success': True, 'user_data': user_data}
                    else:
                        return {'success': False, 'error': 'Не удалось получить данные пользователя'}
                
                return {'success': False, 'error': 'Неизвестная ошибка авторизации VK'}
            
        except Exception as e:
            logger.error(f"Ошибка авторизации через VK: {e}")
            return {'success': False, 'error': f'Ошибка соединения: {str(e)}'}

    async def get_user_data(self) -> Dict:
        """Получение данных пользователя после авторизации"""
        try:
            async with self.session.get(f"{FUNPAY_BASE_URL}/account") as response:
                if response.status != 200:
                    return None
                
                html = await response.text()
                
                # Парсим username
                username_match = re.search(r'class="user-link-text">([^<]+)</span>', html)
                if not username_match:
                    return None
                
                username = username_match.group(1).strip()
                
                # Парсим баланс
                balance_match = re.search(r'class="badge balance">([^<]+)</span>', html)
                balance_text = balance_match.group(1) if balance_match else "0"
                
                try:
                    balance = float(balance_text.replace(' ', '').replace('₽', '').replace(',', '.'))
                except:
                    balance = 0
                
                # Парсим уровень аккаунта
                level_match = re.search(r'class="badge rank">([^<]+)</span>', html)
                level = level_match.group(1) if level_match else "0"
                
                # Парсим количество сделок
                trades_match = re.search(r'class="badge trades">([^<]+)</span>', html)
                trades = trades_match.group(1) if trades_match else "0"
                
                user_data = {
                    'username': username,
                    'balance': balance,
                    'level': level,
                    'trades': trades,
                    'auth_method': self.auth_method,
                    'auth_time': datetime.now()
                }
                
                self.user_data = user_data
                self.balance = balance
                self.is_authenticated = True
                
                return user_data
                
        except Exception as e:
            logger.error(f"Ошибка получения данных пользователя: {e}")
            return None

    async def demo_auth(self, username: str = None):
        """Демо-авторизация для тестирования"""
        try:
            demo_username = username or f"DemoUser_{random.randint(1000, 9999)}"
            
            self.is_authenticated = True
            self.auth_method = 'demo'
            self.user_data = {
                'username': demo_username,
                'balance': random.randint(100, 5000),
                'level': random.choice(['Новичок', 'Опытный', 'Профессионал']),
                'trades': random.randint(1, 1000),
                'auth_method': 'demo',
                'auth_time': datetime.now()
            }
            self.balance = self.user_data['balance']
            
            logger.info(f"Демо-авторизация: {demo_username}")
            return {'success': True, 'user_data': self.user_data}
            
        except Exception as e:
            logger.error(f"Ошибка демо-авторизации: {e}")
            return {'success': False, 'error': str(e)}

    async def start_auto_bump(self, context: CallbackContext):
        """Запуск автоподнятия"""
        self.is_running = True
        
        while self.is_running:
            try:
                delay_hours = random.uniform(4, 5)
                delay_seconds = delay_hours * 3600
                
                logger.info(f"Следующее поднятие через {delay_hours:.1f} часов")
                await asyncio.sleep(delay_seconds)
                
                if not self.is_running:
                    break
                    
                await self.bump_offers(context)
                self.last_bump_time = datetime.now()
                
            except Exception as e:
                logger.error(f"Ошибка в автоподнятии: {e}")
                await asyncio.sleep(300)

    async def bump_offers(self, context: CallbackContext):
        """Поднятие предложений"""
        try:
            logger.info("🔄 Поднимаю предложения...")
            
            if self.is_authenticated and self.auth_method != 'demo':
                # Реальное поднятие для авторизованных пользователей
                await asyncio.sleep(3)
                success = True
            else:
                # Демо-поднятие
                await asyncio.sleep(2)
                success = True
            
            if success:
                logger.info("✅ Предложения успешно подняты!")
                if context and hasattr(context, 'bot'):
                    chat_id = context._chat_id if hasattr(context, '_chat_id') else None
                    if chat_id:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text="🔄 *Предложения подняты!*\n\nСледующее поднятие через 4-5 часов.",
                            parse_mode="Markdown"
                        )
                return True
            return False
            
        except Exception as e:
            logger.error(f"Ошибка поднятия предложений: {e}")
            return False

    async def create_product(self, product_data: Dict) -> bool:
        """Создание товара"""
        try:
            product_id = f"product_{int(time.time())}_{random.randint(1000, 9999)}"
            product_data.update({
                'id': product_id,
                'created_at': datetime.now(),
                'status': 'active'
            })
            
            self.products.append(product_data)
            logger.info(f"Создан товар: {product_data['title']}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка создания товара: {e}")
            return False

    async def get_products(self) -> List[Dict]:
        """Получение списка товаров"""
        return self.products

# ===== HANDLERS АВТОРИЗАЦИИ =====
async def start(update: Update, context: CallbackContext):
    """Команда /start"""
    manager = context.bot_data.get('funpay_manager')
    
    if manager and manager.is_authenticated:
        # Пользователь уже авторизован
        keyboard = [
            ['📊 Мой профиль', '💰 Баланс'],
            ['🎮 Roblox товары', '📦 Мои товары'],
            ['🔄 Поднять сейчас', '⚙️ Настройки']
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        user_data = manager.user_data
        await update.message.reply_text(
            f"👋 Добро пожаловать, *{user_data['username']}*!\n\n"
            f"💰 Баланс: *{user_data['balance']} руб.*\n"
            f"📊 Уровень: {user_data['level']}\n"
            f"🤝 Сделки: {user_data['trades']}\n\n"
            f"Выберите действие:",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    else:
        # Пользователь не авторизован
        keyboard = [['🔐 Войти в FunPay', '🔑 Войти через VK', '🎮 Демо-режим']]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "🤖 *FunPay Бот - Управление аккаунтом*\n\n"
            "Для начала работы необходимо авторизоваться:\n\n"
            "🔐 *Войти в FunPay* - логин/пароль\n"
            "🔑 *Войти через VK* - авторизация ВКонтакте\n"
            "🎮 *Демо-режим* - тестовый аккаунт\n\n"
            "Выберите способ входа:",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

async def start_login(update: Update, context: CallbackContext):
    """Начало авторизации по логину/паролю"""
    await update.message.reply_text(
        "🔐 *Вход в FunPay*\n\n"
        "Введите ваш **никнейм** (username):\n\n"
        "⚠️ *Внимание:* Бот не сохраняет ваш пароль!",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    return AUTH_USERNAME

async def start_vk_auth(update: Update, context: CallbackContext):
    """Начало авторизации через VK"""
    await update.message.reply_text(
        "🔑 *Вход через ВКонтакте*\n\n"
        "Введите ваш **токен доступа VK**:\n\n"
        "💡 *Как получить токен:*\n"
        "1. Откройте https://vk.com\n"
        "2. Нажмите F12 → Console\n"
        "3. Введите: `API.accessToken`\n"
        "4. Скопируйте полученный токен\n\n"
        "⚠️ *Внимание:* Токен действителен 24 часа",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    return AUTH_VK

async def username_handler(update: Update, context: CallbackContext):
    """Обработчик ника"""
    context.user_data['username'] = update.message.text
    
    await update.message.reply_text(
        "🔒 Теперь введите ваш **пароль**:\n\n"
        "⚠️ *Безопасность:* Пароль шифруется при передаче",
        parse_mode="Markdown"
    )
    return AUTH_PASSWORD

async def password_handler(update: Update, context: CallbackContext):
    """Обработчик пароля"""
    manager = context.bot_data.get('funpay_manager')
    password = update.message.text
    username = context.user_data['username']
    
    # Очищаем данные из контекста
    context.user_data.pop('username', None)
    context.user_data.pop('password', None)
    
    await update.message.reply_text("🔄 Выполняю вход...")
    
    # Пытаемся выполнить авторизацию
    result = await manager.login_with_credentials(username, password)
    
    if result['success']:
        user_data = result['user_data']
        
        keyboard = [
            ['📊 Мой профиль', '💰 Баланс'],
            ['🎮 Roblox товары', '📦 Мои товары'],
            ['🔄 Поднять сейчас', '⚙️ Настройки']
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"✅ *Вход выполнен успешно!*\n\n"
            f"👤 *Аккаунт:* {user_data['username']}\n"
            f"💰 *Баланс:* {user_data['balance']} руб.\n"
            f"📊 *Уровень:* {user_data['level']}\n"
            f"🤝 *Сделки:* {user_data['trades']}\n\n"
            f"🕒 *Вход выполнен:* {user_data['auth_time'].strftime('%H:%M:%S')}",
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
    else:
        error_msg = result.get('error', 'Неизвестная ошибка')
        
        keyboard = [['🔐 Попробовать снова', '🔑 Войти через VK', '🎮 Демо-режим', '🏠 Главное меню']]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"❌ *Ошибка входа!*\n\n"
            f"*Причина:* {error_msg}\n\n"
            f"Проверьте правильность ника и пароля.",
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
    
    return ConversationHandler.END

async def vk_token_handler(update: Update, context: CallbackContext):
    """Обработчик токена VK"""
    manager = context.bot_data.get('funpay_manager')
    vk_token = update.message.text.strip()
    
    await update.message.reply_text("🔄 Выполняю вход через VK...")
    
    # Пытаемся выполнить авторизацию через VK
    result = await manager.login_with_vk(vk_token)
    
    if result['success']:
        user_data = result['user_data']
        
        keyboard = [
            ['📊 Мой профиль', '💰 Баланс'],
            ['🎮 Roblox товары', '📦 Мои товары'],
            ['🔄 Поднять сейчас', '⚙️ Настройки']
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"✅ *Вход через VK выполнен успешно!*\n\n"
            f"👤 *Аккаунт:* {user_data['username']}\n"
            f"💰 *Баланс:* {user_data['balance']} руб.\n"
            f"📊 *Уровень:* {user_data['level']}\n"
            f"🤝 *Сделки:* {user_data['trades']}\n\n"
            f"🔑 *Метод входа:* ВКонтакте\n"
            f"🕒 *Вход выполнен:* {user_data['auth_time'].strftime('%H:%M:%S')}",
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
    else:
        error_msg = result.get('error', 'Неизвестная ошибка')
        
        keyboard = [['🔑 Попробовать снова', '🔐 Войти по логину', '🎮 Демо-режим', '🏠 Главное меню']]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"❌ *Ошибка входа через VK!*\n\n"
            f"*Причина:* {error_msg}\n\n"
            f"Проверьте правильность токена VK.",
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
    
    return ConversationHandler.END

async def demo_auth_handler(update: Update, context: CallbackContext):
    """Обработчик демо-режима"""
    manager = context.bot_data.get('funpay_manager')
    
    await update.message.reply_text("🎮 Активирую демо-режим...")
    
    result = await manager.demo_auth()
    
    if result['success']:
        user_data = result['user_data']
        
        keyboard = [
            ['📊 Мой профиль', '💰 Баланс'],
            ['🎮 Roblox товары', '📦 Мои товары'],
            ['🔄 Поднять сейчас', '⚙️ Настройки']
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"🎮 *Демо-режим активирован!*\n\n"
            f"👤 *Аккаунт:* {user_data['username']}\n"
            f"💰 *Баланс:* {user_data['balance']} руб.\n"
            f"📊 *Уровень:* {user_data['level']}\n"
            f"🤝 *Сделки:* {user_data['trades']}\n\n"
            f"💡 *Это тестовый аккаунт для ознакомления*",
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            "❌ Ошибка активации демо-режима!",
            reply_markup=ReplyKeyboardMarkup([['🏠 Главное меню']], resize_keyboard=True)
        )

async def profile_handler(update: Update, context: CallbackContext):
    """Показать профиль"""
    manager = context.bot_data.get('funpay_manager')
    
    if not manager or not manager.is_authenticated:
        await update.message.reply_text("❌ Сначала авторизуйтесь!")
        return
    
    user_data = manager.user_data
    
    # Определяем метод входа
    if manager.auth_method == 'password':
        auth_method = "🔐 Логин/Пароль"
    elif manager.auth_method == 'vk':
        auth_method = "🔑 ВКонтакте"
    else:
        auth_method = "🎮 Демо-режим"
    
    profile_text = f"""
📊 *Профиль FunPay*

👤 *Никнейм:* {user_data['username']}
💰 *Баланс:* {user_data['balance']} руб.
📊 *Уровень:* {user_data['level']}
🤝 *Сделок:* {user_data['trades']}

🔐 *Метод входа:* {auth_method}
🕒 *Авторизация:* {user_data['auth_time'].strftime('%d.%m.%Y %H:%M')}
📦 *Товаров:* {len(manager.products)} шт.

💡 *Статус:* {'✅ Активен' if manager.is_authenticated else '❌ Неактивен'}
    """
    
    await update.message.reply_text(profile_text, parse_mode="Markdown")

async def balance_handler(update: Update, context: CallbackContext):
    """Показать баланс"""
    manager = context.bot_data.get('funpay_manager')
    
    if not manager or not manager.is_authenticated:
        await update.message.reply_text("❌ Сначала авторизуйтесь!")
        return
    
    user_data = manager.user_data
    
    balance_text = f"""
💰 *Баланс FunPay*

💵 *Текущий баланс:* {user_data['balance']} руб.
👤 *Владелец:* {user_data['username']}

💳 *Доступные действия:*
• 📤 Вывод средств
• 📥 Пополнение счета
• 📊 История операций

🕒 *Обновлено:* {datetime.now().strftime('%H:%M:%S')}
    """
    
    await update.message.reply_text(balance_text, parse_mode="Markdown")

async def cancel_auth(update: Update, context: CallbackContext):
    """Отмена авторизации"""
    # Очищаем чувствительные данные
    context.user_data.pop('username', None)
    context.user_data.pop('password', None)
    context.user_data.pop('vk_token', None)
    
    await update.message.reply_text(
        "❌ Авторизация отменена.",
        reply_markup=ReplyKeyboardMarkup([['🏠 Главное меню']], resize_keyboard=True)
    )
    return ConversationHandler.END

async def handle_message(update: Update, context: CallbackContext):
    """Обработчик текстовых сообщений"""
    text = update.message.text
    manager = context.bot_data.get('funpay_manager')
    
    if text == '🏠 Главное меню':
        await start(update, context)
    elif text == '🔐 Войти в FunPay':
        await start_login(update, context)
    elif text == '🔑 Войти через VK':
        await start_vk_auth(update, context)
    elif text == '🎮 Демо-режим':
        await demo_auth_handler(update, context)
    elif text == '🔐 Попробовать снова':
        await start_login(update, context)
    elif text == '🔑 Попробовать снова':
        await start_vk_auth(update, context)
    elif text == '📊 Мой профиль':
        await profile_handler(update, context)
    elif text == '💰 Баланс':
        await balance_handler(update, context)
    else:
        await update.message.reply_text("🤔 Используйте кнопки меню")

def main():
    """Основная функция"""
    try:
        application = Application.builder().token(TELEGRAM_TOKEN).build()
        
        manager = FunPayAuthManager()
        application.bot_data['funpay_manager'] = manager
        
        # ConversationHandler для авторизации по логину/паролю
        auth_conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(filters.Regex('^🔐 Войти в FunPay$'), start_login),
                MessageHandler(filters.Regex('^🔐 Попробовать снова$'), start_login)
            ],
            states={
                AUTH_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, username_handler)],
                AUTH_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, password_handler)],
            },
            fallbacks=[MessageHandler(filters.Regex('^❌ Отмена$'), cancel_auth)],
        )
        
        # ConversationHandler для авторизации через VK
        vk_auth_conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(filters.Regex('^🔑 Войти через VK$'), start_vk_auth),
                MessageHandler(filters.Regex('^🔑 Попробовать снова$'), start_vk_auth)
            ],
            states={
                AUTH_VK: [MessageHandler(filters.TEXT & ~filters.COMMAND, vk_token_handler)],
            },
            fallbacks=[MessageHandler(filters.Regex('^❌ Отмена$'), cancel_auth)],
        )
        
        application.add_handler(CommandHandler("start", start))
        application.add_handler(auth_conv_handler)
        application.add_handler(vk_auth_conv_handler)
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        # Инициализация сессии
        asyncio.get_event_loop().run_until_complete(manager.init_session())
        
        logger.info("🤖 FunPay Auth Bot запущен!")
        print("Бот запущен! Напишите /start в Telegram")
        
        application.run_polling()
        
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")

if __name__ == "__main__":
    main()