"""边坡挡墙业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

import re
from typing import Any

from app.store import store

MODULE = "geom"
REQUIRED_FIELDS = ["边坡编号", "所属路段", "边坡类型"]
OPTIONAL_FIELDS = ["支护形式", "监测点位", "变形速率", "巡查日期", "边坡状态"]
STATUS_ORDER = ["稳定", "关注", "预警", "危险"]
ACTION_RULES = {"升级关注": "关注", "预警通知": "预警", "封闭处置": "危险"}
NEGATIVE_ACTIONS = []

# 预警判断全模块只有这一份：边坡列表、监测详情、巡查上报都通过
# assess_warning / conclusion_for 取结论，不允许在别处再写一套。
#
# 变形速率临界值（与监测数据同单位）按支护形式分档：支护越弱，容忍的速率越低。
# 每档依次是 关注、预警、危险 三条线，速率达到哪条线就得出哪档结论。
RATE_LIMITS = [
    (("挡墙", "抗滑桩", "桩板墙"), (4.0, 8.0, 16.0)),
    (("锚", "格构"), (3.0, 6.0, 12.0)),
    (("喷", "护面", "浆砌"), (2.5, 5.0, 10.0)),
]
DEFAULT_LIMITS = (2.0, 5.0, 10.0)  # 无支护或支护形式缺失时按最保守口径


def _parse_rate(value: Any) -> float | None:
    """把变形速率解析成数值；只认纯数值（可带 mm/月 一类单位），
    历史占位文本这类取不到数值的一律返回 None，表示沿用历史结论。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    match = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*(?:mm|毫米)?\s*/?\s*(?:月|天|日|d)?", text, re.IGNORECASE)
    return float(match.group(1)) if match else None


def _limits_for(support_form: Any) -> tuple[float, float, float]:
    text = str(support_form or "")
    for keywords, limits in RATE_LIMITS:
        if any(keyword in text for keyword in keywords):
            return limits
    return DEFAULT_LIMITS


def assess_warning(deformation_rate: Any, support_form: Any) -> str | None:
    """按变形速率与支护形式算预警结论；速率不是数值时返回 None，表示沿用历史结论。"""
    rate = _parse_rate(deformation_rate)
    if rate is None:
        return None
    watch, warning, danger = _limits_for(support_form)
    if rate >= danger:
        return "危险"
    if rate >= warning:
        return "预警"
    if rate >= watch:
        return "关注"
    return "稳定"


def conclusion_for(entry: dict[str, Any]) -> str:
    """统一结论：历史巡查结论为底，变形速率到了临界值只升不降。"""
    recorded = str(entry.get("status") or STATUS_ORDER[0])
    if recorded not in STATUS_ORDER:
        recorded = STATUS_ORDER[0]
    assessed = assess_warning(entry.get("变形速率"), entry.get("支护形式"))
    if assessed is None:
        return recorded
    if STATUS_ORDER.index(assessed) > STATUS_ORDER.index(recorded):
        return assessed
    return recorded


class GeomService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("边坡编号", ""))]
        rows = [self._with_conclusion(row) for row in rows]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return self._with_conclusion(entry)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in OPTIONAL_FIELDS:
            if values.get(field) is not None:
                entry[field] = values.get(field)
        entry["status"] = assess_warning(entry.get("变形速率"), entry.get("支护形式")) or STATUS_ORDER[0]
        entry["pending"] = entry["status"] != STATUS_ORDER[-1]
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"边坡挡墙 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于边坡挡墙可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        # 巡查上报的结论也要过同一份预警判断：变形速率到临界值时只升不降。
        entry["status"] = self._escalate(entry, target)
        entry["pending"] = entry["status"] != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"边坡挡墙已{action}"

    def _with_conclusion(self, entry: dict[str, Any]) -> dict[str, Any]:
        """读出口径：返回带统一结论的副本，不改写库里存的历史结论。"""
        return {**entry, "status": conclusion_for(entry)}

    def _escalate(self, entry: dict[str, Any], target: str) -> str:
        assessed = assess_warning(entry.get("变形速率"), entry.get("支护形式"))
        if assessed is not None and STATUS_ORDER.index(assessed) > STATUS_ORDER.index(target):
            return assessed
        return target
