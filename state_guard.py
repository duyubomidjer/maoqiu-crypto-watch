"""Versioned, credential-free settings and cache validation."""
import copy
import math
import re

SCHEMA = 1
# Existing reviewed asset identities; symbols alone never grant an OKX mapping.
VERIFIED_PAIRS = {
    "bitcoin": ("BTC", "BTC-USDT"), "ethereum": ("ETH", "ETH-USDT"),
    "arweave": ("AR", "AR-USDT"), "near": ("NEAR", "NEAR-USDT"),
    "chainlink": ("LINK", "LINK-USDT"), "ondo-finance": ("ONDO", "ONDO-USDT"),
}
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, OverflowError):
        return None


def clean_coin(coin):
    if not isinstance(coin, dict):
        raise ValueError("币种记录格式无效")
    if any(not isinstance(coin.get(k), str) for k in ("id", "symbol", "name", "pair")):
        raise ValueError("币种字段格式无效")
    cid, symbol, name = coin["id"].strip(), coin["symbol"].strip(), coin["name"].strip()
    if not ID_PATTERN.fullmatch(cid) or not symbol or len(symbol) > 32 or not name or len(name) > 200:
        raise ValueError("币种标识格式无效")
    if any(ord(ch) < 32 for ch in symbol+name):
        raise ValueError("币种名称包含无效字符")
    verified = VERIFIED_PAIRS.get(cid)
    pair = coin["pair"] if verified and coin["pair"] == verified[1] else ""
    return {"id": cid, "symbol": verified[0] if verified else symbol.upper(), "name": name, "pair": pair}


def clean_coins(coins):
    if not isinstance(coins, list) or len(coins) > 100:
        raise ValueError("自选列表格式无效或超过100个")
    result = [clean_coin(c) for c in coins]
    if len({c["id"] for c in result}) != len(result):
        raise ValueError("自选币种重复")
    return result


def settings_valid_shape(data):
    return isinstance(data, dict) and type(data.get("schema_version", 0)) is int and data.get("schema_version", 0) in (0, SCHEMA)


def normalize_settings(raw, defaults):
    """Keep valid fields/coins, recover invalid fields from validated defaults."""
    output = copy.deepcopy(defaults)
    output["schema_version"] = SCHEMA
    notes = []
    if not settings_valid_shape(raw):
        return output, ["配置结构或版本无效，已恢复"]
    if "coins" in raw:
        items, seen = [], set()
        coins = raw["coins"]
        if isinstance(coins, list):
            for coin in coins[:100]:
                try:
                    cleaned = clean_coin(coin)
                    if cleaned["id"] in seen:
                        raise ValueError("duplicate")
                    items.append(cleaned)
                    seen.add(cleaned["id"])
                    if cleaned["pair"] != coin["pair"]:
                        notes.append("未核验交易对已改用综合价格")
                except (ValueError, TypeError):
                    notes.append("已跳过无效自选记录")
            if items or coins == []:
                output["coins"] = items
            if len(coins) > 100:
                notes.append("自选超出100项，已保留前100项中的有效记录")
        else:
            notes.append("自选列表无效，已恢复")
    for key in ("refresh_minutes", "opacity", "topmost", "geometry", "orb_position"):
        if key not in raw:
            continue
        value = raw[key]
        valid = False
        if key == "refresh_minutes":
            valid = type(value) is int and value in (5, 10, 15, 30, 60)
        elif key == "opacity":
            value = finite(value)
            valid = value is not None and 55 <= value <= 100
        elif key == "topmost":
            valid = type(value) is bool
        elif key == "geometry" and isinstance(value, str):
            match = re.fullmatch(r"(\d{2,5})x(\d{2,5})([+-]\d{1,6})([+-]\d{1,6})", value)
            valid = bool(match and 320 <= int(match[1]) <= 8192 and 240 <= int(match[2]) <= 8192
                         and abs(int(match[3])) <= 100000 and abs(int(match[4])) <= 100000)
        elif key == "orb_position":
            valid = isinstance(value, list) and len(value) == 2 and all(
                type(n) is int and abs(n) <= 100000 for n in value)
        if valid:
            output[key] = value
        else:
            notes.append("已恢复无效窗口设置")
    # Unknown fields (including any accidentally pasted credentials) are not persisted.
    return output, list(dict.fromkeys(notes))


def normalize_cache(raw, allowed_ids, now):
    result = {"schema_version": SCHEMA, "market": {}, "received": 0, "ids": []}
    if not settings_valid_shape(raw) or not isinstance(raw.get("market"), dict):
        return result, ["行情缓存无效，已重新获取"]
    notes = []
    for cid, row in raw["market"].items():
        if cid not in allowed_ids:
            continue
        ts = finite(row.get("ts")) if isinstance(row, dict) else None
        if ts is None or not 0 < ts <= now+60:
            notes.append("已跳过无效行情缓存")
            continue
        clean = {key: finite(row.get(key)) for key in ("price", "cap", "rank", "volume", "d1", "d7")}
        if any(row.get(key) is not None and value is None for key, value in clean.items()):
            notes.append("已修复无效行情缓存值")
        for key in ("price", "cap", "rank", "volume"):
            n = clean[key]
            if n is not None and (n < 0 or (key == "rank" and (n < 1 or not n.is_integer()))):
                clean[key] = None
                notes.append("已修复无效行情缓存值")
        clean["ts"] = ts
        result["market"][cid] = clean
    ids, received = raw.get("ids"), finite(raw.get("received"))
    if isinstance(ids, list) and all(isinstance(cid, str) and ID_PATTERN.fullmatch(cid) for cid in ids):
        result["ids"] = ids
    else:
        notes.append("行情缓存索引无效，已重新获取")
    if received is not None and 0 < received <= now and not notes:
        result["received"] = received
    return result, list(dict.fromkeys(notes))
