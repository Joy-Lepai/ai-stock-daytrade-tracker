from __future__ import annotations


def build_limit_up_market_phase(
    *,
    total: int,
    chase_risk: int = 0,
    wait_confirm: int = 0,
    data_missing: int = 0,
    entered: int = 0,
    missed: int = 0,
    locked: int = 0,
    avoid: int = 0,
    subject: str = "接近漲停 / 漲停",
    empty_target: str = "標的",
) -> dict[str, str]:
    """Describe a limit-up / sharp-rally tape without changing trade model output."""
    if total <= 0:
        return {
            "market_phase": "no_limit_wave",
            "market_phase_label": "無明顯漲停潮",
            "market_phase_summary": f"目前沒有{subject}或漲停鎖住{empty_target}，回到一般強烈買多漏斗與進場雷達。",
            "operator_priority": "不用追逐漲停新聞，先看 VWAP、量比、突破與風控是否完整。",
        }
    if missed > 0:
        return {
            "market_phase": "limit_wave_data_gap",
            "market_phase_label": "漲停潮資料缺口",
            "market_phase_summary": f"有 {total} 檔{subject}，其中 {missed} 檔是真漏抓；優先檢查資料源、候選池與掃描門檻。",
            "operator_priority": "先修資料與候選池，不要手動把漏抓股升級成買多。",
        }
    if data_missing >= max(2, total // 3):
        return {
            "market_phase": "limit_wave_data_unreliable",
            "market_phase_label": "漲停潮資料不足",
            "market_phase_summary": f"有 {total} 檔{subject}，但資料不足比例偏高；不可用來做即時進場判斷。",
            "operator_priority": "等待 price_status 回到 live，且 VWAP、量比、停損價完整後再評估。",
        }
    if total >= 10:
        if chase_risk >= max(3, total // 2):
            return {
                "market_phase": "broad_limit_wave_chase_risk",
                "market_phase_label": "漲停潮但追價風險主導",
                "market_phase_summary": f"今天有 {total} 檔{subject}，且多數被列為追價高風險；盤面很熱，但不代表適合直接追。",
                "operator_priority": "先挑已看到但 high_risk 的股票，等拉回 VWAP 附近不破、停損距離縮小，再重新評估。",
            }
        return {
            "market_phase": "broad_limit_wave",
            "market_phase_label": "漲停潮",
            "market_phase_summary": f"今天有 {total} 檔{subject}，盤面動能明顯；重點是分辨真強續攻與追價陷阱。",
            "operator_priority": "先看鎖漲停與已進 A/B+/B 的標的，其他急拉股等回測 VWAP 或進場雷達轉強。",
        }
    if locked > 0:
        return {
            "market_phase": "locked_limit_watch",
            "market_phase_label": "鎖漲停觀察盤",
            "market_phase_summary": f"目前有 {locked} 檔鎖漲停或買盤堆積；鎖住代表強，但不是追價理由。",
            "operator_priority": "只記錄與觀察，等打開後看 VWAP 是否守住、買盤是否延續。",
        }
    if chase_risk > 0:
        return {
            "market_phase": "selective_chase_risk",
            "market_phase_label": "零星急拉追價風險",
            "market_phase_summary": f"目前有 {total} 檔急拉 / 接近漲停，其中 {chase_risk} 檔追價風險偏高。",
            "operator_priority": "不要追第一波；等拉回不破、停損距離合理或雷達轉強。",
        }
    if wait_confirm > 0:
        return {
            "market_phase": "selective_wait_confirm",
            "market_phase_label": "零星急拉等待確認",
            "market_phase_summary": f"目前有 {total} 檔急拉 / 接近漲停，但多數仍等待 VWAP、量能或突破確認。",
            "operator_priority": "逐檔看下一步條件，不提前追價。",
        }
    if entered > 0:
        return {
            "market_phase": "model_watch_limit_wave",
            "market_phase_label": "急拉股進入模型觀察",
            "market_phase_summary": f"有 {entered} 檔急拉股進入 A/B+/B 觀察層，但仍需進場雷達與風控確認。",
            "operator_priority": "只盯已進模型層標的，逐檔檢查停損距離與 VWAP 守穩。",
        }
    if avoid > 0:
        return {
            "market_phase": "selective_limit_avoid",
            "market_phase_label": "急拉但多方失效",
            "market_phase_summary": f"目前有 {total} 檔急拉 / 接近漲停，其中 {avoid} 檔已被判定多方結構不足或失效。",
            "operator_priority": "只做復盤，不用漲幅掩蓋 VWAP、突破或風險缺口。",
        }
    return {
        "market_phase": "selective_limit_watch",
        "market_phase_label": "零星急拉觀察",
        "market_phase_summary": f"目前有 {total} 檔急拉 / 接近漲停，先列入觀察，不直接升級買多。",
        "operator_priority": "等待回測不破、量能延續或進場雷達轉強。",
    }
