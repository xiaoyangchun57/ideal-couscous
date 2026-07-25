"""到期检查项 → 排程草稿候选。

本模块只产生调度层的建议数据。它绝不能创建 ``insp_plans``、锁车、预留/扣减
库存，也不能替代运维人员提交和管理人员审批。
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
import json


SUPPORTED_SCHEDULE_TYPES = ("weekly", "monthly", "quarterly", "yearly")


def _parse_date(value):
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def period_for(schedule_type, reference_date):
    """返回排程层支持的周期边界。"""
    if schedule_type == "weekly":
        start = reference_date - timedelta(days=reference_date.weekday())
        return start, start + timedelta(days=6)
    if schedule_type == "monthly":
        start = reference_date.replace(day=1)
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return start, next_month - timedelta(days=1)
    if schedule_type == "quarterly":
        month = ((reference_date.month - 1) // 3) * 3 + 1
        start = reference_date.replace(month=month, day=1)
        next_quarter = (start.replace(day=28) + timedelta(days=100)).replace(day=1)
        next_quarter = next_quarter.replace(month=((next_quarter.month - 1) // 3) * 3 + 1)
        return start, next_quarter - timedelta(days=1)
    if schedule_type == "yearly":
        return reference_date.replace(month=1, day=1), reference_date.replace(month=12, day=31)
    raise ValueError("unsupported schedule type: %s" % schedule_type)


def build_draft_recommendations(db, as_of=None, remind_days=1, user_id=None):
    """读取到期项，按责任人、频次和周期生成可创建的草稿候选。

    ``user_sites`` 是站点责任归属的唯一来源。没有负责人或不属于调度层的频次
    被单独计数，不会被悄悄转为执行任务。
    """
    as_of = as_of or date.today()
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    cutoff = as_of + timedelta(days=max(0, int(remind_days)))

    rows = db.execute(
        """
        SELECT s.id, s.site_id, s.frequency, s.next_due_date, us.user_id
          FROM inspection_schedules s
          LEFT JOIN user_sites us ON us.site_id=s.site_id
          JOIN users u ON u.id=us.user_id AND u.role='operator' AND u.status='active'
         WHERE s.status='active' AND date(s.next_due_date) <= ?
        """,
        (cutoff.isoformat(),),
    ).fetchall()

    grouped = defaultdict(list)
    unsupported = 0
    for row in rows:
        frequency = row["frequency"] or ""
        if frequency not in SUPPORTED_SCHEDULE_TYPES:
            unsupported += 1
            continue
        if user_id is not None and int(row["user_id"]) != int(user_id):
            continue
        period_start, period_end = period_for(frequency, as_of)
        grouped[(int(row["user_id"]), frequency, period_start, period_end)].append(row)

    recommendations = []
    for (owner_id, schedule_type, period_start, period_end), due_rows in sorted(grouped.items()):
        existing = db.execute(
            """
            SELECT id, status FROM plan_schedules
             WHERE user_id=? AND schedule_type=? AND period_start=? AND period_end=?
               AND status NOT IN ('rejected', 'archived')
             LIMIT 1
            """,
            (owner_id, schedule_type, period_start.isoformat(), period_end.isoformat()),
        ).fetchone()
        if existing:
            continue

        plan_data = {}
        site_ids = set()
        schedule_ids = []
        for row in due_rows:
            due_date = _parse_date(row["next_due_date"])
            # 草稿的安排日期只能落在当前周期且不可早于今天；避免审批后生成
            # “过去的执行任务”。具体日程仍由排程人编辑确认。
            planned_date = min(max(due_date, as_of), period_end)
            plan_data.setdefault(planned_date.isoformat(), {"sites": [], "notes": ""})
            if row["site_id"] not in plan_data[planned_date.isoformat()]["sites"]:
                plan_data[planned_date.isoformat()]["sites"].append(row["site_id"])
            site_ids.add(row["site_id"])
            schedule_ids.append(row["id"])

        recommendations.append({
            "user_id": owner_id,
            "schedule_type": schedule_type,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "plan_data": plan_data,
            "site_ids": sorted(site_ids),
            "schedule_ids": sorted(schedule_ids),
            "due_item_count": len(schedule_ids),
            "site_count": len(site_ids),
        })

    return {
        "as_of": as_of.isoformat(),
        "cutoff": cutoff.isoformat(),
        "recommendations": recommendations,
        "unsupported_due_items": unsupported,
    }


def create_recommended_drafts(db, as_of=None, remind_days=1):
    """将当前候选写为调度层草稿。

    这是给定时任务使用的唯一写入函数。只插入 ``plan_schedules.status='draft'``；
    不触发执行任务、审批、车辆锁定或物资预留。每次写入前再次检查同一责任人、
    类型和周期是否已有有效排程，确保任务重复运行时保持幂等。
    """
    result = build_draft_recommendations(db, as_of=as_of, remind_days=remind_days)
    created = []
    for candidate in result["recommendations"]:
        exists = db.execute(
            """
            SELECT id FROM plan_schedules
             WHERE user_id=? AND schedule_type=? AND period_start=? AND period_end=?
               AND status NOT IN ('rejected', 'archived')
             LIMIT 1
            """,
            (candidate["user_id"], candidate["schedule_type"],
             candidate["period_start"], candidate["period_end"]),
        ).fetchone()
        if exists:
            continue
        cur = db.execute(
            """
            INSERT INTO plan_schedules
                (user_id, schedule_type, period_start, period_end, plan_data, vehicle_days,
                 spare_parts, work_order_ids, status, remarks, tasks_generated)
            VALUES (?,?,?,?,?,?,?,?,?,?,0)
            """,
            (candidate["user_id"], candidate["schedule_type"], candidate["period_start"],
             candidate["period_end"], json.dumps(candidate["plan_data"], ensure_ascii=False),
             "{}", "[]", "[]", "draft",
             "系统根据到期检查项生成的草稿，请排程人确认日期、车辆和备件后再提交"),
        )
        created.append({"schedule_id": cur.lastrowid, **candidate})
    return {
        "created": created,
        "created_count": len(created),
        "unsupported_due_items": result["unsupported_due_items"],
        "as_of": result["as_of"],
    }
