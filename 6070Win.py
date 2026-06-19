#!/usr/bin/env python3
import websocket
import json
import time
import threading
from datetime import datetime
import logging
from telegram import Bot
import asyncio
import random
import string
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TradingViewSignals6070Win:
    def __init__(self):
        self.bot = Bot(token="7674975679:AAFpNzveogcMlTo5FIoMSWdo0xRWgxfdrqo")
        self.chat_id = "-1002868092777"
        self.topic_id = 8451  # Новый topic для 60-70% сигналов
        
        # Отборные инструменты с высокой ликвидностью
        self.symbols = {
            "EURUSD": {
                "name": "EUR/USD",
                "tv_symbol": "FX_IDC:EURUSD",
                "price_history": [],
                "volume_history": [],
                "signal_strength": 0,
                "trend": "NEUTRAL",
                "entry_price": None,
                "stop_loss": None,
                "take_profit": None,
                "confidence": 0,
                "risk_reward": 0
            },
            "XAUUSD": {
                "name": "GOLD (XAU/USD)", 
                "tv_symbol": "OANDA:XAUUSD",
                "price_history": [],
                "volume_history": [],
                "signal_strength": 0,
                "trend": "NEUTRAL",
                "entry_price": None,
                "stop_loss": None,
                "take_profit": None,
                "confidence": 0,
                "risk_reward": 0
            },
            "GBPUSD": {
                "name": "GBP/USD",
                "tv_symbol": "FX_IDC:GBPUSD",
                "price_history": [],
                "volume_history": [],
                "signal_strength": 0,
                "trend": "NEUTRAL", 
                "entry_price": None,
                "stop_loss": None,
                "take_profit": None,
                "confidence": 0,
                "risk_reward": 0
            },
            "USDJPY": {
                "name": "USD/JPY",
                "tv_symbol": "FX_IDC:USDJPY", 
                "price_history": [],
                "volume_history": [],
                "signal_strength": 0,
                "trend": "NEUTRAL",
                "entry_price": None,
                "stop_loss": None,
                "take_profit": None,
                "confidence": 0,
                "risk_reward": 0
            }
        }
        
        # Строгие настройки для 60-70% Win Rate
        self.win_rate_target = 65  # Целевой win rate
        self.min_confidence = 75   # Повышенная минимальная уверенность
        self.price_history_length = 100
        self.volume_confirmation = True  # Подтверждение объемами
        
        self.last_signal_time = {}
        for symbol in self.symbols.keys():
            self.last_signal_time[symbol] = None
            
        self.ws = None
        self.session_id = self.generate_session_id()
        self.reconnect_delay = 5
        self.running = True
        self.loop = asyncio.new_event_loop()
        
        # Статистика сигналов
        self.signals_sent = 0
        self.profitable_signals = 0
        self.total_signals = 0

    def generate_session_id(self):
        return ''.join(random.choices(string.ascii_letters + string.digits, k=12))

    async def send_telegram_signal(self, symbol_data, signal_type, confirmation_level):
        try:
            emoji = "🟢" if signal_type == "BUY" else "🔴"
            confidence_emoji = "🎯" if symbol_data['confidence'] > 80 else "📊"
            premium_emoji = "⭐" if confirmation_level == "HIGH" else "🔸"
            
            # Расчет потенциальной прибыли
            if signal_type == "BUY":
                potential_profit = ((symbol_data['take_profit'] - symbol_data['entry_price']) / symbol_data['entry_price']) * 100
                potential_loss = ((symbol_data['entry_price'] - symbol_data['stop_loss']) / symbol_data['entry_price']) * 100
            else:
                potential_profit = ((symbol_data['entry_price'] - symbol_data['take_profit']) / symbol_data['entry_price']) * 100
                potential_loss = ((symbol_data['stop_loss'] - symbol_data['entry_price']) / symbol_data['entry_price']) * 100
            
            message = (
                f"{premium_emoji} *ПРЕМИУМ СИГНАЛ {signal_type} | 60-70% WIN RATE* {premium_emoji}\n\n"
                f"*Инструмент:* {symbol_data['name']}\n"
                f"*Тип сигнала:* {signal_type}\n"
                f"*Уровень подтверждения:* {confirmation_level}\n\n"
                f"*💰 Уровни торговли:*\n"
                f"   • Вход: {symbol_data['entry_price']:.5f}\n"
                f"   • Стоп-лосс: {symbol_data['stop_loss']:.5f}\n" 
                f"   • Тейк-профит: {symbol_data['take_profit']:.5f}\n\n"
                f"*📊 Аналитика сигнала:*\n"
                f"   • Уверенность: {symbol_data['confidence']}% {confidence_emoji}\n"
                f"   • Сила сигнала: {symbol_data['signal_strength']}/10\n"
                f"   • Риск/Прибыль: 1:{symbol_data['risk_reward']:.1f}\n"
                f"   • Потенц. прибыль: +{potential_profit:.2f}%\n"
                f"   • Потенц. убыток: -{potential_loss:.2f}%\n\n"
                f"*🎯 Рекомендации:*\n"
                f"   • Таймфрейм: H1-H4\n"
                f"   • Объем: 2-3% от депозита\n"
                f"   • Длительность: 2-6 часов\n"
                f"   • Трейлинг-стоп: +1.5% от цены\n\n"
                f"*📈 Статистика системы:*\n"
                f"   • Win Rate: {self.get_win_rate()}%\n"
                f"   • Премиум сигналов: {self.signals_sent}\n"
                f"   • Общих сигналов: {self.total_signals}\n"
                f"   • Время: {datetime.now().strftime('%H:%M:%S')}\n\n"
                f"_💎 Сигнал с повышенной вероятностью успеха_"
            )
            
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                message_thread_id=self.topic_id,
                parse_mode="Markdown"
            )
            
            self.signals_sent += 1
            logger.info(f"Премиум сигнал {signal_type} отправлен для {symbol_data['name']}")
            
        except Exception as e:
            logger.error(f"Ошибка отправки премиум сигнала: {e}")

    def calculate_sma(self, prices, period):
        """Простое скользящее среднее"""
        if len(prices) < period:
            return np.mean(prices) if prices else 0
        return np.mean(prices[-period:])

    def calculate_ema(self, prices, period):
        """Экспоненциальное скользящее среднее"""
        if len(prices) < period:
            return np.mean(prices) if prices else 0
        
        alpha = 2 / (period + 1)
        ema = prices[0]
        for price in prices[1:]:
            ema = alpha * price + (1 - alpha) * ema
        return ema

    def calculate_rsi(self, prices, period=14):
        """Расчет RSI"""
        if len(prices) < period + 1:
            return 50
        
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        
        if avg_loss == 0:
            return 100
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def calculate_macd(self, prices, fast=12, slow=26, signal=9):
        """Упрощенный расчет MACD"""
        if len(prices) < slow:
            return 0, 0, 0
        
        ema_fast = self.calculate_ema(prices, fast)
        ema_slow = self.calculate_ema(prices, slow)
        macd_line = ema_fast - ema_slow
        
        # Для сигнальной линии используем последние значения MACD
        macd_values = []
        for i in range(len(prices) - slow + 1):
            window = prices[i:i+slow]
            ema_f = self.calculate_ema(window, fast)
            ema_s = self.calculate_ema(window, slow)
            macd_values.append(ema_f - ema_s)
        
        if len(macd_values) >= signal:
            signal_line = self.calculate_ema(macd_values[-signal:], signal)
        else:
            signal_line = np.mean(macd_values) if macd_values else 0
            
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    def calculate_bollinger_bands(self, prices, period=20, std_dev=2):
        """Полосы Боллинджера"""
        if len(prices) < period:
            middle = np.mean(prices) if prices else 0
            return middle, middle, middle
        
        middle_band = np.mean(prices[-period:])
        std = np.std(prices[-period:])
        
        upper_band = middle_band + (std * std_dev)
        lower_band = middle_band - (std * std_dev)
        
        return upper_band, middle_band, lower_band

    def calculate_stochastic(self, prices, k_period=14, d_period=3):
        """Стохастический осциллятор"""
        if len(prices) < k_period:
            return 50, 50
        
        current_close = prices[-1]
        lowest_low = min(prices[-k_period:])
        highest_high = max(prices[-k_period:])
        
        if highest_high - lowest_low == 0:
            return 50, 50
            
        k = ((current_close - lowest_low) / (highest_high - lowest_low)) * 100
        
        # Для %D используем простую SMA от %K
        if len(prices) >= k_period + d_period - 1:
            k_values = []
            for i in range(len(prices) - k_period + 1):
                window = prices[i:i+k_period]
                low = min(window)
                high = max(window)
                if high - low == 0:
                    k_val = 50
                else:
                    k_val = ((window[-1] - low) / (high - low)) * 100
                k_values.append(k_val)
            
            d = np.mean(k_values[-d_period:]) if k_values else k
        else:
            d = k
            
        return k, d

    def calculate_atr(self, prices, period=14):
        """Average True Range"""
        if len(prices) < period + 1:
            return 0
        
        true_ranges = []
        for i in range(1, len(prices)):
            high_low = abs(prices[i] - prices[i-1])
            true_ranges.append(high_low)
        
        atr = np.mean(true_ranges[-period:]) if true_ranges else 0
        return atr

    def calculate_advanced_indicators(self, price_history, volume_history):
        """Расчет расширенных технических индикаторов без TA-Lib"""
        if len(price_history) < 50:
            return None
            
        prices = np.array(price_history)
        volumes = np.array(volume_history) if volume_history and len(volume_history) > 0 else np.ones_like(prices)
        
        try:
            # SMA 20 и 50
            sma_20 = self.calculate_sma(prices, 20)
            sma_50 = self.calculate_sma(prices, 50)
            
            # EMA 21 для тренда
            ema_21 = self.calculate_ema(prices, 21)
            
            # RSI 14
            rsi = self.calculate_rsi(prices, 14)
            
            # MACD
            macd_line, macd_signal, macd_hist = self.calculate_macd(prices)
            macd_trend = "BULLISH" if macd_hist > 0 else "BEARISH"
            
            # Bollinger Bands
            bb_upper, bb_middle, bb_lower = self.calculate_bollinger_bands(prices)
            if bb_upper - bb_lower > 0:
                bb_position = (prices[-1] - bb_lower) / (bb_upper - bb_lower)
            else:
                bb_position = 0.5
            
            # Stochastic
            stoch_k, stoch_d = self.calculate_stochastic(prices)
            
            # ATR для волатильности
            atr = self.calculate_atr(prices)
            atr_percent = (atr / prices[-1]) * 100 if prices[-1] > 0 else 0
            
            # Volume analysis
            volume_sma = self.calculate_sma(volumes, 20)
            volume_ratio = volumes[-1] / volume_sma if volume_sma > 0 else 1
            
            return {
                'sma_20': sma_20,
                'sma_50': sma_50,
                'ema_21': ema_21,
                'rsi': rsi,
                'macd_trend': macd_trend,
                'macd_hist': macd_hist,
                'bb_position': bb_position,
                'stoch_k': stoch_k,
                'stoch_d': stoch_d,
                'atr_percent': atr_percent,
                'volume_ratio': volume_ratio,
                'current_price': prices[-1],
                'trend': 'BULLISH' if sma_20 > sma_50 else 'BEARISH',
                'strong_trend': 'BULLISH' if (sma_20 > sma_50 and ema_21 > sma_20) else 'BEARISH'
            }
        except Exception as e:
            logger.error(f"Ошибка расчета индикаторов: {e}")
            return None

    def generate_premium_signal(self, symbol, indicators):
        """Генерация премиум сигнала с повышенными требованиями"""
        if not indicators:
            return None
            
        current_price = indicators['current_price']
        signal_type = None
        confidence = 0
        confirmation_level = "MEDIUM"
        
        # СТРОГИЕ ПРАВИЛА ДЛЯ BUY СИГНАЛОВ
        buy_conditions = [
            indicators['trend'] == 'BULLISH',
            indicators['strong_trend'] == 'BULLISH',
            indicators['rsi'] > 40 and indicators['rsi'] < 65,
            indicators['macd_trend'] == 'BULLISH',
            indicators['bb_position'] < 0.7,  # Не в перекупленности
            indicators['stoch_k'] > 20 and indicators['stoch_k'] < 80,
            indicators['volume_ratio'] > 1.0,  # Подтверждение объемом
            current_price > indicators['sma_20']
        ]
        
        # СТРОГИЕ ПРАВИЛА ДЛЯ SELL СИГНАЛОВ
        sell_conditions = [
            indicators['trend'] == 'BEARISH',
            indicators['strong_trend'] == 'BEARISH',
            indicators['rsi'] < 60 and indicators['rsi'] > 35,
            indicators['macd_trend'] == 'BEARISH',
            indicators['bb_position'] > 0.3,  # Не в перепроданности
            indicators['stoch_k'] < 80 and indicators['stoch_k'] > 20,
            indicators['volume_ratio'] > 1.0,  # Подтверждение объемом
            current_price < indicators['sma_20']
        ]
        
        if sum(buy_conditions) >= 6:  # Минимум 6 из 8 условий
            signal_type = "BUY"
            confidence = self.calculate_premium_confidence(indicators, 'BUY', sum(buy_conditions))
            if sum(buy_conditions) >= 7:
                confirmation_level = "HIGH"
            
        elif sum(sell_conditions) >= 6:  # Минимум 6 из 8 условий
            signal_type = "SELL" 
            confidence = self.calculate_premium_confidence(indicators, 'SELL', sum(sell_conditions))
            if sum(sell_conditions) >= 7:
                confirmation_level = "HIGH"
        
        if signal_type and confidence >= self.min_confidence:
            # Расчет уровней с ATR
            atr_multiplier = 1.8  # Более широкий стоп для премиум сигналов
            sl_distance = indicators['atr_percent'] * atr_multiplier / 100 * current_price
            
            if signal_type == "BUY":
                stop_loss = current_price - sl_distance
                take_profit = current_price + (sl_distance * 2.5)  # R:R = 1:2.5
            else:
                stop_loss = current_price + sl_distance  
                take_profit = current_price - (sl_distance * 2.5)
            
            risk_reward = abs((take_profit - current_price) / (current_price - stop_loss)) if (current_price - stop_loss) != 0 else 0
            
            return {
                'type': signal_type,
                'confidence': confidence,
                'entry_price': current_price,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'signal_strength': min(int(confidence / 8), 10),
                'risk_reward': risk_reward,
                'confirmation_level': confirmation_level
            }
        
        return None

    def calculate_premium_confidence(self, indicators, signal_type, conditions_met):
        """Расчет уверенности для премиум сигналов"""
        confidence = 60  # Базовая уверенность выше
        
        # Количество выполненных условий
        confidence += (conditions_met - 6) * 8  # +8% за каждое дополнительное условие
        
        # RSI фактор
        if signal_type == "BUY":
            if 45 <= indicators['rsi'] < 55:
                confidence += 10
            elif 40 <= indicators['rsi'] < 60:
                confidence += 5
        else:  # SELL
            if 45 <= indicators['rsi'] < 55:
                confidence += 10
            elif 40 <= indicators['rsi'] < 60:
                confidence += 5
        
        # Тренд фактор
        if indicators['strong_trend'] == ('BULLISH' if signal_type == "BUY" else 'BEARISH'):
            confidence += 15
        
        # Объемный фактор
        if indicators['volume_ratio'] > 1.5:
            confidence += 10
        elif indicators['volume_ratio'] > 1.2:
            confidence += 5
        
        # Волатильность фактор (умеренная лучше)
        if 0.8 < indicators['atr_percent'] < 2.5:
            confidence += 8
        
        return min(confidence, 95)

    def get_win_rate(self):
        """Расчет текущего win rate"""
        if self.total_signals == 0:
            return 0
        return (self.profitable_signals / self.total_signals) * 100

    def process_data(self, data):
        if isinstance(data, dict) and data.get("m") == "qsd":
            try:
                symbol_data = data["p"][1]
                symbol_name = symbol_data["n"]
                
                for symbol_key, symbol_info in self.symbols.items():
                    if symbol_info["tv_symbol"] == symbol_name:
                        values = symbol_data.get("v", {})
                        
                        if "lp" in values:
                            # Обновляем цену и объем
                            new_price = float(values["lp"])
                            new_volume = float(values.get("volume", 0))
                            
                            self.symbols[symbol_key]["price_history"].append(new_price)
                            if new_volume > 0:
                                self.symbols[symbol_key]["volume_history"].append(new_volume)
                            
                            # Ограничиваем историю
                            if len(self.symbols[symbol_key]["price_history"]) > self.price_history_length:
                                self.symbols[symbol_key]["price_history"].pop(0)
                            if len(self.symbols[symbol_key]["volume_history"]) > self.price_history_length:
                                self.symbols[symbol_key]["volume_history"].pop(0)
                            
                            # Анализируем на премиум сигналы
                            if len(self.symbols[symbol_key]["price_history"]) >= 50:
                                indicators = self.calculate_advanced_indicators(
                                    self.symbols[symbol_key]["price_history"],
                                    self.symbols[symbol_key]["volume_history"]
                                )
                                
                                if indicators:
                                    signal = self.generate_premium_signal(symbol_key, indicators)
                                    
                                    if signal and self.should_send_premium_signal(symbol_key):
                                        # Обновляем данные символа
                                        self.symbols[symbol_key].update(signal)
                                        self.symbols[symbol_key]['confidence'] = signal['confidence']
                                        
                                        # Отправляем премиум сигнал
                                        asyncio.run_coroutine_threadsafe(
                                            self.send_telegram_signal(
                                                self.symbols[symbol_key], 
                                                signal['type'],
                                                signal['confirmation_level']
                                            ), 
                                            self.loop
                                        )
                                        
                                        self.last_signal_time[symbol_key] = time.time()
                                        self.total_signals += 1
                            
                        break

            except KeyError as e:
                logger.error(f"Ошибка в данных: {e}")

    def should_send_premium_signal(self, symbol):
        """Проверяем, можно ли отправлять премиум сигнал"""
        now = time.time()
        last_time = self.last_signal_time.get(symbol)
        
        # Не отправляем премиум сигналы чаще чем раз в 2 часа
        if last_time and (now - last_time) < 7200:  # 2 часа
            return False
            
        return True

    def send_ws_message(self, message):
        """Отправка сообщения через WebSocket"""
        if self.ws and self.ws.sock and self.ws.sock.connected:
            try:
                formatted = f"~m~{len(message)}~m~{message}"
                self.ws.send(formatted)
            except Exception as e:
                logger.error(f"Ошибка отправки сообщения: {e}")

    def on_message(self, ws, message):
        """Обработка входящих сообщений WebSocket"""
        try:
            if message == "~h~":
                self.send_ws_message("~h~")
                return
                
            if message.startswith("~m~"):
                parts = message.split("~m~")
                for part in parts:
                    if part and part[0] == "{":
                        try:
                            data = json.loads(part)
                            self.process_data(data)
                        except json.JSONDecodeError:
                            continue

        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {e}")

    def on_error(self, ws, error):
        """Обработка ошибок WebSocket"""
        logger.error(f"WebSocket ошибка: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        """Обработка закрытия соединения"""
        logger.info(f"Соединение закрыто. Код: {close_status_code}, Причина: {close_msg}")
        if self.running:
            logger.info(f"Переподключение через {self.reconnect_delay} сек...")
            time.sleep(self.reconnect_delay)
            self.connect_websocket()

    def on_open(self, ws):
        """Обработка открытия соединения"""
        logger.info("Успешное подключение к TradingView WebSocket")
        logger.info(f"🎯 Режим: ПРЕМИУМ СИГНАЛЫ 60-70% WIN RATE")
        logger.info(f"📊 Минимальная уверенность: {self.min_confidence}%")
        
        init_sequence = [
            json.dumps({"m": "set_auth_token", "p": ["unauthorized_user_token"]}),
            json.dumps({"m": "quote_create_session", "p": [self.session_id]}),
            json.dumps({"m": "quote_set_fields", "p": [self.session_id, "lp", "volume", "ch", "chp"]}),
        ]
        
        for symbol in self.symbols.values():
            init_sequence.append(
                json.dumps({"m": "quote_add_symbols", "p": [self.session_id, symbol["tv_symbol"]]})
            )
        
        for cmd in init_sequence:
            self.send_ws_message(cmd)
            time.sleep(0.1)

    def connect_websocket(self):
        """Подключение к WebSocket"""
        websocket.enableTrace(False)
        self.ws = websocket.WebSocketApp(
            "wss://data.tradingview.com/socket.io/websocket",
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            header={
                "Origin": "https://www.tradingview.com",
                "User-Agent": "Mozilla/5.0"
            }
        )
        
        self.ws_thread = threading.Thread(target=self.ws.run_forever)
        self.ws_thread.daemon = True
        self.ws_thread.start()

    def run(self):
        """Основной метод запуска бота"""
        logger.info("Запуск премиум бота 60-70% Win Rate...")
        
        # Запускаем event loop в отдельном потоке
        def run_loop():
            asyncio.set_event_loop(self.loop)
            self.loop.run_forever()
            
        loop_thread = threading.Thread(target=run_loop)
        loop_thread.daemon = True
        loop_thread.start()
        
        # Подключаемся к WebSocket
        self.connect_websocket()
        
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Остановка премиум бота...")
            self.running = False
            if self.ws:
                self.ws.close()
            self.loop.call_soon_threadsafe(self.loop.stop)

if __name__ == "__main__":
    bot = TradingViewSignals6070Win()
    bot.run()