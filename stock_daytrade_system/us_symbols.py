from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Dict, List


@dataclass(frozen=True)
class USSymbolInfo:
    symbol: str
    name_en: str
    name_zh: str
    short_name_zh: str
    sector_en: str
    sector_zh: str
    industry_en: str
    industry_zh: str
    description_zh: str
    is_etf: bool = False
    is_active: bool = True

    def display_name(self) -> str:
        return f"{self.symbol}｜{self.short_name_zh}｜{self.name_en}"

    def subtitle(self) -> str:
        return f"{self.sector_zh}｜{self.description_zh}"


US_WATCHLIST: List[USSymbolInfo] = [
    USSymbolInfo("AAPL", "Apple Inc.", "蘋果", "蘋果", "Consumer Electronics", "消費電子", "Consumer Electronics", "消費電子", "iPhone、Mac 與消費電子龍頭"),
    USSymbolInfo("MSFT", "Microsoft Corporation", "微軟", "微軟", "Software", "軟體", "Cloud and AI Software", "雲端與 AI 軟體", "Windows、Office、Azure 雲端與 AI 軟體公司"),
    USSymbolInfo("NVDA", "NVIDIA Corporation", "輝達", "輝達", "Semiconductors", "半導體", "AI Accelerators", "AI 晶片", "AI 晶片、GPU 與資料中心加速運算龍頭"),
    USSymbolInfo("TSLA", "Tesla, Inc.", "特斯拉", "特斯拉", "Electric Vehicles", "電動車", "Automobiles", "電動車", "電動車、能源儲存與自動駕駛公司"),
    USSymbolInfo("AMD", "Advanced Micro Devices, Inc.", "超微半導體", "超微", "Semiconductors", "半導體", "CPUs and GPUs", "CPU / GPU", "CPU、GPU 與資料中心晶片公司"),
    USSymbolInfo("AMZN", "Amazon.com, Inc.", "亞馬遜", "亞馬遜", "Internet Retail", "電商與雲端", "E-Commerce and Cloud", "電商與雲端", "電商、雲端 AWS 與數位服務公司"),
    USSymbolInfo("META", "Meta Platforms, Inc.", "Meta", "Meta", "Social Platforms", "社群平台", "AI and Social Media", "AI 與社群", "Facebook、Instagram、WhatsApp 與 AI / 元宇宙公司"),
    USSymbolInfo("GOOGL", "Alphabet Inc.", "Alphabet / Google", "Google", "Internet Services", "網路服務", "Search and Cloud", "搜尋與雲端", "Google 搜尋、YouTube、雲端與 AI 公司"),
    USSymbolInfo("AVGO", "Broadcom Inc.", "博通", "博通", "Semiconductors", "半導體", "Networking Chips", "網通晶片", "半導體、網通晶片與基礎架構軟體公司"),
    USSymbolInfo("SMCI", "Super Micro Computer, Inc.", "美超微", "美超微", "AI Servers", "AI 伺服器", "High Performance Computing", "高效能運算", "AI 伺服器與高效能運算硬體公司"),
    USSymbolInfo("PLTR", "Palantir Technologies Inc.", "Palantir", "Palantir", "Software", "軟體", "Data Analytics and AI", "資料分析與 AI", "資料分析、政府與企業 AI 軟體公司"),
    USSymbolInfo("MSTR", "MicroStrategy Incorporated", "MicroStrategy", "MicroStrategy", "Cryptocurrency Related", "加密貨幣概念", "Enterprise Software and Bitcoin", "企業軟體與比特幣", "企業軟體與比特幣概念股"),
    USSymbolInfo("COIN", "Coinbase Global, Inc.", "Coinbase", "Coinbase", "Cryptocurrency Related", "加密貨幣概念", "Crypto Exchange", "加密貨幣交易平台", "加密貨幣交易平台"),
    USSymbolInfo("NFLX", "Netflix, Inc.", "Netflix", "Netflix", "Streaming Media", "串流影音", "Entertainment", "影音娛樂", "串流影音與內容平台"),
    USSymbolInfo("QQQ", "Invesco QQQ Trust", "納斯達克 100 ETF", "QQQ", "ETF", "指數型 ETF", "Nasdaq 100 ETF", "Nasdaq 100 ETF", "追蹤 Nasdaq 100 指數的 ETF", is_etf=True),
    USSymbolInfo("SPY", "SPDR S&P 500 ETF Trust", "標普 500 ETF", "SPY", "ETF", "指數型 ETF", "S&P 500 ETF", "S&P 500 ETF", "追蹤 S&P 500 指數的 ETF", is_etf=True),
]


def us_watchlist() -> List[USSymbolInfo]:
    return list(US_WATCHLIST)


def us_symbol_map() -> Dict[str, USSymbolInfo]:
    return {item.symbol: item for item in US_WATCHLIST}


def us_symbol_rows(now: datetime | None = None) -> List[dict]:
    timestamp = (now or datetime.now()).isoformat(timespec="seconds")
    rows = []
    for item in US_WATCHLIST:
        data = asdict(item)
        data["is_etf"] = int(item.is_etf)
        data["is_active"] = int(item.is_active)
        data["created_at"] = timestamp
        data["updated_at"] = timestamp
        rows.append(data)
    return rows
