"""边坡挡墙预警判断的唯一实现。

边坡列表、监测详情、巡查上报三处接口统一调用 :func:`evaluate_warning`，
结论只由「变形速率 + 支护形式」决定，三处不再各写一份阈值判断，
保证同一个边坡编号在任何接口拿到的结论一致。

变形速率统一按 mm/d（毫米/天）解释，入参允许携带单位文本
（如 ``"5.0mm/d"``）；解析不出数值时按「稳定」处理，避免脏数据
被误抬成高等级预警。

升档一律按临界值「达到即升」（``>=``）：变形速率恰好等于临界值时
必须进入更高等级，不再出现到了临界值却不升级的情况。
"""
from __future__ import annotations

import re
from typing import Any

# 预警结论等级，顺序即严重程度
WARNING_ORDER = ("稳定", "关注", "预警", "危险")

# 各支护形式下的升档临界变形速率（mm/d）：达到临界值（含）即升档
THRESHOLDS: dict[str, dict[str, float]] = {
    "刚性支护": {"关注": 2.0, "预警": 5.0, "危险": 10.0},
    "柔性支护": {"关注": 3.0, "预警": 8.0, "危险": 15.0},
    "无支护": {"关注": 1.0, "预警": 3.0, "危险": 5.0},
}
# 支护形式缺省或无法辨认时，按工程上最常见的刚性支护（挡墙/抗滑桩）基线处理
DEFAULT_SUPPORT_CATEGORY = "刚性支护"

_FLEXIBLE_KEYWORDS = ("格构", "锚喷", "喷锚", "挂网", "柔性", "土钉")
_NO_SUPPORT_KEYWORDS = ("无支护", "素喷", "抹面", "简单防护", "无防护")
_RATE_PATTERN = re.compile(r"[-+]?\d+(?:\.\d+)?")


def parse_rate(value: Any) -> float | None:
    """把变形速率解析成 mm/d 数值；无法解析时返回 None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _RATE_PATTERN.search(str(value))
    return float(match.group()) if match else None


def support_category(support: Any) -> str:
    """按支护形式文本归类为刚性支护、柔性支护或无支护。"""
    text = str(support or "")
    if any(keyword in text for keyword in _NO_SUPPORT_KEYWORDS):
        return "无支护"
    if any(keyword in text for keyword in _FLEXIBLE_KEYWORDS):
        return "柔性支护"
    return DEFAULT_SUPPORT_CATEGORY


def evaluate_warning(rate: Any, support: Any) -> str:
    """按变形速率与支护形式计算预警结论。

    三处接口共用本函数，禁止在别处再抄一份阈值判断。
    """
    numeric_rate = parse_rate(rate)
    if numeric_rate is None:
        return WARNING_ORDER[0]
    bounds = THRESHOLDS[support_category(support)]
    if numeric_rate >= bounds["危险"]:
        return "危险"
    if numeric_rate >= bounds["预警"]:
        return "预警"
    if numeric_rate >= bounds["关注"]:
        return "关注"
    return "稳定"


def warning_view(row: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """返回附带统一「预警结论」的行副本。

    只追加/覆盖结论字段，不动行内的监测点位、巡查结论等任何原始字段；
    可用 ``overrides`` 传入巡查上报时最新填报的变形速率、支护形式。
    """
    view = dict(row)
    rate = overrides.get("rate", row.get("变形速率"))
    support = overrides.get("support", row.get("支护形式"))
    view["预警结论"] = evaluate_warning(rate, support)
    return view
