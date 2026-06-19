import asyncio
import aiohttp
import logging
import random
import time
import hashlib
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackContext, 
    MessageHandler, filters, ConversationHandler,
    CallbackQueryHandler
)
import sqlite3
from decimal import Decimal, ROUND_DOWN
import math

# ===== КОНФИГУРАЦИЯ =====
TELEGRAM_TOKEN = "8252842027:AAHaNtvromtodItSOpX1788RTa4OOO6Izl4"

# ===== БАЗА ДАННЫХ =====
class ScamCoinDatabase:
    def __init__(self):
        self.conn = sqlite3.connect('scam_coins.db', check_same_thread=False)
        self.create_tables()
    
    def create_tables(self):
        cursor = self.conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_coins (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                coin_name TEXT,
                coin_symbol TEXT UNIQUE,
                initial_price DECIMAL(20,10),
                total_supply DECIMAL(30,8),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT TRUE
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS spot_balances (
                user_id INTEGER,
                symbol TEXT,
                balance DECIMAL(30,8),
                PRIMARY KEY (user_id, symbol)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS binary_options (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                symbol TEXT,
                option_type TEXT,
                amount DECIMAL(20,8),
                open_price DECIMAL(20,10),
                expiry_time TIMESTAMP,
                is_completed BOOLEAN DEFAULT FALSE,
                is_win BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                api_key TEXT UNIQUE
            )
        ''')
        
        # Инициализируем стартовый баланс
        cursor.execute('''
            INSERT OR IGNORE INTO spot_balances (user_id, symbol, balance)
            VALUES (0, 'USDT', 1000)
        ''')
        
        self.conn.commit()
    
    def create_coin(self, user_id: int, name: str, symbol: str, initial_price: Decimal, supply: Decimal) -> bool:
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO user_coins (user_id, coin_name, coin_symbol, initial_price, total_supply)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, name, symbol, str(initial_price), str(supply)))
            self.conn.commit()
            return True
        except:
            return False
    
    def get_user_coins(self, user_id: int) -> List[Dict]:
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT * FROM user_coins WHERE user_id = ? AND is_active = TRUE
        ''', (user_id,))
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    
    def get_spot_balance(self, user_id: int, symbol: str = "USDT") -> Decimal:
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT balance FROM spot_balances WHERE user_id = ? AND symbol = ?
        ''', (user_id, symbol))
        result = cursor.fetchone()
        if result:
            return Decimal(str(result[0]))
        else:
            # Создаем запись с начальным балансом
            initial_balance = Decimal('1000') if symbol == "USDT" else Decimal('0')
            cursor.execute('''
                INSERT INTO spot_balances (user_id, symbol, balance)
                VALUES (?, ?, ?)
            ''', (user_id, symbol, str(initial_balance)))
            self.conn.commit()
            return initial_balance
    
    def update_spot_balance(self, user_id: int, symbol: str, new_balance: Decimal):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO spot_balances (user_id, symbol, balance)
            VALUES (?, ?, ?)
        ''', (user_id, symbol, str(new_balance)))
        self.conn.commit()

    def create_binary_option(self, user_id: int, symbol: str, option_type: str, 
                           amount: Decimal, open_price: Decimal, expiry_minutes: int) -> int:
        cursor = self.conn.cursor()
        expiry_time = datetime.now() + timedelta(minutes=expiry_minutes)
        
        cursor.execute('''
            INSERT INTO binary_options (user_id, symbol, option_type, amount, open_price, expiry_time)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, symbol, option_type, str(amount), str(open_price), expiry_time))
        
        option_id = cursor.lastrowid
        self.conn.commit()
        return option_id

    def get_active_options(self, user_id: int) -> List[Dict]:
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT * FROM binary_options 
            WHERE user_id = ? AND is_completed = FALSE AND expiry_time > CURRENT_TIMESTAMP
        ''', (user_id,))
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def complete_option(self, option_id: int, is_win: bool):
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE binary_options 
            SET is_completed = TRUE, is_win = ?
            WHERE id = ?
        ''', (is_win, option_id))
        self.conn.commit()

    def get_user_api_key(self, user_id: int) -> str:
        cursor = self.conn.cursor()
        cursor.execute('SELECT api_key FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        
        if result and result[0]:
            return result[0]
        else:
            # Генерируем новый API ключ
            api_key = hashlib.sha256(f"{user_id}{datetime.now()}{random.random()}".encode()).hexdigest()[:32]
            cursor.execute('''
                INSERT OR REPLACE INTO users (user_id, api_key)
                VALUES (?, ?)
            ''', (user_id, api_key))
            self.conn.commit()
            return api_key

# ===== СТАНДАРТНЫЕ МОНЕТЫ =====
SCAM_COINS = {
    "MOONSHOT": {"name": "MoonShot Coin", "base_price": Decimal('0.0005'), "volatility": Decimal('0.12')},
    "LAMBO": {"name": "Lambo Token", "base_price": Decimal('0.002'), "volatility": Decimal('0.15')},
    "RICH": {"name": "Rich Quick", "base_price": Decimal('0.0001'), "volatility": Decimal('0.20')},
    "SCAM": {"name": "Scam Coin", "base_price": Decimal('0.00001'), "volatility": Decimal('0.25')},
    "PUMP": {"name": "Pump & Dump", "base_price": Decimal('0.005'), "volatility": Decimal('0.30')},
    "DUMP": {"name": "Dump & Pump", "base_price": Decimal('0.0002'), "volatility": Decimal('0.35')},
    "RUG": {"name": "Rug Pull", "base_price": Decimal('0.00005'), "volatility": Decimal('0.40')}
}

# ===== СОСТОЯНИЯ =====
COIN_NAME, COIN_SYMBOL, COIN_PRICE, COIN_SUPPLY = range(4)
BINARY_SYMBOL, BINARY_TYPE, BINARY_AMOUNT, BINARY_DURATION = range(4, 8)

# ===== ОСНОВНОЙ БОТ =====
class UltimateScamExchangeBot:
    def __init__(self):
        self.db = ScamCoinDatabase()
        self.user_coin_creation = {}
        self.user_binary_creation = {}
        
    def get_main_keyboard(self):
        keyboard = [
            ["📈 Открыть График", "💸 Быстрая Торговля"],
            ["🎯 Фьючерсы 100-1000x", "💰 Спот Торговля"],
            ["🪙 Создать Монету", "📊 Мои Монеты"],
            ["🏦 Пополнение/Вывод", "👤 Профиль"],
            ["📚 Помощь", "🔥 Топ Ликвидаций"],
            ["📊 Бинарные Опционы", "🔑 API Ключ"]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    def get_spot_trading_keyboard(self):
        keyboard = [
            ["🪙 MOONSHOT", "🪙 LAMBO", "🪙 RICH"],
            ["🪙 SCAM", "🪙 PUMP", "🪙 DUMP"],
            ["🪙 RUG", "📊 Спот Баланс", "🏠 Главная"]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    def get_binary_options_keyboard(self):
        keyboard = [
            ["📈 CALL опцион", "📉 PUT опцион"],
            ["📊 Мои опционы", "🔙 Назад"]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    def get_trade_keyboard(self, symbol: str):
        keyboard = [
            [InlineKeyboardButton("⚡ Купить", callback_data=f"buy_{symbol}")],
            [InlineKeyboardButton("💸 Продать", callback_data=f"sell_{symbol}")],
            [InlineKeyboardButton("📈 График", callback_data=f"chart_{symbol}")],
            [InlineKeyboardButton("📊 Бинарный опцион", callback_data=f"binary_{symbol}")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_spot")]
        ]
        return InlineKeyboardMarkup(keyboard)

# ===== ТОРГОВЛЯ =====
class SpotTrading:
    def __init__(self, db):
        self.db = db
        self.price_history = {}  # Для хранения истории цен
    
    def get_current_price(self, symbol: str) -> Decimal:
        if symbol in SCAM_COINS:
            base_price = SCAM_COINS[symbol]["base_price"]
            volatility = SCAM_COINS[symbol]["volatility"]
            # Генерируем случайную цену на основе волатильности
            change = random.uniform(-float(volatility), float(volatility))
            price = base_price * (Decimal('1') + Decimal(str(change)))
            
            # Сохраняем историю цен
            if symbol not in self.price_history:
                self.price_history[symbol] = []
            self.price_history[symbol].append({
                'timestamp': datetime.now(),
                'price': float(price)
            })
            # Ограничиваем историю последними 100 точками
            if len(self.price_history[symbol]) > 100:
                self.price_history[symbol] = self.price_history[symbol][-100:]
            
            return price
        return Decimal('0.0001')
    
    def get_price_history(self, symbol: str, limit: int = 50) -> List[Dict]:
        if symbol in self.price_history:
            return self.price_history[symbol][-limit:]
        return []
    
    async def execute_spot_buy(self, user_id: int, symbol: str, amount: Decimal) -> Dict:
        try:
            price = self.get_current_price(symbol)
            total_cost = amount * price
            usdt_balance = self.db.get_spot_balance(user_id, "USDT")
            
            if usdt_balance >= total_cost:
                new_usdt_balance = usdt_balance - total_cost
                coin_balance = self.db.get_spot_balance(user_id, symbol) + amount
                
                self.db.update_spot_balance(user_id, "USDT", new_usdt_balance)
                self.db.update_spot_balance(user_id, symbol, coin_balance)
                
                return {
                    "success": True,
                    "message": f"✅ Куплено {amount} {symbol} за {total_cost:.8f} USDT\n💰 Текущая цена: {price:.8f} USDT",
                    "price": price
                }
            else:
                return {
                    "success": False,
                    "message": f"❌ Недостаточно USDT. Нужно: {total_cost:.8f}, есть: {usdt_balance:.2f}"
                }
                
        except Exception as e:
            return {"success": False, "message": f"❌ Ошибка: {str(e)}"}
    
    async def execute_spot_sell(self, user_id: int, symbol: str, amount: Decimal) -> Dict:
        try:
            price = self.get_current_price(symbol)
            coin_balance = self.db.get_spot_balance(user_id, symbol)
            
            if coin_balance >= amount:
                total_revenue = amount * price
                new_coin_balance = coin_balance - amount
                new_usdt_balance = self.db.get_spot_balance(user_id, "USDT") + total_revenue
                
                self.db.update_spot_balance(user_id, symbol, new_coin_balance)
                self.db.update_spot_balance(user_id, "USDT", new_usdt_balance)
                
                return {
                    "success": True,
                    "message": f"✅ Продано {amount} {symbol} за {total_revenue:.8f} USDT\n💰 Текущая цена: {price:.8f} USDT",
                    "price": price
                }
            else:
                return {
                    "success": False,
                    "message": f"❌ Недостаточно {symbol}. Нужно: {amount}, есть: {coin_balance}"
                }
                
        except Exception as e:
            return {"success": False, "message": f"❌ Ошибка: {str(e)}"}

class BinaryOptionsTrading:
    def __init__(self, db, spot_trading):
        self.db = db
        self.spot_trading = spot_trading
    
    async def create_binary_option(self, user_id: int, symbol: str, option_type: str, 
                                 amount: Decimal, duration: int) -> Dict:
        try:
            current_price = self.spot_trading.get_current_price(symbol)
            usdt_balance = self.db.get_spot_balance(user_id, "USDT")
            
            if usdt_balance < amount:
                return {
                    "success": False,
                    "message": f"❌ Недостаточно USDT. Нужно: {amount}, есть: {usdt_balance:.2f}"
                }
            
            # Списываем сумму опциона
            new_usdt_balance = usdt_balance - amount
            self.db.update_spot_balance(user_id, "USDT", new_usdt_balance)
            
            # Создаем опцион
            option_id = self.db.create_binary_option(
                user_id, symbol, option_type, amount, current_price, duration
            )
            
            expiry_time = datetime.now() + timedelta(minutes=duration)
            
            return {
                "success": True,
                "message": f"✅ Бинарный опцион открыт!\n\n"
                          f"🪙 Актив: {symbol}\n"
                          f"📈 Тип: {option_type}\n"
                          f"💰 Сумма: {amount} USDT\n"
                          f"⏰ Экспирация: {expiry_time.strftime('%H:%M:%S')}\n"
                          f"🎯 Цена открытия: {current_price:.8f} USDT",
                "option_id": option_id
            }
            
        except Exception as e:
            return {"success": False, "message": f"❌ Ошибка: {str(e)}"}
    
    async def check_expired_options(self):
        """Проверяет и завершает истекшие опционы"""
        # Эта функция должна вызываться периодически
        pass

# ===== КОМАНДЫ =====
class UltimateBotCommands:
    def __init__(self, ultimate_bot):
        self.bot = ultimate_bot
        self.spot_trading = SpotTrading(ultimate_bot.db)
        self.binary_trading = BinaryOptionsTrading(ultimate_bot.db, self.spot_trading)
        self.waiting_for_trade_input = {}  # {user_id: {"action": "buy/sell", "symbol": "SYMBOL"}}
        self.waiting_for_binary_input = {}  # {user_id: {"symbol": "SYMBOL", "option_type": "CALL/PUT"}}
    
    async def start_command(self, update: Update, context: CallbackContext):
        user = update.effective_user
        
        welcome_text = f"""
🤖 **Ultimate Scam Exchange - Полный Функционал!**

Привет, {user.first_name}! 🎯

💸 **Доступные функции:**
• 📈 Просмотр графиков монет
• 💸 Быстрая торговля
• 🎯 Фьючерсы 100-1000x  
• 💰 Спот-торговля
• 🪙 Создание своих монет
• 📊 Бинарные опционы

💰 **Стартовый баланс:** 1000 USDT

👇 **Выберите действие:**
        """
        
        await update.message.reply_text(
            welcome_text,
            reply_markup=self.bot.get_main_keyboard(),
            parse_mode="Markdown"
        )
    
    async def binary_options_command(self, update: Update, context: CallbackContext):
        binary_text = """
📊 **Бинарные Опционы**

🎯 **Как это работает:**
• Выбираете актив (монету)
• Ставите на рост (CALL) или падение (PUT)
• Выбираете сумму и время экспирации
• Если ваш прогноз верный - получаете 80% прибыли!

⏰ **Доступные сроки:**
• 1 минута (x1.8)
• 5 минут (x1.8)  
• 15 минут (x1.8)

👇 **Выберите тип опциона:
        """
        
        await update.message.reply_text(
            binary_text,
            reply_markup=self.bot.get_binary_options_keyboard(),
            parse_mode="Markdown"
        )
    
    async def api_key_command(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        api_key = self.bot.db.get_user_api_key(user_id)
        
        api_text = f"""
🔑 **Ваш API Ключ**

Для использования мобильного приложения:

**API Key:** `{api_key}`
**User ID:** `{user_id}`

📱 **Скачайте приложение для:**
• 📊 Реальных графиков со свечами
• 📈 Технического анализа
• ⚡ Быстрой торговли
• 🔔 Уведомлений о ценах

⚠️ **Не передавайте ключ третьим лицам!**
        """
        
        await update.message.reply_text(
            api_text,
            parse_mode="Markdown"
        )
    
    async def spot_trading_command(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        usdt_balance = self.bot.db.get_spot_balance(user_id, "USDT")
        
        spot_text = f"""
💰 **Спот-Торговля (Без Плеча)**

💵 **Ваш баланс:** {usdt_balance:.2f} USDT

🎯 **Выберите монету для торговли:**
• MOONSHOT - Мем-монета для быстрого роста
• LAMBO - Токен для покупки Lambo
• RICH - Быстрое обогащение
• SCAM - Классическая скам-монета
• PUMP - Для пампов и дампов
• DUMP - Обратная сторона пампов
• RUG - Высокий риск, высокий доход

👇 **Нажмите на монету:**
        """
        
        await update.message.reply_text(
            spot_text,
            reply_markup=self.bot.get_spot_trading_keyboard(),
            parse_mode="Markdown"
        )
    
    async def handle_coin_selection(self, update: Update, context: CallbackContext):
        text = update.message.text
        user_id = update.effective_user.id
        
        if text.startswith("🪙 "):
            symbol = text.replace("🪙 ", "").strip()
            if symbol in SCAM_COINS:
                price = self.spot_trading.get_current_price(symbol)
                coin_balance = self.bot.db.get_spot_balance(user_id, symbol)
                usdt_balance = self.bot.db.get_spot_balance(user_id, "USDT")
                
                coin_info = f"""
🪙 **{SCAM_COINS[symbol]['name']} ({symbol})**

💰 **Текущая цена:** {price:.8f} USDT
📊 **Ваш баланс:** {coin_balance:.2f} {symbol}
💵 **Доступно USDT:** {usdt_balance:.2f}

⚡ **Выберите действие:**
                """
                
                await update.message.reply_text(
                    coin_info,
                    reply_markup=self.bot.get_trade_keyboard(symbol),
                    parse_mode="Markdown"
                )
        
        elif text == "📊 Спот Баланс":
            await self.show_spot_balance(update, context)
        elif text == "🏠 Главная":
            await self.start_command(update, context)
        elif text == "📊 Бинарные Опционы":
            await self.binary_options_command(update, context)
        elif text == "🔑 API Ключ":
            await self.api_key_command(update, context)
    
    async def show_spot_balance(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        usdt_balance = self.bot.db.get_spot_balance(user_id, "USDT")
        
        balance_text = f"""
💰 **Ваш Спот Баланс**

💵 **USDT:** {usdt_balance:.2f}

🪙 **Монеты:**
        """
        
        total_value = usdt_balance
        for symbol in SCAM_COINS:
            coin_balance = self.bot.db.get_spot_balance(user_id, symbol)
            if coin_balance > 0:
                price = self.spot_trading.get_current_price(symbol)
                value = coin_balance * price
                total_value += value
                balance_text += f"• {symbol}: {coin_balance:.2f} (~{value:.2f} USDT)\n"
        
        # Показываем активные опционы
        active_options = self.bot.db.get_active_options(user_id)
        if active_options:
            balance_text += f"\n📊 **Активные опционы:** {len(active_options)}"
        
        balance_text += f"\n💎 **Общая стоимость:** {total_value:.2f} USDT"
        
        await update.message.reply_text(
            balance_text,
            parse_mode="Markdown"
        )
    
    async def start_binary_creation(self, update: Update, context: CallbackContext):
        option_type = "CALL" if "CALL" in update.message.text else "PUT"
        
        self.waiting_for_binary_input[update.effective_user.id] = {
            "option_type": option_type
        }
        
        await update.message.reply_text(
            f"📊 **Создание {option_type} опциона**\n\n"
            f"💡 Введите тикер монеты (например: MOONSHOT):",
            reply_markup=ReplyKeyboardRemove()
        )
        return BINARY_SYMBOL
    
    async def handle_binary_symbol(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        symbol = update.message.text.upper()
        
        if symbol not in SCAM_COINS:
            await update.message.reply_text("❌ Неверный тикер монеты! Попробуйте снова:")
            return BINARY_SYMBOL
        
        self.waiting_for_binary_input[user_id]["symbol"] = symbol
        
        current_price = self.spot_trading.get_current_price(symbol)
        await update.message.reply_text(
            f"🪙 **{symbol}** - Текущая цена: {current_price:.8f} USDT\n\n"
            f"💵 Введите сумму опциона в USDT:"
        )
        return BINARY_AMOUNT
    
    async def handle_binary_amount(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        
        try:
            amount = Decimal(update.message.text)
            if amount <= 0:
                await update.message.reply_text("❌ Сумма должна быть больше 0! Попробуйте снова:")
                return BINARY_AMOUNT
            
            self.waiting_for_binary_input[user_id]["amount"] = amount
            
            await update.message.reply_text(
                "⏰ Выберите время экспирации:\n\n"
                "1 - 1 минута\n"
                "5 - 5 минут\n" 
                "15 - 15 минут\n\n"
                "Введите число:"
            )
            return BINARY_DURATION
            
        except:
            await update.message.reply_text("❌ Введите корректную сумму!")
            return BINARY_AMOUNT
    
    async def handle_binary_duration(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        
        try:
            duration = int(update.message.text)
            if duration not in [1, 5, 15]:
                await update.message.reply_text("❌ Доступные варианты: 1, 5, 15. Попробуйте снова:")
                return BINARY_DURATION
            
            binary_data = self.waiting_for_binary_input[user_id]
            result = await self.binary_trading.create_binary_option(
                user_id, binary_data["symbol"], binary_data["option_type"],
                binary_data["amount"], duration
            )
            
            await update.message.reply_text(
                result["message"],
                reply_markup=self.bot.get_main_keyboard()
            )
            
            del self.waiting_for_binary_input[user_id]
            return ConversationHandler.END
            
        except:
            await update.message.reply_text("❌ Введите корректное число!")
            return BINARY_DURATION
    
    async def cancel_binary_creation(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        if user_id in self.waiting_for_binary_input:
            del self.waiting_for_binary_input[user_id]
        
        await update.message.reply_text(
            "❌ Создание опциона отменено.",
            reply_markup=self.bot.get_main_keyboard()
        )
        return ConversationHandler.END

# ===== ГЛАВНЫЙ КЛАСС =====
class UltimateScamExchange:
    def __init__(self):
        self.ultimate_bot = UltimateScamExchangeBot()
        self.commands = UltimateBotCommands(self.ultimate_bot)
        
        # ConversationHandler для создания монет
        self.coin_creation_conv = ConversationHandler(
            entry_points=[
                MessageHandler(filters.Regex('^🪙 Создать Монету$'), self.start_coin_creation)
            ],
            states={
                COIN_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_coin_name)],
                COIN_SYMBOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_coin_symbol)],
                COIN_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_coin_price)],
                COIN_SUPPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_coin_supply)],
            },
            fallbacks=[CommandHandler('cancel', self.cancel_coin_creation)]
        )
        
        # ConversationHandler для бинарных опционов
        self.binary_creation_conv = ConversationHandler(
            entry_points=[
                MessageHandler(filters.Regex('^(📈 CALL опцион|📉 PUT опцион)$'), self.commands.start_binary_creation)
            ],
            states={
                BINARY_SYMBOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.commands.handle_binary_symbol)],
                BINARY_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.commands.handle_binary_amount)],
                BINARY_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.commands.handle_binary_duration)],
            },
            fallbacks=[CommandHandler('cancel', self.commands.cancel_binary_creation)]
        )
    
    async def start_coin_creation(self, update: Update, context: CallbackContext):
        await update.message.reply_text(
            "🪙 **Создание новой монеты**\n\n"
            "💡 Введите название монеты:",
            reply_markup=ReplyKeyboardRemove()
        )
        return COIN_NAME
    
    async def handle_coin_name(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        self.ultimate_bot.user_coin_creation[user_id] = {"name": update.message.text}
        
        await update.message.reply_text("📛 Введите тикер (3-8 букв):")
        return COIN_SYMBOL
    
    async def handle_coin_symbol(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        symbol = update.message.text.upper()
        self.ultimate_bot.user_coin_creation[user_id]["symbol"] = symbol
        
        await update.message.reply_text("💰 Введите начальную цену в USDT:")
        return COIN_PRICE
    
    async def handle_coin_price(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        try:
            price = Decimal(update.message.text)
            self.ultimate_bot.user_coin_creation[user_id]["price"] = price
            await update.message.reply_text("🔢 Введите общее предложение (total supply):")
            return COIN_SUPPLY
        except:
            await update.message.reply_text("❌ Введите корректное число!")
            return COIN_PRICE
    
    async def handle_coin_supply(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        try:
            supply = Decimal(update.message.text)
            coin_data = self.ultimate_bot.user_coin_creation[user_id]
            
            success = self.ultimate_bot.db.create_coin(
                user_id, coin_data["name"], coin_data["symbol"], 
                coin_data["price"], supply
            )
            
            if success:
                await update.message.reply_text(
                    f"🎉 Монета {coin_data['name']} ({coin_data['symbol']}) создана!",
                    reply_markup=self.ultimate_bot.get_main_keyboard()
                )
            else:
                await update.message.reply_text("❌ Ошибка создания!")
            
            if user_id in self.ultimate_bot.user_coin_creation:
                del self.ultimate_bot.user_coin_creation[user_id]
                
            return ConversationHandler.END
        except:
            await update.message.reply_text("❌ Ошибка!")
            return COIN_SUPPLY
    
    async def cancel_coin_creation(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        if user_id in self.ultimate_bot.user_coin_creation:
            del self.ultimate_bot.user_coin_creation[user_id]
        
        await update.message.reply_text(
            "❌ Создание отменено.",
            reply_markup=self.ultimate_bot.get_main_keyboard()
        )
        return ConversationHandler.END
    
    async def handle_trade_input(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        
        if user_id in self.commands.waiting_for_trade_input:
            trade_data = self.commands.waiting_for_trade_input[user_id]
            
            try:
                amount = Decimal(update.message.text)
                if amount <= 0:
                    await update.message.reply_text("❌ Сумма должна быть больше 0!")
                    return
                
                if trade_data["action"] == "buy":
                    result = await self.commands.spot_trading.execute_spot_buy(
                        user_id, trade_data["symbol"], amount
                    )
                else:
                    result = await self.commands.spot_trading.execute_spot_sell(
                        user_id, trade_data["symbol"], amount
                    )
                
                await update.message.reply_text(result["message"])
                del self.commands.waiting_for_trade_input[user_id]
                
            except:
                await update.message.reply_text("❌ Введите корректное число!")
    
    def setup_handlers(self, application):
        # Основные команды
        application.add_handler(CommandHandler("start", self.commands.start_command))
        application.add_handler(CommandHandler("spot", self.commands.spot_trading_command))
        application.add_handler(CommandHandler("profile", self.commands.profile_command))
        application.add_handler(CommandHandler("help", self.commands.help_command))
        application.add_handler(CommandHandler("api", self.commands.api_key_command))
        
        # Создание монет
        application.add_handler(self.coin_creation_conv)
        
        # Бинарные опционы
        application.add_handler(self.binary_creation_conv)
        
        # Обработчики кнопок
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_buttons))
        
        # Callback handlers
        application.add_handler(CallbackQueryHandler(self.handle_callback))
        
        # Обработчик торговых вводов
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_trade_input))
    
    async def handle_callback(self, update: Update, context: CallbackContext):
        query = update.callback_query
        await query.answer()
        
        data = query.data
        user_id = query.from_user.id
        
        if data.startswith("buy_") or data.startswith("sell_"):
            symbol = data.split("_")[1]
            action = "покупки" if data.startswith("buy_") else "продажи"
            
            self.commands.waiting_for_trade_input[user_id] = {
                "action": "buy" if data.startswith("buy_") else "sell",
                "symbol": symbol
            }
            
            await query.edit_message_text(
                f"🪙 **{action.title()} {symbol}**\n\n"
                f"💡 Введите сумму {action} в {symbol}:\n"
                f"Пример: 100 (для покупки 100 {symbol})",
                parse_mode="Markdown"
            )
            
        elif data.startswith("binary_"):
            symbol = data.split("_")[1]
            await query.edit_message_text(
                f"📊 **Бинарный опцион на {symbol}**\n\n"
                f"Выберите тип опциона:",
                reply_markup=self.ultimate_bot.get_binary_options_keyboard()
            )
            
        elif data == "back_to_spot":
            await self.commands.spot_trading_command(update, context)
    
    async def handle_buttons(self, update: Update, context: CallbackContext):
        text = update.message.text
        
        if text == "📈 Открыть График":
            await update.message.reply_text("📊 Графики будут в мобильном приложении!")
        elif text == "💸 Быстрая Торговля":
            await update.message.reply_text("⚡ Быстрая торговля доступна в приложении!")
        elif text == "🎯 Фьючерсы 100-1000x":
            await update.message.reply_text("🎯 Фьючерсы с плечом в разработке!")
        elif text == "💰 Спот Торговля":
            await self.commands.spot_trading_command(update, context)
        elif text == "🪙 Создать Монету":
            await self.start_coin_creation(update, context)
        elif text == "📊 Мои Монеты":
            await self.show_user_coins(update, context)
        elif text == "🏦 Пополнение/Вывод":
            await update.message.reply_text("💳 Пополнение и вывод в разработке!")
        elif text == "👤 Профиль":
            await self.commands.profile_command(update, context)
        elif text == "📚 Помощь":
            await self.commands.help_command(update, context)
        elif text == "🔥 Топ Ликвидаций":
            await update.message.reply_text("🔥 Топ ликвидаций в разработке!")
        elif text == "📊 Бинарные Опционы":
            await self.commands.binary_options_command(update, context)
        elif text == "🔑 API Ключ":
            await self.commands.api_key_command(update, context)
        elif text == "📊 Мои опционы":
            await self.show_user_options(update, context)
        elif text == "🔙 Назад":
            await self.commands.start_command(update, context)
        else:
            await self.commands.handle_coin_selection(update, context)
    
    async def show_user_coins(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        user_coins = self.ultimate_bot.db.get_user_coins(user_id)
        
        if not user_coins:
            await update.message.reply_text("❌ У вас нет созданных монет.")
            return
        
        coins_text = "🪙 **Ваши созданные монеты:**\n\n"
        for coin in user_coins:
            coins_text += f"• **{coin['coin_name']}** ({coin['coin_symbol']})\n"
            coins_text += f"  💰 Начальная цена: {Decimal(str(coin['initial_price'])):.8f} USDT\n"
            coins_text += f"  🔢 Общее предложение: {Decimal(str(coin['total_supply'])):.0f}\n\n"
        
        await update.message.reply_text(coins_text, parse_mode="Markdown")
    
    async def show_user_options(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        active_options = self.ultimate_bot.db.get_active_options(user_id)
        
        if not active_options:
            await update.message.reply_text("📊 У вас нет активных опционов.")
            return
        
        options_text = "📊 **Ваши активные опционы:**\n\n"
        for option in active_options:
            expiry_time = datetime.fromisoformat(option['expiry_time'])
            time_left = expiry_time - datetime.now()
            minutes_left = max(0, int(time_left.total_seconds() / 60))
            
            options_text += f"🪙 **{option['symbol']}** - {option['option_type']}\n"
            options_text += f"💰 Сумма: {Decimal(str(option['amount']))} USDT\n"
            options_text += f"🎯 Цена открытия: {Decimal(str(option['open_price'])):.8f} USDT\n"
            options_text += f"⏰ Осталось: {minutes_left} минут\n\n"
        
        await update.message.reply_text(options_text, parse_mode="Markdown")
    
    async def profile_command(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        usdt_balance = self.ultimate_bot.db.get_spot_balance(user_id, "USDT")
        
        profile_text = f"""
👤 **Ваш Профиль**

💼 **ID:** {user_id}
💵 **Баланс USDT:** {usdt_balance:.2f}
📊 **Активные опционы:** {len(self.ultimate_bot.db.get_active_options(user_id))}
🪙 **Созданные монеты:** {len(self.ultimate_bot.db.get_user_coins(user_id))}

📱 **Для полного функционала скачайте приложение!**
        """
        
        await update.message.reply_text(profile_text, parse_mode="Markdown")
    
    async def help_command(self, update: Update, context: CallbackContext):
        help_text = """
📚 **Помощь по Ultimate Scam Exchange**

💸 **Торговля:**
• Спот-торговля - покупка/продажа монет
• Бинарные опционы - ставки на рост/падение
• Создание монет - выпуск своих токенов

📊 **Бинарные опционы:**
• CALL - ставка на рост цены
• PUT - ставка на падение цены  
• Экспирация: 1, 5, 15 минут
• Выигрыш: 80% от суммы

🔑 **API Ключ:**
• Используется для мобильного приложения
• Не передавайте ключ третьим лицам

📱 **Мобильное приложение:**
• Реальные графики со свечами
• Технический анализ
• Быстрая торговля
• Уведомления о ценах

⚠️ **Внимание:** Это демо-версия для обучения!
        """
        
        await update.message.reply_text(help_text, parse_mode="Markdown")

# ===== ЗАПУСК БОТА =====
def main():
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    ultimate_exchange = UltimateScamExchange()
    ultimate_exchange.setup_handlers(application)
    
    print("🤖 Ultimate Scam Exchange Bot запущен!")
    application.run_polling()

if __name__ == "__main__":
    main()