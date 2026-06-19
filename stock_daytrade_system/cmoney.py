from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, Iterable, List

from stock_daytrade_system.config import WatchSymbol
from stock_daytrade_system.resilience import record_source_health, retry_sync


@dataclass(frozen=True)
class CMoneyRanking:
    rank: int
    date: str
    symbol: str
    code: str
    name: str
    foreign_buy_million: float
    investment_buy_million: float
    dealers_buy_million: float
    total_buy_million: float


class CMoneyDataError(RuntimeError):
    pass


class CMoneyClient:
    """Client for CMoney public finance leaderboard data."""

    endpoint = "https://www.cmoney.tw/finance/ashx/mainpage.ashx"
    leaderboard_url = "https://www.cmoney.tw/finance/f00065.aspx"
    cmkey = "eKCtnyfWW15mXQqZ6Cg3HQ=="

    def __init__(self, timeout: int = 20, pause_seconds: float = 0.2) -> None:
        self.timeout = timeout
        self.pause_seconds = pause_seconds

    def fetch_institutional_buy_rankings(self, limit: int = 30) -> List[CMoneyRanking]:
        payload = urllib.parse.urlencode(
            {
                "action": "GetUltraSaleLeaderboard",
                "cmkey": self.cmkey,
                "cmType": "0",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={
                "User-Agent": "Mozilla/5.0 AI-stock-research/0.1",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": self.leaderboard_url,
            },
            method="POST",
        )
        def operation() -> list:
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8-sig"))
            except Exception as exc:
                raise CMoneyDataError(f"failed to fetch CMoney institutional rankings: {exc}") from exc

        data = retry_sync(
            operation,
            source="c_money",
            operation_name="CMoney institutional rankings",
        )

        if not isinstance(data, list) or len(data) < 2 or not isinstance(data[1], list):
            record_source_health("c_money", "ERROR", failure_count=1, error=f"unexpected response: {data!r}")
            raise CMoneyDataError(f"unexpected CMoney response: {data!r}")

        date = str(data[0])
        rows = sorted(
            data[1],
            key=lambda item: _float(item.get("NearDayThreeInstitutionalInvestors")),
            reverse=True,
        )
        rankings: List[CMoneyRanking] = []
        for item in rows:
            total = _float(item.get("NearDayThreeInstitutionalInvestors"))
            if total <= 0:
                continue
            code = str(item.get("CommKey", "")).strip()
            name = str(item.get("CommName", "")).strip()
            if not code or not name:
                continue
            rankings.append(
                CMoneyRanking(
                    rank=len(rankings) + 1,
                    date=date,
                    symbol=f"{code}.TW",
                    code=code,
                    name=name,
                    foreign_buy_million=_float(item.get("NearDayForeignCapital")),
                    investment_buy_million=_float(item.get("NearDayInvestmentTrust")),
                    dealers_buy_million=_float(item.get("NearDayDealers")),
                    total_buy_million=total,
                )
            )
            if len(rankings) >= limit:
                break
        time.sleep(self.pause_seconds)
        record_source_health("c_money", "OK", success_count=len(rankings), message="CMoney 法人排行擷取成功。")
        return rankings


def rankings_by_symbol(rankings: Iterable[CMoneyRanking]) -> Dict[str, CMoneyRanking]:
    return {item.symbol: item for item in rankings}


def merge_cmoney_symbols(symbols: Iterable[WatchSymbol], rankings: Iterable[CMoneyRanking]) -> List[WatchSymbol]:
    result = list(symbols)
    seen = {item.symbol for item in result}
    for item in rankings:
        if item.symbol in seen:
            continue
        result.append(WatchSymbol(symbol=item.symbol, name=item.name, sector="institutional_buy"))
        seen.add(item.symbol)
    return result


def _float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
