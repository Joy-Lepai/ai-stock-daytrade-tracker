from __future__ import annotations


SECTOR_LABELS = {
    "semiconductor": "半導體 / semiconductor",
    "electronics": "電子代工 / electronics",
    "ai_server": "AI伺服器 / ai_server",
    "thermal": "散熱 / thermal",
    "shipping": "航運 / shipping",
    "airline": "航空 / airline",
    "financial": "金融 / financial",
    "biotech": "生技醫療 / biotech",
    "networking": "網通 / networking",
    "memory": "記憶體 / memory",
    "pcb": "PCB / pcb",
    "telecom": "電信 / telecom",
    "materials": "水泥建材 / materials",
    "plastics": "塑化 / plastics",
    "steel": "鋼鐵 / steel",
    "consumer": "民生消費 / consumer",
    "retail": "零售通路 / retail",
    "industrial": "工業自動化 / industrial",
    "textile": "紡織 / textile",
    "institutional_buy": "法人買超 / institutional_buy",
    "unknown": "未分類 / unknown",
}


def sector_label(sector: str) -> str:
    return SECTOR_LABELS.get(sector, f"{sector} / {sector}")


def stock_label(name: str, symbol: str) -> str:
    return f"{name} / {symbol}"
