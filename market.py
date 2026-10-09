"""Public market feeds. No accounts, trading endpoints, or database access."""
import copy
import ctypes
import json
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from state_guard import SCHEMA, VERIFIED_PAIRS, clean_coin, clean_coins, normalize_settings, normalize_cache

RESOURCE_BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
BASE = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else RESOURCE_BASE
DEFAULT_COINS = [
    {"id": cid, "symbol": symbol, "name": name, "pair": symbol + "-USDT"}
    for cid, symbol, name in [
        ("bitcoin", "BTC", "Bitcoin"), ("ethereum", "ETH", "Ethereum"),
        ("arweave", "AR", "Arweave"), ("near", "NEAR", "NEAR Protocol"),
        ("chainlink", "LINK", "Chainlink"), ("ondo-finance", "ONDO", "Ondo"),
    ]
]
CG = "https://api.coingecko.com/api/v3"
OKX = "https://www.okx.com/api/v5"
WS_URL = "wss://ws.okx.com/ws/v5/public"
REFRESH = 900
INTERVALS = (5, 10, 15, 30, 60)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return copy.deepcopy(default)


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def timestamp(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, AttributeError):
        return 0


def age_is_stale(value, now, seconds):
    return not value or now - value > seconds or value - now > 60


def error_label(error):
    # Never include request headers, URLs containing credentials, or raw server bodies.
    if isinstance(error, urllib.error.HTTPError):
        if error.code == 429:
            return "接口限流，稍后自动重试"
        if error.code in (401, 403):
            return "接口拒绝访问，请检查 API Key 或网络限制"
        return "数据源 HTTP " + str(error.code)
    if isinstance(error, (urllib.error.URLError, TimeoutError, OSError)):
        return "网络/代理连接异常，自动重试中"
    return "数据源响应异常（" + type(error).__name__ + "）"


def get_json(url, key=""):
    headers = {"User-Agent": "AduCryptoWatch/1.0", "Accept": "application/json"}
    if key and url.startswith(CG + "/"):
        headers["x-cg-demo-api-key"] = key
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as response:
        return json.load(response)


def protect_key(data, decrypt=False):
    """Windows DPAPI, bound to the current Windows user; never store plaintext."""
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    name = "CryptUnprotectData" if decrypt else "CryptProtectData"
    fn = getattr(ctypes.windll.crypt32, name)
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(target.data)


class Feed:
    def __init__(self, directory=BASE / "state"):
        self.directory = Path(directory)
        defaults = {"coins": copy.deepcopy(DEFAULT_COINS), "refresh_minutes": 15,
                    "opacity": 100, "topmost": True, "geometry": "1180x800+80+80"}
        backup = load_json(self.directory / "settings.last-good.json", None)
        fallback, _ = normalize_settings(backup, defaults)
        primary_path = self.directory / "settings.json"
        raw = load_json(primary_path, None)
        if raw is None and not primary_path.exists() and backup is None:
            raw = defaults
        self.settings, notices = normalize_settings(raw, fallback)
        self.coins = copy.deepcopy(self.settings["coins"])
        self.startup_notice = "；".join(notices)
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.revision = 0
        self.prices = {}
        self.market = {}
        self.market_received = 0
        self.market_ids = []
        self.price_status = "正在连接 OKX…"
        self.market_status = "正在读取综合行情…"
        minutes = self.settings.get("refresh_minutes", 15)
        self.interval = (minutes if minutes in INTERVALS else 15) * 60
        self.next_market_at = 0
        self.market_busy = False
        self.manual_after = 0
        self.blocked_until = 0
        self.key = ""
        try:
            path = self.directory / "coingecko-key.dpapi"
            if path.exists():
                self.key = protect_key(path.read_bytes(), decrypt=True).decode("utf-8")
        except (OSError, UnicodeError):
            self.market_status = "本机 API Key 无法解密，请在设置中重新填写"
            notices.append("本机 API Key 无法解密，请在设置中重新填写")
        cache_path = self.directory / "market-cache.json"
        if cache_path.exists():
            cache, cache_notices = normalize_cache(load_json(cache_path, None), {c["id"] for c in self.coins}, time.time())
            self.market, self.market_received, self.market_ids = cache["market"], cache["received"], cache["ids"]
            notices.extend(cache_notices)
        self.startup_notice = "；".join(dict.fromkeys(notices))
        if self.market_received <= time.time() and set(self.market_ids) == {c["id"] for c in self.coins}:
            self.next_market_at = self.market_received + self.interval

    def persist(self):
        with self.lock:
            self.settings, _ = normalize_settings(self.settings, {"coins": clean_coins(self.coins)})
            self.settings["coins"] = clean_coins(self.coins)
            atomic_json(self.directory / "settings.json", self.settings)
            atomic_json(self.directory / "settings.last-good.json", self.settings)

    def set_key(self, key):
        path = self.directory / "coingecko-key.dpapi"
        path.parent.mkdir(parents=True, exist_ok=True)
        if key:
            path.write_bytes(protect_key(key.encode("utf-8")))
        elif path.exists():
            path.unlink()
        with self.lock:
            self.key = key
            self.market_received = 0
            self.next_market_at = 0
            self.blocked_until = 0
        self.wake.set()

    def set_interval(self, minutes):
        if minutes not in INTERVALS:
            raise ValueError("不支持的刷新间隔")
        with self.lock:
            self.interval = minutes * 60
            self.settings["refresh_minutes"] = minutes
            self.next_market_at = max(self.blocked_until, self.market_received + self.interval)
        self.persist()
        self.wake.set()

    def request_refresh(self):
        with self.lock:
            if self.market_busy or time.time() < max(self.manual_after, self.blocked_until):
                return False
            self.next_market_at = 0
            self.manual_after = time.time() + 30
        self.wake.set()
        return True

    def set_coins(self, coins):
        coins = clean_coins(coins)
        with self.lock:
            old_ids = {c["id"] for c in self.coins}
            self.coins = copy.deepcopy(coins)
            self.revision += 1
            if old_ids != {c["id"] for c in coins}:
                self.next_market_at = max(self.blocked_until, self.manual_after)
        self.persist()
        self.wake.set()

    def snapshot(self):
        with self.lock:
            return copy.deepcopy({"coins": self.coins, "prices": self.prices,
                "market": self.market, "received": self.market_received,
                "price_status": self.price_status, "market_status": self.market_status,
                "next_market_at": self.next_market_at, "market_busy": self.market_busy,
                "manual_after": max(self.manual_after, self.blocked_until), "interval": self.interval,
                "startup_notice": self.startup_notice})

    def search(self, query):
        data = get_json(CG + "/search?" + urllib.parse.urlencode({"query": query}), self.key)
        if not isinstance(data, dict) or not isinstance(data.get("coins"), list):
            raise ValueError("Invalid search response")
        result = []
        for row in data["coins"][:60]:
            try:
                result.append(clean_coin({**row, "pair": ""}))
            except (TypeError, ValueError):
                continue
        return result

    @staticmethod
    def verified_pairs(coin_id, instruments):
        verified = VERIFIED_PAIRS.get(coin_id)
        if not verified:
            return []
        return [verified[1]] if any(isinstance(row, dict) and row.get("instId") == verified[1]
                                   for row in instruments) else []

    def instruments(self):
        result = get_json(OKX + "/public/instruments?instType=SPOT")
        if result.get("code") != "0":
            raise ValueError("OKX instruments rejected")
        return [r for r in result["data"] if r.get("state") == "live" and r.get("quoteCcy") == "USDT"]

    def apply_tickers(self, rows):
        with self.lock:
            pairs = {c["pair"] for c in self.coins if c["pair"]}
            for row in rows:
                pair, price, ts = row.get("instId"), number(row.get("last")), number(row.get("ts"))
                if pair in pairs and price is not None and price > 0 and ts:
                    self.prices[pair] = {"price": price, "ts": ts / 1000}

    def fetch_market(self):
        with self.lock:
            ids = [c["id"] for c in self.coins]
            key = self.key
        if not ids:
            return
        params = {"vs_currency": "usd", "ids": ",".join(ids), "per_page": 250,
                  "price_change_percentage": "24h,7d"}
        rows = get_json(CG + "/coins/markets?" + urllib.parse.urlencode(params), key)
        if not isinstance(rows, list):
            raise ValueError("Invalid CoinGecko response")
        # Only CoinGecko total_volume is used for the displayed global volume.
        market = {}
        for r in rows:
            if r.get("id") in ids:
                market[r["id"]] = {"price": number(r.get("current_price")),
                    "cap": number(r.get("market_cap")), "rank": number(r.get("market_cap_rank")),
                    "volume": number(r.get("total_volume")),
                    "d1": number(r.get("price_change_percentage_24h_in_currency")),
                    "d7": number(r.get("price_change_percentage_7d_in_currency")),
                    "ts": timestamp(r.get("last_updated"))}
        if not market:
            raise ValueError("Empty CoinGecko response")
        received = time.time()
        with self.lock:
            self.market = market
            self.market_received = received
            self.market_ids = ids
            self.market_status = "综合行情已更新" if len(market) == len(ids) else "部分币种无数据，显示 —"
            atomic_json(self.directory / "market-cache.json", {"schema_version": SCHEMA, "market": market, "received": received, "ids": ids})

    def market_loop(self):
        while not self.stop.is_set():
            self.wake.clear()
            with self.lock:
                ids = [c["id"] for c in self.coins]
                delay = self.next_market_at - time.time()
            if not ids:
                with self.lock:
                    self.market_status = "添加自选币种后开始获取行情"
                self.wake.wait(1)
                continue
            if delay > 0:
                with self.lock:
                    if not self.market_status.startswith("综合行情："):
                        self.market_status = f"综合行情每{self.interval // 60}分钟更新"
                self.wake.wait(min(delay, self.interval))
                continue
            with self.lock:
                self.market_busy = True
                self.manual_after = max(self.manual_after, time.time() + 30)
                revision = self.revision
                self.market_status = "正在刷新综合行情…"
            retry_delay = self.interval
            try:
                self.fetch_market()
            except Exception as exc:
                with self.lock:
                    self.market_status = "综合行情：" + error_label(exc)
                    retry_delay = 300
                    if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
                        self.blocked_until = time.time() + 300
            finally:
                with self.lock:
                    self.market_busy = False
                    self.next_market_at = time.time() + retry_delay
                    if revision != self.revision:
                        self.next_market_at = max(self.manual_after, self.blocked_until)
            # Loop back to a wakeable countdown; manual refresh may bring the deadline forward.

    def rest_prices(self):
        result = get_json(OKX + "/market/tickers?instType=SPOT")
        if result.get("code") != "0":
            raise ValueError("OKX ticker request rejected")
        self.apply_tickers(result["data"])

    def price_loop(self):
        try:
            import websocket
        except ImportError:
            websocket = None
        while not self.stop.is_set():
            with self.lock:
                pairs = [c["pair"] for c in self.coins if c["pair"]]
                revision = self.revision
            if not pairs:
                with self.lock:
                    self.price_status = "当前自选使用综合价格"
                self.stop.wait(1)
                continue
            ws = None
            try:
                if websocket is None:
                    self.rest_prices()
                    with self.lock:
                        self.price_status = "OKX 轮询 · 5秒"
                    self.stop.wait(5)
                    continue
                ws = websocket.create_connection(WS_URL, timeout=12)
                ws.settimeout(1)
                ws.send(json.dumps({"op": "subscribe", "args": [
                    {"channel": "tickers", "instId": pair} for pair in pairs]}))
                last_message, last_ping = time.monotonic(), 0
                with self.lock:
                    self.price_status = "OKX 实时推送"
                while not self.stop.is_set() and revision == self.revision:
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        now = time.monotonic()
                        if now - last_message > 40:
                            raise TimeoutError("OKX heartbeat timeout")
                        if now - last_message > 20 and now - last_ping > 15:
                            ws.send("ping")
                            last_ping = now
                        continue
                    if not raw:
                        raise OSError("OKX connection closed")
                    last_message = time.monotonic()
                    if raw == "pong":
                        continue
                    data = json.loads(raw)
                    if data.get("event") == "error":
                        with self.lock:
                            self.price_status = "OKX 部分订阅失败，请检查交易对；旧数据会标为过期"
                    self.apply_tickers(data.get("data", []))
            except Exception as exc:
                with self.lock:
                    self.price_status = "OKX：" + error_label(exc)
                try:
                    self.rest_prices()
                    with self.lock:
                        self.price_status = "OKX 备用轮询 · 正在恢复推送"
                except Exception:
                    pass
                self.stop.wait(5)
            finally:
                if ws:
                    ws.close()

    def start(self):
        for target in (self.market_loop, self.price_loop):
            threading.Thread(target=target, daemon=True).start()

    def close(self):
        self.stop.set()
        self.wake.set()
