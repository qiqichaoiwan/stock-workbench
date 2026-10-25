"""AI 顾问(参考层):个股分析+卖点预测;硬规则不受其影响。

原则:所有 AI 输出必须标注「参考建议,非决策」;
LLM 未配置或调用失败 → 降级为纯规则摘要,功能不中断。
"""
import json
import logging

from sqlalchemy.orm import Session

from ..shared import llm_gateway, stock_ref
from .holding_service import holdings_view
from .models import AiAnalysis

log = logging.getLogger("ai_advisor")

DISCLAIMER = "> ⚠️ 以下为 AI 参考建议,非决策依据。硬性止损线由系统代码强制执行,不受本分析影响。"


def analyze_stock(db: Session, user_id: int, code: str, holding_id: int | None = None) -> dict:
    """对用户持仓(或自选)生成 AI 分析与卖出点位预测,存档并返回。"""
    holding_ctx = ""
    if holding_id:
        views = [v for v in holdings_view(db, user_id) if v["id"] == holding_id]
        if views:
            v = views[0]
            holding_ctx = (
                f"持仓信息:买入价{v['buy_price']},现价{v['close']},"
                f"浮盈{v['unrealized_pct']}%,保本价{v['breakeven_price']},"
                f"当前止损价{v['stop_price']}(阶段{v['stop_stage']}),"
                f"持仓最高价{v['highest_price']}。\n"
            )
    quote_hist = stock_ref.quote_history(db, code, days=30)[-20:]
    prices = ",".join(f"{q['close']}" for q in quote_hist)
    rating = stock_ref.pool_rating(db, code)
    rating_ctx = f"系统选股池评级:{rating['rating']}(池:{rating['pool_type']},评分{rating['score']})。\n" if rating else ""

    system = (
        "你是严谨的A股投研助手。基于给定事实数据分析,输出结构化结论。"
        "禁止编造数据中不存在的信息。所有结论是参考建议,最终纪律由系统止损线执行。"
    )
    user_prompt = (
        f"{holding_ctx}{rating_ctx}"
        f"股票代码:{code},近20日收盘序列:{prices}。\n"
        "请输出 JSON:{\"analysis\": \"300字以内分析(趋势位置/相对强弱/风险点)\","
        " \"sell_points\": [{\"price\": 数值, \"reason\": \"一句话\"}],"
        " \"risk_note\": \"一句话风险提示\"}。"
        "sell_points 给出2-3个参考卖出位(如前高、保本上方关键位、动态止损预估),价格必须贴近给定数据。"
    )
    try:
        content, model = llm_gateway.chat(db, user_id, system, user_prompt, json_mode=True)
        data = json.loads(content)
    except Exception as exc:  # noqa: BLE001
        log.warning("ai advisor fallback: %s", exc)
        latest = stock_ref.latest_close(db, code)
        close = latest[1] if latest else None
        views = [v for v in holdings_view(db, user_id) if v.get("code") == code]
        stop = views[0]["stop_price"] if views else None
        data = {
            "analysis": f"AI服务不可用({exc}),以下为规则摘要:现价{close},"
                        f"系统止损参考{stop}。请以止损纪律为准。",
            "sell_points": [{"price": stop, "reason": "系统止损线(硬规则)"}] if stop else [],
            "risk_note": "AI不可用,仅规则输出",
        }
        model = "rule-fallback"

    rec = AiAnalysis(user_id=user_id, code=code, kind="holding_analysis",
                     content=data.get("analysis", ""),
                     sell_points=json.dumps(data.get("sell_points", []), ensure_ascii=False),
                     model_name=model)
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return {"id": rec.id, "code": code, "analysis": data.get("analysis", ""),
            "sell_points": data.get("sell_points", []), "risk_note": data.get("risk_note", ""),
            "model": model, "disclaimer": DISCLAIMER}
