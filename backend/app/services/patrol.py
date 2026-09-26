"""日常巡查业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.services.slope_warning import warning_view

MODULE = "patrol"
SLOPE_MODULE = "geom"
REQUIRED_FIELDS = ["巡查编号", "巡查路段", "巡查人员"]
STATUS_ORDER = ["待巡查", "巡查中", "已巡查", "待复查"]
ACTION_RULES = {"开始巡查": "巡查中", "提交巡查": "已巡查", "发起复查": "待复查"}
NEGATIVE_ACTIONS = []


class PatrolService:
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
            rows = [row for row in rows if keyword in str(row.get("巡查编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"巡查记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于日常巡查可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        if action == "提交巡查":
            self._apply_slope_warning(entry, context or {})
        return entry, f"巡查记录已{action}"

    def _apply_slope_warning(self, entry: dict[str, Any], context: dict[str, Any]) -> None:
        """巡查上报边坡时按统一规则给出预警结论，与边坡列表、监测详情同源。"""
        slope_code = str(
            context.get("边坡编号") or entry.get("边坡编号") or ""
        ).strip()
        slope = None
        if slope_code:
            slope = next(
                (
                    row
                    for row in store.rows(SLOPE_MODULE)
                    if str(row.get("边坡编号", "")) == slope_code
                ),
                None,
            )
        if slope is None:
            return
        view = warning_view(
            slope,
            rate=context.get("变形速率", slope.get("变形速率")),
            support=context.get("支护形式", slope.get("支护形式")),
        )
        entry["边坡编号"] = slope_code
        if context.get("变形速率") is not None:
            entry["变形速率"] = context.get("变形速率")
        entry["预警结论"] = view["预警结论"]
