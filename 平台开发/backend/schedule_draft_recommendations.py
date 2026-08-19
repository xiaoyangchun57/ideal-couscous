"""系统性巡检异常复查建议。"""

from datetime import date, datetime, timedelta
import json


SYSTEMIC_WINDOW_DAYS = 30
SYSTEMIC_MIN_OCCURRENCES = 2


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


def _schedule_contains_site(plan_data, site_id):
    for day_data in (plan_data or {}).values():
        if isinstance(day_data, dict) and site_id in (day_data.get("sites") or []):
            return True
    return False


def _has_open_follow_up(db, user_id, site_id, anomaly_type):
    """同一异常的复查草稿/计划存在时，不再重复建议。"""
    rows = db.execute(
        """
        SELECT plan_data, remarks FROM plan_schedules
         WHERE user_id=? AND status NOT IN ('rejected', 'archived')
           AND remarks LIKE '%系统性异常复查%'
        """,
        (user_id,),
    ).fetchall()
    marker = "异常类型：%s" % anomaly_type
    for row in rows:
        try:
            plan_data = json.loads(row["plan_data"] or "{}")
        except (TypeError, ValueError):
            continue
        if marker in (row["remarks"] or "") and _schedule_contains_site(plan_data, site_id):
            return True
    return False


def build_systemic_follow_up_recommendations(db, as_of=None):
    """识别系统性巡检异常，生成“复查草稿”候选而不是执行任务。

    已确认规则：同一站点、同类巡检异常在 30 天内达到 2 次；同类未完成
    复查排程存在时抑制重复建议。
    """
    as_of = as_of or date.today()
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    window_start = as_of - timedelta(days=SYSTEMIC_WINDOW_DAYS - 1)
    rows = db.execute(
        """
        SELECT us.user_id, wo.site_id, wo.event_type AS anomaly_type,
               COUNT(*) AS occurrence_count, MAX(wo.created_at) AS latest_at
          FROM work_orders wo
          JOIN user_sites us ON us.site_id=wo.site_id
          JOIN users u ON u.id=us.user_id AND u.status='active'
             AND (u.role='operator' OR EXISTS (
                   SELECT 1 FROM user_roles ur
                    WHERE ur.user_id=u.id AND ur.role='operator'
             ))
         WHERE wo.source='inspection' AND date(wo.created_at) >= ?
         GROUP BY us.user_id, wo.site_id, wo.event_type
        HAVING COUNT(*) >= ?
        """,
        (window_start.isoformat(), SYSTEMIC_MIN_OCCURRENCES),
    ).fetchall()
    period_start, period_end = period_for("weekly", as_of)
    recommendations = []
    for row in rows:
        user_id, site_id, anomaly_type = int(row["user_id"]), int(row["site_id"]), row["anomaly_type"] or "巡检异常"
        if _has_open_follow_up(db, user_id, site_id, anomaly_type):
            continue
        site = db.execute("SELECT name FROM sites WHERE id=?", (site_id,)).fetchone()
        recommendations.append({
            "user_id": user_id,
            "site_id": site_id,
            "site_name": site["name"] if site else "站点%s" % site_id,
            "anomaly_type": anomaly_type,
            "occurrence_count": int(row["occurrence_count"]),
            "latest_at": row["latest_at"],
            "window_days": SYSTEMIC_WINDOW_DAYS,
            "schedule_type": "weekly",
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "plan_data": {as_of.isoformat(): {"sites": [site_id], "notes": "系统性异常复查"}},
        })
    return {"as_of": as_of.isoformat(), "recommendations": recommendations}


def create_systemic_follow_up_draft(db, recommendation):
    """将确认的一条系统性异常建议落为可编辑草稿，且仅此写入。"""
    if _has_open_follow_up(db, recommendation["user_id"], recommendation["site_id"], recommendation["anomaly_type"]):
        return None
    remarks = "系统性异常复查：异常类型：%s；近%d天出现%d次；请确认复查日期、车辆和备件后提交" % (
        recommendation["anomaly_type"], recommendation["window_days"], recommendation["occurrence_count"])
    cur = db.execute(
        """
        INSERT INTO plan_schedules
            (user_id, schedule_type, period_start, period_end, plan_data, vehicle_days,
             spare_parts, work_order_ids, status, remarks, tasks_generated)
        VALUES (?,?,?,?,?,?,?,?,?,?,0)
        """,
        (recommendation["user_id"], "weekly", recommendation["period_start"],
         recommendation["period_end"], json.dumps(recommendation["plan_data"], ensure_ascii=False),
         "{}", "[]", "[]", "draft", remarks),
    )
    return cur.lastrowid
