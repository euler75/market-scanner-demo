import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import requests

# -----------------------------------------------------------------------------
# LOGGING SETUP
# -----------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

# -----------------------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
PORT = int(os.getenv("PORT", "10000"))

# State cache to prevent duplicate alerts for the same candle
SENT_ALERTS: set[tuple[str, str, int]] = set()

DEFAULT_REVOLUT_X_CRYPTO = [
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "ADA-USD", "AVAX-USD", "DOGE-USD",
    "DOT-USD", "LINK-USD", "MATIC-USD", "NEAR-USD", "LTC-USD", "BCH-USD", "UNI-USD",
    "SHIB-USD", "PEPE-USD", "ATOM-USD", "XLM-USD", "ALGO-USD", "ICP-USD", "APT-USD",
    "INJ-USD", "RNDR-USD", "GRT-USD", "FET-USD", "STX-USD", "FIL-USD", "AAVE-USD",
    "OP-USD", "ARB-USD", "TIA-USD", "SUI-USD", "FTM-USD", "KAS-USD", "SEI-USD"
]

DEFAULT_STOCKS = [
    "AAPL", "NVDA", "TSLA", "MSFT", "AMZN", "GOOGL", "META", "AMD", "SPY", "QQQ"
]

def get_crypto_symbols() -> tuple[str, ...]:
    env_symbols = os.getenv("CRYPTO_SYMBOLS", "").strip()
    if env_symbols:
        symbols = [
            f"{s.strip().upper()}-USD" if not s.strip().upper().endswith("-USD") else s.strip().upper()
            for s in env_symbols.split(",") if s.strip()
        ]
        return tuple(symbols)
    return tuple(DEFAULT_REVOLUT_X_CRYPTO)

def get_stock_symbols() -> tuple[str, ...]:
    env_symbols = os.getenv("STOCK_SYMBOLS", "").strip()
    if env_symbols:
        return tuple([s.strip().upper() for s in env_symbols.split(",") if s.strip()])
    return tuple(DEFAULT_STOCKS)

# -----------------------------------------------------------------------------
# TELEGRAM BOT CLIENT
# -----------------------------------------------------------------------------
class TelegramBot:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    def send_message(self, message: str) -> bool:
        if DRY_RUN:
            logging.info("[DRY_RUN] Signal Triggered:\n%s", message)
            return True

        if not self.token or not self.chat_id:
            logging.error("Telegram credentials missing.")
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }
        
        topic_id = os.getenv("TELEGRAM_TOPIC_ID", "").strip()
        if topic_id.isdigit():
            payload["message_thread_id"] = int(topic_id)

        try:
            res = requests.post(url, json=payload, timeout=10.0)
            res.raise_for_status()
            time.sleep(0.1)
            return True
        except Exception as exc:
            logging.error("Failed to send Telegram message: %s", exc)
            return False

# -----------------------------------------------------------------------------
# INDICATOR ENGINE
# -----------------------------------------------------------------------------
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})

def fetch_candles(symbol: str, timeframe: str) -> list[dict] | None:
    interval = "1d" if timeframe.upper() == "DAILY" else "1wk"
    range_param = "1y" if interval == "1d" else "2y"
    
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range_param}&interval={interval}"

    try:
        res = SESSION.get(url, timeout=10.0)
        if res.status_code in (404, 429):
            return None
        res.raise_for_status()
        data = res.json()

        result = data.get("chart", {}).get("result")
        if not result:
            return None

        timestamps = result[0].get("timestamp", [])
        quote = result[0].get("indicators", {}).get("quote", [{}])[0]

        opens = quote.get("open", [])
        highs = quote.get("high", [])
        lows = quote.get("low", [])
        closes = quote.get("close", [])

        candles = []
        for i in range(len(timestamps)):
            if None in (opens[i], highs[i], lows[i], closes[i]):
                continue
            candles.append({
                "time": timestamps[i],
                "open": float(opens[i]),
                "high": float(highs[i]),
                "low": float(lows[i]),
                "close": float(closes[i])
            })

        return candles if len(candles) >= 36 else None
    except Exception as exc:
        logging.error("Fetch failed for %s (%s): %s", symbol, timeframe, exc)
        return None

def calculate_rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None

    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def calculate_ao(candles: list[dict]) -> tuple[float | None, str, bool]:
    if len(candles) < 35:
        return None, "UNKNOWN", False

    midpoints = [(c["high"] + c["low"]) / 2.0 for c in candles]

    sma5_curr = sum(midpoints[-5:]) / 5.0
    sma34_curr = sum(midpoints[-34:]) / 34.0
    ao_curr = sma5_curr - sma34_curr

    sma5_prev = sum(midpoints[-6:-1]) / 5.0
    sma34_prev = sum(midpoints[-35:-1]) / 34.0
    ao_prev = sma5_prev - sma34_prev

    ao_color = "GREEN" if ao_curr > ao_prev else "RED"
    is_negative = ao_curr < 0.0

    return ao_curr, ao_color, is_negative

def calculate_psar(candles: list[dict], af_start=0.02, af_inc=0.02, af_max=0.2) -> tuple[float | None, bool]:
    if len(candles) < 5:
        return None, False

    is_bull = candles[1]["close"] >= candles[0]["close"]
    psar = candles[0]["low"] if is_bull else candles[0]["high"]
    ep = candles[0]["high"] if is_bull else candles[0]["low"]
    af = af_start

    for i in range(1, len(candles)):
        curr = candles[i]
        prev = candles[i - 1]

        if is_bull:
            psar_next = psar + af * (ep - psar)
            if i > 1:
                psar_next = min(psar_next, prev["low"], candles[i - 2]["low"])
            else:
                psar_next = min(psar_next, prev["low"])

            if curr["low"] < psar_next:
                is_bull = False
                psar = ep
                ep = curr["low"]
                af = af_start
            else:
                psar = psar_next
                if curr["high"] > ep:
                    ep = curr["high"]
                    af = min(af + af_inc, af_max)
        else:
            psar_next = psar - af * (psar - ep)
            if i > 1:
                psar_next = max(psar_next, prev["high"], candles[i - 2]["high"])
            else:
                psar_next = max(psar_next, prev["high"])

            if curr["high"] > psar_next:
                is_bull = True
                psar = ep
                ep = curr["high"]
                af = af_start
            else:
                psar = psar_next
                if curr["low"] < ep:
                    ep = curr["low"]
                    af = min(af + af_inc, af_max)

    return psar, is_bull

def analyze_asset(symbol: str, timeframe: str, category: str) -> dict | None:
    all_candles = fetch_candles(symbol, timeframe)
    if not all_candles or len(all_candles) < 36:
        return None

    closed_candles = all_candles[:-1]
    if len(closed_candles) < 36:
        return None

    curr_closed_bar = closed_candles[-1]
    candle_timestamp = curr_closed_bar["time"]

    if (symbol, timeframe.upper(), candle_timestamp) in SENT_ALERTS:
        return None

    closes = [c["close"] for c in closed_candles]

    rsi = calculate_rsi(closes, period=14)
    ao, ao_color, ao_is_negative = calculate_ao(closed_candles)
    psar, is_bullish_psar = calculate_psar(closed_candles)

    if rsi is None or ao is None or psar is None:
        return None

    # BUY SIGNAL CONDITIONS
    rsi_in_range        = 50.0 <= rsi <= 60.0
    psar_is_below       = is_bullish_psar and (psar < curr_closed_bar["close"])
    ao_is_neg_and_green = ao_is_negative and (ao_color == "GREEN")
    candle_closed_green = curr_closed_bar["close"] >= curr_closed_bar["open"]

    if rsi_in_range and psar_is_below and ao_is_neg_and_green and candle_closed_green:
        display_symbol = symbol.replace("-USD", "") if category == "CRYPTO" else symbol
        return {
            "symbol": display_symbol,
            "category": category,
            "timeframe": timeframe.upper(),
            "close": curr_closed_bar["close"],
            "rsi": rsi,
            "ao": ao,
            "ao_color": ao_color,
            "psar": psar,
            "timestamp": candle_timestamp
        }

    return None

def format_telegram_alert(data: dict) -> str:
    icon = "🪙" if data["category"] == "CRYPTO" else "📈"
    return (
        f"{icon} **[{data['category']} BUY SIGNAL]** — `{data['symbol']}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Timeframe: **{data['timeframe']}**\n"
        f"Price: **${data['close']:,.4f}**\n"
        f"RSI (14): `{data['rsi']:.2f}` (Range: 50-60)\n"
        f"AO: `{data['ao']:,.4f}` ({data['ao_color']} & < 0)\n"
        f"PSAR: `${data['psar']:,.4f}` (Below Candle)\n"
        f"Status: CLOSED GREEN\n"
        f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )

# -----------------------------------------------------------------------------
# WORKER THREADS
# -----------------------------------------------------------------------------
def run_crypto_scanner(bot: TelegramBot):
    symbols = get_crypto_symbols()
    timeframes = ["DAILY", "WEEKLY"]
    logging.info("Crypto Thread Active (%d pairs)", len(symbols))

    while True:
        for timeframe in timeframes:
            for symbol in symbols:
                match = analyze_asset(symbol, timeframe, "CRYPTO")
                if match:
                    alert_msg = format_telegram_alert(match)
                    if bot.send_message(alert_msg):
                        SENT_ALERTS.add((symbol, timeframe, match["timestamp"]))
                time.sleep(0.3)

        logging.info("[Crypto Thread] Scan cycle complete. Sleeping 15 minutes...")
        time.sleep(900)

def run_stock_scanner(bot: TelegramBot):
    symbols = get_stock_symbols()
    timeframes = ["DAILY", "WEEKLY"]
    logging.info("Stock Thread Active (%d tickers)", len(symbols))

    while True:
        for timeframe in timeframes:
            for symbol in symbols:
                match = analyze_asset(symbol, timeframe, "STOCK")
                if match:
                    alert_msg = format_telegram_alert(match)
                    if bot.send_message(alert_msg):
                        SENT_ALERTS.add((symbol, timeframe, match["timestamp"]))
                time.sleep(0.3)

        logging.info("[Stock Thread] Scan cycle complete. Sleeping 15 minutes...")
        time.sleep(900)

# -----------------------------------------------------------------------------
# HEALTH SERVER (UptimeRobot)
# -----------------------------------------------------------------------------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"24/7 Dual Scanner Active")

    def log_message(self, format, *args):
        return

def start_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthCheckHandler)
    server.serve_forever()

# -----------------------------------------------------------------------------
# MAIN ENTRY POINT
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    if not DRY_RUN and (not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID):
        raise ValueError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID environment variables.")

    bot = TelegramBot(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

    # Launch HTTP Server for UptimeRobot
    threading.Thread(target=start_health_server, daemon=True).start()

    # Launch Parallel Scanners
    threading.Thread(target=run_crypto_scanner, args=(bot,), daemon=True).start()
    threading.Thread(target=run_stock_scanner, args=(bot,), daemon=True).start()

    logging.info("Multi-threaded engine online. Both scanners running 24/7.")

    while True:
        time.sleep(3600)
