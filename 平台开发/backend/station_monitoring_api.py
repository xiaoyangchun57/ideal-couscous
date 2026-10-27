"""Station-monitoring HTTP API, configuration, query, and projection orchestration."""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from flask import g, jsonify, request

from hj212_parser import FrameError as HJ212FrameError, parse_hj212_frame
from station_ingest_retention import RetentionError, read_raw_evidence
from station_monitoring import (
    BUSINESS_PROJECTION_VERSION as MONITORING_BUSINESS_PROJECTION_VERSION,
    business_slot_deadline as monitoring_business_slot_deadline,
    business_slot_diagnostics as monitoring_business_slot_diagnostics,
    current_expected_factor_selections_batch as monitoring_expected_factor_selections_batch,
    current_factor_configurations as monitoring_factor_configurations,
    current_factor_configurations_batch as monitoring_factor_configurations_batch,
    expected_business_slots as monitoring_expected_business_slots,
    is_formal_hj212_source as monitoring_is_formal_hj212_source,
    latest_cycle as monitoring_latest_cycle,
    latest_values as monitoring_latest_values,
    latest_values_and_cycles_batch as monitoring_latest_values_and_cycles_batch,
    next_business_slot as monitoring_next_business_slot,
    result_trend_page as monitoring_result_trend_page,
    trend_slots as monitoring_trend_slots,
)

_shared = None
_STATION_MONITORING_SITE_TYPE_LABELS = {}


def _dependency(name):
    if _shared is None:
        raise RuntimeError("station monitoring API has not been registered")
    return _shared[name]


def get_db():
    return _dependency("get_db")()


def _database_path():
    return _dependency("database_path")()


def _app_context():
    return _dependency("app_context")()


def _active_master_site_ids(*args, **kwargs):
    return _dependency("active_master_site_ids")(*args, **kwargs)


def _active_master_site_predicate(*args, **kwargs):
    return _dependency("active_master_site_predicate")(*args, **kwargs)


def _active_operator_site_ids(*args, **kwargs):
    return _dependency("active_operator_site_ids")(*args, **kwargs)


def _current_service_site_ids(*args, **kwargs):
    return _dependency("current_service_site_ids")(*args, **kwargs)


def _current_service_site_predicate(*args, **kwargs):
    return _dependency("current_service_site_predicate")(*args, **kwargs)


def _station_monitoring_access_mode():
    return _dependency("station_monitoring_access_mode")()


def _has_any_role(*args, **kwargs):
    return _dependency("has_any_role")(*args, **kwargs)


def _normalize_user_roles(*args, **kwargs):
    return _dependency("normalize_user_roles")(*args, **kwargs)


def _roles_for_user(*args, **kwargs):
    return _dependency("roles_for_user")(*args, **kwargs)


def _site_access_denied(*args, **kwargs):
    return _dependency("site_access_denied")(*args, **kwargs)


def _site_can_calibrate(*args, **kwargs):
    return _dependency("site_can_calibrate")(*args, **kwargs)


def _site_reporting_required_at(*args, **kwargs):
    return _dependency("site_reporting_required_at")(*args, **kwargs)


def _site_responsibility(*args, **kwargs):
    return _dependency("site_responsibility")(*args, **kwargs)


def _sqlite_busy(*args, **kwargs):
    return _dependency("sqlite_busy")(*args, **kwargs)


def _station_service_policy_events_batch(*args, **kwargs):
    return _dependency("station_service_policy_events_batch")(*args, **kwargs)


def _station_service_policy_payload(*args, **kwargs):
    return _dependency("station_service_policy_payload")(*args, **kwargs)


def _station_service_policy_rows_batch(*args, **kwargs):
    return _dependency("station_service_policy_rows_batch")(*args, **kwargs)


def _monitoring_read_access_denied(db, site_id, action='读取站点监测'):
    denied = _site_access_denied(site_id, action)
    if not denied or _station_monitoring_access_mode() != 'public':
        return denied
    visible = db.execute(
        f"SELECT 1 FROM sites s WHERE s.id=? "
        f"AND {_active_master_site_predicate(db, 's')} "
        f"AND {_current_service_site_predicate(db, 's')}",
        (site_id,),
    ).fetchone()
    return None if visible else denied


def _monitoring_site_context(db, site_id, *, allow_public_read=False):
    denied = (_monitoring_read_access_denied(db, site_id)
              if allow_public_read else _site_access_denied(site_id, '读取站点监测'))
    if denied:
        return None, denied
    if not db.execute('SELECT 1 FROM sites WHERE id=?', (site_id,)).fetchone():
        return None, (jsonify({'error': '站点不存在', 'code': 'MONITORING_SITE_NOT_FOUND'}), 404)
    now = datetime.now(timezone.utc).isoformat(timespec='microseconds')
    profile = db.execute(
        """SELECT profile.* FROM monitoring_endpoint_profiles profile
           JOIN trusted_endpoints endpoint ON endpoint.id=profile.endpoint_id AND endpoint.enabled=1 AND endpoint.endpoint_state='bound' AND endpoint.business_site_id IS NOT NULL AND endpoint.business_site_id=profile.business_site_id
           WHERE profile.business_site_id=? AND profile.enabled=1 AND profile.effective_from<=?
             AND (effective_to IS NULL OR effective_to>?)
           ORDER BY profile.effective_from DESC LIMIT 1""", (site_id, now, now)
    ).fetchone()
    if not profile:
        profile = db.execute(
            """SELECT id AS endpoint_id,business_site_id,NULL AS instrument_asset_code,
                      'Asia/Shanghai' AS timezone,NULL AS expected_interval_seconds
               FROM trusted_endpoints
               WHERE business_site_id=? AND enabled=1 AND endpoint_state='bound'
               ORDER BY id LIMIT 1""", (site_id,),
        ).fetchone()
    if not profile:
        return None, (jsonify({'error': '站点尚未接入监测端点', 'code': 'MONITORING_NOT_CONNECTED'}), 409)
    return profile, None


def _monitoring_freshness(last_time, interval, tolerance=0):
    if not interval:
        return {'state': 'unknown', 'reason_code': 'unconfigured'}
    if not last_time:
        return {'state': 'unknown', 'reason_code': 'no_observation'}
    try:
        value = datetime.fromisoformat(str(last_time))
        now = datetime.now(value.tzinfo) if value.tzinfo else datetime.now()
        allowance = int(interval) + int(tolerance or 0)
        return {'state': 'fresh' if (now - value).total_seconds() <= allowance else 'stale', 'reason_code': None}
    except ValueError:
        return {'state': 'unknown', 'reason_code': 'invalid_time'}


def _monitoring_query_time(value, field):
    if not value:
        return None, None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None, (jsonify({'error': f'{field}必须是ISO时间', 'code': 'MONITORING_INVALID_TIME'}), 400)
    if parsed.tzinfo is None:
        return None, (jsonify({'error': f'{field}必须包含时区', 'code': 'MONITORING_INVALID_TIME'}), 400)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat(), None


def _monitoring_trend_cursor_encode(boundary):
    if boundary is None:
        return None
    payload = json.dumps({
        'observed_at': boundary[0], 'endpoint_id': boundary[1],
        'instrument_key': boundary[2],
    }, separators=(',', ':')).encode('utf-8')
    return base64.urlsafe_b64encode(payload).decode('ascii').rstrip('=')


def _monitoring_trend_cursor_decode(value):
    if not value:
        return None, None
    try:
        padded = value + '=' * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode('utf-8'))
        observed_at, error = _monitoring_query_time(payload.get('observed_at'), 'cursor')
        if error:
            raise ValueError('invalid cursor time')
        endpoint_id = int(payload['endpoint_id'])
        instrument_key = payload['instrument_key']
        if endpoint_id < 1 or not isinstance(instrument_key, str):
            raise ValueError('invalid cursor boundary')
        return (observed_at, endpoint_id, instrument_key), None
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None, (jsonify({
            'error': '趋势游标无效', 'code': 'MONITORING_INVALID_CURSOR',
        }), 400)


_MONITORING_UNSET = object()
_MONITORING_AUTH_DIAGNOSTIC_HOURS = 8


def _monitoring_auth_diagnostic_cutoff():
    return (datetime.now(timezone.utc).replace(microsecond=0)
            - timedelta(hours=_MONITORING_AUTH_DIAGNOSTIC_HOURS)).isoformat()


def _station_monitoring_projection(db, site_id, *, site=None, profile=_MONITORING_UNSET,
                                   raw=_MONITORING_UNSET, configs=None,
                                   values=None, include_latest_complete=False, responsibility=None,
                                   cycle=None, factor_states=None, auth_diagnostic=_MONITORING_UNSET,
                                   expected_factor_selection=_MONITORING_UNSET):
    """Build the first-wave read-only station monitoring contract from normalized facts."""
    site = site or db.execute(
        "SELECT id, code, name, type, district, address, river, basin, gps_lat AS lat, gps_lng AS lng, manager, phone FROM sites WHERE id=?",
        (site_id,),
    ).fetchone()
    if not site:
        return None
    now = datetime.now(timezone.utc).isoformat(timespec='microseconds')
    if profile is _MONITORING_UNSET:
        profile = db.execute(
            """SELECT profile.*, endpoint.station_code AS endpoint_station_code
               FROM monitoring_endpoint_profiles profile
               JOIN trusted_endpoints endpoint ON endpoint.id=profile.endpoint_id
                 AND endpoint.enabled=1 AND endpoint.endpoint_state='bound' AND endpoint.business_site_id IS NOT NULL
                 AND endpoint.business_site_id=profile.business_site_id
               WHERE profile.business_site_id=? AND profile.enabled=1 AND profile.effective_from<=?
                 AND (profile.effective_to IS NULL OR profile.effective_to>?)
               ORDER BY profile.effective_from DESC LIMIT 1""", (site_id, now, now)
        ).fetchone()
        if profile is None:
            profile = db.execute(
                """SELECT id AS endpoint_id, station_code AS endpoint_station_code
                   FROM trusted_endpoints
                   WHERE enabled=1 AND endpoint_state='bound' AND business_site_id=? LIMIT 1""", (site_id,)
            ).fetchone()
    responsibility = (responsibility if responsibility is not None
                      else _site_responsibility(db, site_id))
    if expected_factor_selection is _MONITORING_UNSET:
        expected_factor_selection = monitoring_expected_factor_selections_batch(
            db, [site_id], at_time=now).get(site_id)
    expected_factor_count = len(expected_factor_selection.get('items') or []) \
        if expected_factor_selection else 0
    unavailable_reason = ('time_configuration_missing' if expected_factor_selection
                          else 'expected_factors_unconfigured')
    base = {
        'id': site['id'], 'site_id': site['id'], 'code': site['code'], 'name': site['name'],
        'type': site['type'], 'district': site['district'] or '', 'address': site['address'] or '',
        'river': site['river'] or '', 'basin': site['basin'] or '', 'lat': site['lat'], 'lng': site['lng'],
        'manager': responsibility['manager'], 'phone': responsibility['phone'],
        'responsible_people': responsibility['responsible_people'],
        'monitoring_status_label': '未接入', 'reason_code': 'not_connected', 'monitoring_reason': '未配置启用的监测身份',
        'last_received_at': None, 'last_communication_at': None,
        'last_transport_received_at': None, 'recent_authentication_failures': 0,
        'last_valid_observation_at': None, 'published_factor_count': 0,
        'monitoring_status': 'not_connected', 'monitoring_load_state': 'ready',
        'expected_factors_configured': bool(expected_factor_selection),
        'expected_factor_version': (
            expected_factor_selection.get('version') if expected_factor_selection else 0),
        'communication': {'status': 'not_connected', 'last_received_at': None},
        'latest_cycle': {'status': 'not_configured', 'reason_code': unavailable_reason,
                         'scheduled_at': None, 'expected_count': expected_factor_count,
                         'arrived_count': 0,
                         'formed_count': 0, 'missing_count': 0,
                         'review_required_count': 0, 'sufficient_for_business': False},
    }
    if not profile:
        return base
    if raw is _MONITORING_UNSET:
        raw = db.execute(
            """SELECT latest_authenticated_at AS received_at
               FROM station_ingest_current_state WHERE endpoint_id=?
               ORDER BY latest_authenticated_at DESC LIMIT 1""", (profile['endpoint_id'],)
        ).fetchone()
    if auth_diagnostic is _MONITORING_UNSET:
        station_code = (profile['endpoint_station_code']
                        if 'endpoint_station_code' in profile.keys() else None)
        auth_diagnostic = (db.execute(
            """SELECT COUNT(*) AS failure_count, MAX(received_at) AS last_transport_received_at
               FROM station_ingest_recent_receipts
               WHERE station_code=? AND authentication_status='credential_failed'
                 AND received_at>=?""",
            (station_code, _monitoring_auth_diagnostic_cutoff()),
        ).fetchone() if station_code else None)
    last_communication = raw['received_at'] if raw and raw['received_at'] else None
    authentication_failures = int(auth_diagnostic['failure_count'] or 0) if auth_diagnostic else 0
    last_transport_received_at = (auth_diagnostic['last_transport_received_at']
                                  if auth_diagnostic else None)
    configs = configs if configs is not None else monitoring_factor_configurations(
        db, site_id, at_time=now)
    configs = [item for item in configs if item.get('endpoint_id') == profile['endpoint_id']]
    values = values if values is not None else monitoring_latest_values(
        db, site_id, at_time=now)
    values = [item for item in values if item.get('endpoint_id') == profile['endpoint_id']]
    cycle = (cycle if cycle is not None else monitoring_latest_cycle(
        db, site_id, at_time=now, configurations=configs,
        include_latest_complete=include_latest_complete))
    communication_freshness = _monitoring_freshness(last_communication, 1800)
    communication_status = ('awaiting' if not last_communication else
                            'normal' if communication_freshness['state'] == 'fresh' else 'stale')
    base['last_received_at'] = last_communication
    base['last_communication_at'] = last_communication
    base['last_transport_received_at'] = last_transport_received_at
    base['recent_authentication_failures'] = authentication_failures
    base['communication'] = {
        'status': ('authentication_failed' if not last_communication and authentication_failures
                   else communication_status),
        'last_received_at': last_communication,
        'last_transport_received_at': last_transport_received_at,
        'recent_authentication_failures': authentication_failures,
        'diagnostic_window_hours': _MONITORING_AUTH_DIAGNOSTIC_HOURS,
    }
    base['latest_cycle'] = cycle
    base['last_valid_observation_at'] = max(
        (item.get('observed_at') or item.get('scheduled_at') for item in values
         if (item.get('observed_at') or item.get('scheduled_at'))
         and item.get('status', 'value') == 'value'),
        default=None)
    base['published_factor_count'] = len(configs)
    if not expected_factor_selection:
        base.update(
            monitoring_status='raw_received_config_pending',
            monitoring_status_label='应监测因子未设置',
            reason_code='expected_factors_unconfigured',
            monitoring_reason='请先设置本站实际应监测因子；协议映射不会自动作为完整性分母')
    elif not last_communication:
        if authentication_failures:
            base.update(
                monitoring_status='attention', monitoring_status_label='认证失败',
                reason_code='authentication_failed',
                monitoring_reason=f'最近{_MONITORING_AUTH_DIAGNOSTIC_HOURS}小时已收到站点报文，但认证未通过')
        else:
            base.update(monitoring_status='awaiting_first_frame', monitoring_status_label='等待首帧', reason_code='no_authenticated_frame', monitoring_reason='身份已接入，尚未收到认证原文')
    elif not configs:
        base.update(monitoring_status='raw_received_config_pending', monitoring_status_label='监测配置待完善', reason_code='monitoring_config_pending', monitoring_reason='应监测因子已设置，但当前端点缺少可用监测配置')
    elif not values:
        data_axis = _station_monitoring_data_axis(
            configs, values, db, site_id=site_id, factor_states=factor_states)
        if data_axis['state'] == 'analysis_pending':
            base.update(monitoring_status='waiting_first_valid', monitoring_status_label='等待分析结果',
                        reason_code='analysis_pending', monitoring_reason='当前业务时点仍在分析结果窗口内')
        else:
            base.update(monitoring_status='attention', monitoring_status_label='数据需关注',
                        reason_code='missing_observation', monitoring_reason='完整结果窗口结束后仍无有效观测')
    else:
        summary = _station_monitoring_summary_projection(
            db, site_id, profile, configs, values, last_communication,
            factor_states=factor_states)
        base.update(monitoring_status=summary['status'], monitoring_status_label=summary['status_label'], reason_code=summary['reason_code'], monitoring_reason=summary['reason'])
    if (not values and expected_factor_selection and last_communication
            and cycle['status'] in {'incomplete', 'review_required'}):
        base.update(monitoring_status='attention', monitoring_status_label='数据需关注',
                    reason_code='latest_cycle_' + cycle['status'],
                    monitoring_reason='最近检测周期不完整或存在待核对结果')
    elif (not values and expected_factor_selection and last_communication
          and cycle['status'] == 'pending' and not cycle['formed_count']):
        base.update(monitoring_status='waiting_first_valid', monitoring_status_label='等待分析结果',
                    reason_code='analysis_pending', monitoring_reason='最近检测周期仍在结果窗口内')
    return base


def _station_monitoring_summary_projection(db, site_id, profile, configs=None, values=None,
                                           last_communication=None, factor_states=None):
    configs = configs if configs is not None else monitoring_factor_configurations(db, site_id)
    values = values if values is not None else monitoring_latest_values(db, site_id)
    if last_communication is None:
        row = db.execute(
            """SELECT latest_authenticated_at AS received_at
               FROM station_ingest_current_state WHERE endpoint_id=?
               ORDER BY latest_authenticated_at DESC LIMIT 1""",
            (profile['endpoint_id'],),
        ).fetchone()
        last_communication = row['received_at'] if row else None
    profile_data = dict(profile)
    communication = _monitoring_freshness(last_communication, 1800)
    if communication['state'] == 'stale':
        return {'status': 'attention', 'status_label': '数据需关注', 'reason_code': 'stale_communication', 'reason': '最后收到报文时间已超出配置周期'}
    data_axis = _station_monitoring_data_axis(
        configs, values, db, site_id=site_id, factor_states=factor_states)
    if data_axis['status'] == 'attention':
        return {'status': 'attention', 'status_label': '数据需关注',
                'reason_code': data_axis.get('reason_code'), 'reason': data_axis.get('reason')}
    if values and (not profile_data.get('expected_interval_seconds')
                   or any(not item.get('expected_interval_seconds') for item in configs)):
        return {
            'status': 'attention', 'status_label': '已有监测结果',
            'reason_code': 'result_freshness_unknown',
            'reason': '已显示最新有效结果；未配置预期周期，无法判断结果是否及时',
        }
    if data_axis['state'] in {'fresh', 'analysis_pending'}:
        return {'status': 'normal', 'status_label': '数据正常', 'reason_code': None, 'reason': '最近有效观测在配置周期内'}
    return {'status': 'attention', 'status_label': '数据需关注', 'reason_code': 'stale_observation', 'reason': '最近有效观测超出配置周期或存在缺口'}


def _station_monitoring_axis(name, state, label, *, status='info', reason=None, **extra):
    return dict({'name': name, 'state': state, 'status': status, 'status_label': label, 'reason': reason}, **extra)


def _station_monitoring_presented_value(item):
    value = dict(item)
    value['source_data_time'] = value.get('observed_at')
    return value


def _station_monitoring_factor_states(db, site_id, configs, values, *, faults=None):
    value_by_config = {
        (item.get('endpoint_id'), item.get('business_metric'), item.get('instrument_asset_code')): item
        for item in values
    }
    protocol_codes = sorted({code for item in configs for code in (item.get('protocol_codes') or [item.get('protocol_code')]) if code})
    faults_supplied = faults is not None
    faults = {} if faults is None else dict(faults)
    if db is not None and site_id is not None and protocol_codes and not faults_supplied:
        marks = ','.join('?' * len(protocol_codes))
        rows = db.execute(
            f"""SELECT issue.issue_type,issue.object_summary AS protocol_code,batch.observed_at
                FROM monitoring_quality_issues issue
                JOIN observation_batches batch ON batch.id=issue.observation_batch_id AND batch.is_current=1
                WHERE issue.business_site_id=? AND issue.status='open'
                  AND issue.object_summary IN ({marks})
                ORDER BY batch.observed_at DESC,issue.id DESC""",
            [site_id, *protocol_codes],
        ).fetchall()
        for row in rows:
            faults.setdefault(row['protocol_code'], dict(row))
    states = []
    for config in configs:
        key = (config.get('endpoint_id'), config.get('business_metric'), config.get('instrument_asset_code'))
        value = value_by_config.get(key)
        fault = max(
            (faults[code] for code in (config.get('protocol_codes') or [config.get('protocol_code')]) if code in faults),
            key=lambda item: item['observed_at'], default=None,
        )
        if value and value.get('status') == 'review_required':
            state, reason_code, reason = (
                'review_required', 'valid_value_conflict',
                '同一采集结果存在多个不同有效值，需核对')
        elif fault and (not value or fault['observed_at'] >= value.get('observed_at', '')):
            state, reason_code, reason = 'fault', fault['issue_type'], '仪器质量标记无效，故障值未发布'
        elif value:
            state, reason_code, reason = 'valid', None, None
        elif config.get('has_due_slot') or not config.get('result_delay_seconds'):
            state, reason_code, reason = 'missing', 'missing_observation', '完整结果窗口结束后仍无有效值'
        else:
            state, reason_code, reason = 'pending', 'analysis_pending', '当前业务时点仍在分析结果窗口内'
        states.append(dict(
            config, state=state, reason_code=reason_code, reason=reason,
            last_valid=value if state == 'valid' else None, latest_result=value,
            pending_until=config.get('pending_until')))
    return states


def _station_monitoring_data_axis(configs, values, db=None, *, site_id=None, factor_states=None):
    """Project observation availability without borrowing communication facts."""
    if not configs:
        return _station_monitoring_axis(
            '观测数据', 'not_configured', '监测因子未配置', status='pending',
            reason='尚未配置已批准的监测因子', reason_code='not_configured',
            last_valid_observation_at=None)
    if factor_states is None:
        factor_states = _station_monitoring_factor_states(db, site_id, configs, values)
    freshness = []
    next_expected = []
    now = datetime.now(timezone.utc).replace(microsecond=0)
    for state in factor_states:
        config = state
        value = state.get('last_valid')
        if value:
            scheduled_at = value.get('scheduled_at') or value.get('observed_at')
            next_at = (monitoring_next_business_slot(db, config, scheduled_at) if db else
                       (datetime.fromisoformat(scheduled_at) + timedelta(
                            seconds=int(config.get('expected_interval_seconds') or 0))).isoformat())
            next_expected.append(next_at)
            deadline = datetime.fromisoformat(monitoring_business_slot_deadline(config, next_at)) if next_at else None
            freshness.append({'state': 'fresh' if deadline and now <= deadline else 'stale', 'reason_code': None})
        else:
            next_at = config.get('next_expected_at')
            if next_at:
                next_expected.append(next_at)
            freshness.append({'state': state['state'], 'reason_code': state['reason_code']})
    last_valid = max(
        (item.get('observed_at') or item.get('scheduled_at') for item in values
         if item.get('status', 'value') == 'value'
         and (item.get('observed_at') or item.get('scheduled_at'))),
        default=None,
    )
    next_expected_at = min((item for item in next_expected if item), default=None)
    if any(item['state'] == 'review_required' for item in factor_states):
        return _station_monitoring_axis(
            '观测数据', 'review_required', '结果待核对', status='attention',
            reason='同一采集结果存在多个不同有效值，无法自动选择',
            last_valid_observation_at=last_valid, next_expected_at=next_expected_at,
            reason_code='valid_value_conflict')
    if last_valid and any(not item.get('expected_interval_seconds') for item in configs):
        return _station_monitoring_axis(
            '观测数据', 'result_available', '已有监测结果', status='info',
            reason='已显示最新有效结果；未配置预期周期，无法判断结果是否及时',
            reason_code='result_freshness_unknown',
            last_valid_observation_at=last_valid, next_expected_at=next_expected_at)
    if not last_valid:
        if any(item['state'] == 'fault' for item in factor_states):
            return _station_monitoring_axis(
                '观测数据', 'quality_fault', '存在故障值', status='attention',
                reason='仪器质量标记无效，故障值未发布', last_valid_observation_at=None,
                next_expected_at=next_expected_at, reason_code='quality_fault')
        if not any(item['state'] == 'missing' for item in factor_states):
            return _station_monitoring_axis(
                '观测数据', 'analysis_pending', '等待分析结果', status='pending',
                reason='当前业务时点仍在分析结果窗口内', last_valid_observation_at=None,
                next_expected_at=next_expected_at, reason_code='analysis_pending')
        return _station_monitoring_axis(
            '观测数据', 'no_valid_observation', '暂无有效观测', status='attention',
            reason='完整结果窗口结束后仍无有效观测', last_valid_observation_at=None,
            next_expected_at=next_expected_at, reason_code='missing_observation')
    if any(item['state'] == 'fault' for item in factor_states):
        return _station_monitoring_axis(
            '观测数据', 'quality_fault', '存在故障值', status='attention',
            reason='当前配置因子存在未发布的仪器故障值', last_valid_observation_at=last_valid,
            next_expected_at=next_expected_at, reason_code='quality_fault')
    if any(item['state'] == 'missing' for item in factor_states):
        return _station_monitoring_axis(
            '观测数据', 'no_valid_observation', '有效观测不完整', status='attention',
            reason='完整结果窗口结束后仍有配置因子缺测', last_valid_observation_at=last_valid,
            next_expected_at=next_expected_at, reason_code='missing_observation')
    if any(item['state'] == 'pending' for item in factor_states):
        return _station_monitoring_axis(
            '观测数据', 'analysis_pending', '部分因子分析中', status='pending',
            reason='当前业务时点仍有配置因子处于分析结果窗口内', last_valid_observation_at=last_valid,
            next_expected_at=next_expected_at, reason_code='analysis_pending')
    if all(item['state'] == 'fresh' for item in freshness):
        return _station_monitoring_axis(
            '观测数据', 'fresh', '在配置周期内', status='normal',
            reason='最近正式业务观测仍在应到周期内', last_valid_observation_at=last_valid,
            next_expected_at=next_expected_at, reason_code=None)
    return _station_monitoring_axis(
        '观测数据', 'stale', '超出配置周期', status='attention',
        reason='最近正式业务观测已超过下一应到时点', last_valid_observation_at=last_valid,
        next_expected_at=next_expected_at, reason_code='stale_observation')


_STATION_MONITORING_FACTOR_LABELS = {
    'water_temp': '水温', 'ph': 'pH', 'dissolved_oxygen': '溶解氧',
    'conductivity': '电导率', 'turbidity': '浊度', 'codmn': '高锰酸盐指数',
    'ammonia': '氨氮', 'total_phosphorus': '总磷', 'total_nitrogen': '总氮',
    'chlorophyll': '叶绿素a', 'algae_density': '藻密度',
    'total_organic_carbon': '总有机碳', 'total_tin': '总锡', 'total_lead': '总铅',
    'total_copper': '总铜', 'total_zinc': '总锌',
}

_STATION_EXPECTED_FACTOR_METRICS = (
    'dissolved_oxygen', 'ph', 'turbidity', 'water_temp', 'conductivity',
    'codmn', 'total_nitrogen', 'total_phosphorus', 'ammonia',
)

_STATION_EXPECTED_HJ212_STANDARD_MAPPINGS = {
    'dissolved_oxygen': 'HJ212:w01009',
    'ph': 'HJ212:w01001',
    'turbidity': 'HJ212:w01003',
    'water_temp': 'HJ212:w01010',
    'conductivity': 'HJ212:w01014',
    'codmn': 'HJ212:w01019-Rtd',
    'total_nitrogen': 'HJ212:w21001',
    'total_phosphorus': 'HJ212:w21011',
    'ammonia': 'HJ212:w21003',
}

_STATION_EXPECTED_AUTO_PROFILE_INTERVAL_SECONDS = 14400
_STATION_EXPECTED_AUTO_PROFILE_TOLERANCE_SECONDS = 600
_STATION_EXPECTED_AUTO_PROFILE_RESULT_DELAY_SECONDS = 7200

def _station_monitoring_overview(db, site_id, *, include_diagnostics=True):
    projection = _station_monitoring_projection(db, site_id, include_latest_complete=True)
    if projection is None:
        return None
    profile, error = _monitoring_site_context(db, site_id, allow_public_read=True)
    if error:
        profile = None
    configs = [item for item in monitoring_factor_configurations(db, site_id) if item.get('endpoint_id') == profile['endpoint_id']] if profile else []
    values = [item for item in monitoring_latest_values(db, site_id) if item.get('endpoint_id') == profile['endpoint_id']] if profile else []
    factor_states = _station_monitoring_factor_states(db, site_id, configs, values)
    # Current collection values remain separate from formal-cycle completeness.
    # Missing factors stay missing instead of being filled from an older collection.
    latest = values
    latest = [dict(
        _station_monitoring_presented_value(item),
        factor_name_cn=_STATION_MONITORING_FACTOR_LABELS.get(
            item.get('business_metric'), f'监测因子{index + 1}'),
    ) for index, item in enumerate(latest)]
    status = projection['monitoring_status']
    communication_contract = (projection.get('communication') or {}).get('status')
    communication_state = {'normal': 'fresh', 'stale': 'stale',
                           'awaiting': 'awaiting', 'not_connected': 'not_connected'}.get(
                               communication_contract, 'unknown')
    communication_label = ('未接入' if not projection['last_communication_at'] and status == 'not_connected'
        else '等待首帧' if not projection['last_communication_at']
        else '数据已接收' if communication_state == 'fresh'
        else '最近未收到报文' if communication_state == 'stale'
        else '接收状态待确认')
    axes = None
    if include_diagnostics:
        data_axis = _station_monitoring_data_axis(
            configs, values, db, site_id=site_id, factor_states=factor_states)
        axes = {
            'communication': _station_monitoring_axis('数据接收', communication_state, communication_label,
                status='normal' if communication_state == 'fresh' else ('attention' if communication_state == 'stale' else 'pending'),
                reason='最后收到报文时间已超出配置周期' if communication_state == 'stale' else ('数据接收周期尚未配置' if projection['last_communication_at'] and communication_state == 'unknown' else None),
                last_received_at=projection['last_communication_at']),
            'data': data_axis,
        }
    cycle = dict(projection.get('latest_cycle') or {})
    cycle['factors'] = [dict(
        item,
        factor_name_cn=_STATION_MONITORING_FACTOR_LABELS.get(
            item.get('business_metric'), f'监测因子{index + 1}'),
    ) for index, item in enumerate(cycle.get('factors') or [])]
    factors = [{
        'business_metric': item.get('business_metric'),
        'factor_name_cn': _STATION_MONITORING_FACTOR_LABELS.get(
            item.get('business_metric'), f'监测因子{index + 1}'),
        'standard_unit': item.get('standard_unit') or '',
        'state': item.get('state'), 'reason_code': item.get('reason_code'),
        'reason': item.get('reason'), 'pending_until': item.get('pending_until'),
    } for index, item in enumerate(factor_states)]
    has_trend_facts = bool(factors)
    monitoring = {'latest_cycle': cycle, 'latest_values': latest,
                  'communication': projection.get('communication'), 'factors': factors,
                  'capabilities': {'latest': bool(latest), 'trend': has_trend_facts, 'factors': bool(factors)}}
    if axes is not None:
        monitoring['axes'] = axes
    projection['can_calibrate'] = _site_can_calibrate(db, site_id)
    projection['type_cn'] = _STATION_MONITORING_SITE_TYPE_LABELS.get(projection.get('type'), '其他站点')
    projection['service_policy'] = _station_service_policy_payload(db, site_id)
    result = {'site': projection, 'monitoring': monitoring, 'factors': factors,
            'capabilities': monitoring['capabilities'],
            'section_status': {'latest_values': 'ready' if latest else 'not_configured',
                               'trend': 'ready' if has_trend_facts else 'not_configured',
                               'factors': 'ready' if factors else 'not_configured'},
            'updated_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
    if axes is not None:
        result['axes'] = axes
    return result


def _station_monitoring_latest_values_and_cycles_for_list(db, configurations_by_site, *, at_time):
    return monitoring_latest_values_and_cycles_batch(
        db, configurations_by_site, at_time=at_time)


def _station_monitoring_latest_authenticated_receipts(db, endpoint_ids):
    """Seek the newest authenticated receipt for each endpoint without scanning its history."""
    endpoint_ids = list(endpoint_ids)
    if not endpoint_ids:
        return {}
    marks = ','.join('?' * len(endpoint_ids))
    rows = db.execute(f"""SELECT endpoint_id,latest_authenticated_at AS received_at
        FROM station_ingest_current_state
        WHERE endpoint_id IN ({marks}) AND latest_authenticated_at IS NOT NULL""",
        endpoint_ids).fetchall()
    return {
        row['endpoint_id']: row for row in rows
        if row['received_at'] is not None
    }


def _station_monitoring_batch_projections(db, site_rows):
    """Fetch monitoring facts in bounded sets, then assemble per-site projections."""
    if not site_rows:
        return []
    now = datetime.now(timezone.utc).isoformat(timespec='microseconds')
    site_ids = [row['id'] for row in site_rows]
    marks = ','.join('?' * len(site_ids))
    endpoints = db.execute(f"SELECT * FROM trusted_endpoints WHERE enabled=1 AND endpoint_state='bound' AND business_site_id IS NOT NULL AND business_site_id IN ({marks})", site_ids).fetchall()
    endpoint_by_id = {row['id']: row for row in endpoints}
    endpoint_ids = [row['id'] for row in endpoints]
    epmarks = ','.join('?' * len(endpoint_ids)) or 'NULL'
    raw_by_ep = _station_monitoring_latest_authenticated_receipts(db, endpoint_ids)
    profiles = db.execute(f"""SELECT profile.* FROM monitoring_endpoint_profiles profile
        JOIN trusted_endpoints endpoint ON endpoint.id=profile.endpoint_id
          AND endpoint.enabled=1 AND endpoint.endpoint_state='bound' AND endpoint.business_site_id=profile.business_site_id
        WHERE profile.enabled=1 AND profile.effective_from<=? AND (profile.effective_to IS NULL OR profile.effective_to>?)
          AND profile.business_site_id IN ({marks}) AND profile.endpoint_id IN ({epmarks}) ORDER BY profile.effective_from DESC""", [now, now, *site_ids, *endpoint_ids]).fetchall()
    profile_by_site = {}
    for row in profiles:
        profile = dict(row)
        endpoint = endpoint_by_id.get(row['endpoint_id'])
        profile['endpoint_station_code'] = endpoint['station_code'] if endpoint else None
        profile_by_site.setdefault(row['business_site_id'], profile)
    endpoint_by_site = {}
    for row in endpoints:
        endpoint_by_site.setdefault(row['business_site_id'], {
            'endpoint_id': row['id'], 'endpoint_station_code': row['station_code']})
    station_codes = [row['station_code'] for row in endpoints if row['station_code']]
    if station_codes:
        code_marks = ','.join('?' * len(station_codes))
        auth_rows = db.execute(f"""SELECT station_code,COUNT(*) AS failure_count,
                MAX(received_at) AS last_transport_received_at
            FROM station_ingest_recent_receipts
            WHERE station_code IN ({code_marks}) AND authentication_status='credential_failed'
              AND received_at>=? GROUP BY station_code""",
            [*station_codes, _monitoring_auth_diagnostic_cutoff()]).fetchall()
    else:
        auth_rows = []
    auth_by_station_code = {row['station_code']: row for row in auth_rows}
    responsibility_rows = db.execute(f"""SELECT DISTINCT us.site_id,u.id,u.real_name,u.phone
        FROM user_sites us JOIN users u ON u.id=us.user_id
        LEFT JOIN user_roles ur ON ur.user_id=u.id
        WHERE us.site_id IN ({marks}) AND u.status='active'
          AND (u.role='operator' OR ur.role='operator')
        ORDER BY us.site_id,u.real_name,u.id""", site_ids).fetchall()
    responsibility_by_site = {
        site_id: {'manager': '未分配', 'phone': '', 'responsible_people': []}
        for site_id in site_ids
    }
    for row in responsibility_rows:
        responsibility_by_site[row['site_id']]['responsible_people'].append({
            'id': row['id'], 'real_name': row['real_name'], 'phone': row['phone'],
        })
    for responsibility in responsibility_by_site.values():
        people = responsibility['responsible_people']
        responsibility['manager'] = ('、'.join(
            str(person['real_name'] or '').strip() for person in people
            if str(person['real_name'] or '').strip()) or '未分配')
        responsibility['phone'] = '、'.join(
            str(person['phone'] or '').strip() for person in people
            if str(person['phone'] or '').strip())
    expected_selections = monitoring_expected_factor_selections_batch(
        db, site_ids, at_time=now)
    all_configs = monitoring_factor_configurations_batch(
        db, site_ids, at_time=now, expected_selections=expected_selections)
    configs_by_site = {}
    for site_id in site_ids:
        profile = profile_by_site.get(site_id) or endpoint_by_site.get(site_id)
        endpoint_id = profile['endpoint_id'] if profile else None
        configs_by_site[site_id] = [
            item for item in all_configs.get(site_id, [])
            if item.get('endpoint_id') == endpoint_id
        ]
    values_by_site, cycles_by_site = _station_monitoring_latest_values_and_cycles_for_list(
        db, configs_by_site, at_time=now)
    issue_rows = db.execute(f"""SELECT issue.business_site_id,issue.issue_type,
               issue.object_summary AS protocol_code,batch.observed_at
        FROM monitoring_quality_issues issue
        JOIN observation_batches batch ON batch.id=issue.observation_batch_id AND batch.is_current=1
        WHERE issue.business_site_id IN ({marks}) AND issue.status='open'
        ORDER BY issue.business_site_id,batch.observed_at DESC,issue.id DESC""", site_ids).fetchall()
    faults_by_site = {}
    for row in issue_rows:
        faults_by_site.setdefault(row['business_site_id'], {}).setdefault(
            row['protocol_code'], dict(row))
    result = []
    for site in site_rows:
        sid = site['id']; profile = profile_by_site.get(sid) or endpoint_by_site.get(sid)
        raw = raw_by_ep.get(profile['endpoint_id']) if profile else None
        configs = configs_by_site.get(sid, [])
        values = values_by_site.get(sid, [])
        station_code = profile.get('endpoint_station_code') if profile else None
        factor_states = _station_monitoring_factor_states(
            db, sid, configs, values, faults=faults_by_site.get(sid, {}))
        result.append(_station_monitoring_projection(
            db, sid, site=site, profile=profile, raw=raw, configs=configs, values=values,
            responsibility=responsibility_by_site[sid], cycle=cycles_by_site.get(sid),
            factor_states=factor_states, auth_diagnostic=auth_by_station_code.get(station_code),
            expected_factor_selection=expected_selections.get(sid)))
    return result


_STATION_MONITORING_LIST_CACHE_SECONDS = 10
_STATION_MONITORING_LIST_REFRESH_SECONDS = 8
_STATION_MONITORING_LIST_STALE_SECONDS = 15
_station_monitoring_batch_cache = {}
_station_monitoring_batch_cache_lock = threading.Lock()
_station_monitoring_batch_inflight = {}
_station_monitoring_list_cache = {}
_station_monitoring_list_cache_lock = threading.Lock()
_station_monitoring_list_inflight = {}
_station_monitoring_overview_projection_cache = {}
_station_monitoring_overview_projection_cache_lock = threading.Lock()
_station_monitoring_overview_projection_inflight = {}
_station_monitoring_overview_cache = {}
_station_monitoring_overview_cache_lock = threading.Lock()
_station_monitoring_overview_inflight = {}
_station_monitoring_cache_generation = 0


def _invalidate_station_monitoring_projection_caches():
    global _station_monitoring_cache_generation
    with _station_monitoring_list_cache_lock:
        _station_monitoring_cache_generation += 1
        _station_monitoring_list_cache.clear()
    with _station_monitoring_batch_cache_lock:
        _station_monitoring_batch_cache.clear()
    with _station_monitoring_overview_cache_lock:
        _station_monitoring_overview_cache.clear()
    with _station_monitoring_overview_projection_cache_lock:
        _station_monitoring_overview_projection_cache.clear()


def _station_expected_factor_can_edit(db, site_id, user):
    if not user or not user.get('id'):
        return False
    current = db.execute(
        'SELECT id,role,status FROM users WHERE id=?', (user['id'],)).fetchone()
    if not current or current['status'] != 'active':
        return False
    roles = set(_roles_for_user(db, current['id'], current['role']))
    if 'admin' in roles:
        return True
    return ('operator' in roles and db.execute(
        'SELECT 1 FROM user_sites WHERE user_id=? AND site_id=?',
        (current['id'], site_id),
    ).fetchone() is not None)


def _station_expected_factor_mapping_metrics(db, site_id, at_time):
    rows = db.execute("""SELECT DISTINCT
               COALESCE(mapping.business_metric,definition.business_metric) AS business_metric
        FROM monitoring_endpoint_profiles profile
        JOIN monitoring_factor_mappings mapping ON mapping.endpoint_id=profile.endpoint_id
        JOIN monitoring_factor_definitions definition ON definition.protocol_code=mapping.protocol_code
        WHERE profile.business_site_id=? AND profile.enabled=1
          AND profile.effective_from<=? AND (profile.effective_to IS NULL OR profile.effective_to>?)
          AND mapping.enabled=1 AND mapping.effective_from<=?
          AND (mapping.effective_to IS NULL OR mapping.effective_to>?)
          AND definition.is_published=1""",
        (site_id, at_time, at_time, at_time, at_time),
    ).fetchall()
    return {row['business_metric'] for row in rows}


def _station_hj212_endpoint_evidence_ids(db, endpoint_id, *, limit=200):
    return tuple(int(row['raw_id']) for row in db.execute("""SELECT raw_id FROM (
            SELECT raw.id AS raw_id
            FROM ingest_raw_frames raw
                 INDEXED BY idx_ingest_raw_authenticated_endpoint_received
            JOIN ingest_frame_protocols protocol ON protocol.raw_frame_id=raw.id
            WHERE raw.endpoint_id=? AND raw.authentication_status='authenticated'
              AND protocol.protocol_family='hj212'
            UNION
            SELECT evidence.source_raw_frame_id AS raw_id
            FROM monitoring_archive_evidence_refs evidence
            WHERE evidence.endpoint_id=? AND evidence.authentication_status='authenticated'
              AND evidence.protocol_family='hj212'
        ) ORDER BY raw_id DESC LIMIT ?""", (endpoint_id, endpoint_id, limit)).fetchall())


def _station_hj212_endpoint_evidence(db, endpoint_id, *, limit=200, raw_ids=None):
    raw_ids = (_station_hj212_endpoint_evidence_ids(db, endpoint_id, limit=limit)
               if raw_ids is None else tuple(raw_ids))
    factor_codes = set()
    has_four_hour_boundary_formal = False
    for raw_id in raw_ids:
        try:
            frame = parse_hj212_frame(read_raw_evidence(db, raw_id))
        except RetentionError as exc:
            raise RetentionError("HJ212 historical evidence is unavailable; retry after archive recovery") from exc
        except HJ212FrameError:
            continue
        if frame.command != '2061':
            continue
        formal_factors = [
            factor for factor in frame.factors
            if factor.raw_value is not None and monitoring_is_formal_hj212_source(
                factor.protocol_code, frame.command, factor.value_kind)
        ]
        factor_codes.update(factor.protocol_code for factor in formal_factors)
        if frame.data_time is not None \
                and frame.data_time.minute == 0 and frame.data_time.second == 0 \
                and frame.data_time.hour % 4 == 0 \
                and formal_factors:
            has_four_hour_boundary_formal = True
    return {
        'has_hj212': bool(raw_ids),
        'factor_codes': factor_codes,
        'has_four_hour_boundary_formal': has_four_hour_boundary_formal,
        'raw_ids': raw_ids,
    }


def _station_expected_bound_endpoint_ids(db, site_id):
    return tuple(int(row['id']) for row in db.execute("""SELECT id FROM trusted_endpoints
        WHERE business_site_id=? AND enabled=1 AND endpoint_state='bound'
        ORDER BY id""", (site_id,)).fetchall())


def _prepare_station_expected_hj212_evidence(db, site_id):
    endpoint_ids = _station_expected_bound_endpoint_ids(db, site_id)
    return {
        'endpoint_ids': endpoint_ids,
        'by_endpoint': {
            endpoint_id: _station_hj212_endpoint_evidence(db, endpoint_id)
            for endpoint_id in endpoint_ids
        },
    }


def _ensure_station_expected_hj212_profiles(
        db, site_id, selected_metrics, effective_from, *, evidence_by_endpoint=None):
    """Create a profile only from a bound endpoint plus retained formal HJ212 evidence."""
    endpoints = db.execute("""SELECT id FROM trusted_endpoints
        WHERE business_site_id=? AND enabled=1 AND endpoint_state='bound'
        ORDER BY id""", (site_id,)).fetchall()
    for endpoint in endpoints:
        endpoint_id = int(endpoint['id'])
        active_profiles = db.execute("""SELECT * FROM monitoring_endpoint_profiles
            WHERE endpoint_id=? AND enabled=1 AND effective_from<=?
              AND (effective_to IS NULL OR effective_to>?)
            ORDER BY effective_from DESC,id DESC""",
            (endpoint_id, effective_from, effective_from),
        ).fetchall()
        if len(active_profiles) > 1:
            return (
                'EXPECTED_FACTORS_CONFIGURATION_CONFLICT',
                '同一端点存在重叠监测 profile，不能自动补齐配置',
            )
        if active_profiles:
            if int(active_profiles[0]['business_site_id']) != int(site_id):
                return (
                    'EXPECTED_FACTORS_CONFIGURATION_CONFLICT',
                    '端点 profile 与当前站点归属冲突，不能静默改绑',
                )
            continue
        evidence = (evidence_by_endpoint or {}).get(endpoint_id)
        if evidence is None:
            evidence = _station_hj212_endpoint_evidence(db, endpoint_id)
        if not evidence['has_hj212']:
            continue
        definitions = db.execute("""SELECT DISTINCT business_metric
            FROM monitoring_factor_definitions
            WHERE protocol_code IN ({}) AND is_published=1
              AND business_metric IS NOT NULL""".format(
                  ','.join('?' * len(evidence['factor_codes']))
              ), tuple(sorted(evidence['factor_codes']))).fetchall() if evidence['factor_codes'] else []
        evidenced_metrics = {row['business_metric'] for row in definitions}
        if not evidenced_metrics.intersection(selected_metrics):
            continue
        if not evidence['has_four_hour_boundary_formal']:
            return (
                'EXPECTED_FACTORS_CONFIGURATION_EVIDENCE_REQUIRED',
                '端点已有认证 HJ212 原文，但缺少合法四小时时点的2061正式值依据，未自动猜测周期',
            )
        future_profiles = db.execute("""SELECT 1 FROM monitoring_endpoint_profiles
            WHERE endpoint_id=? AND enabled=1 AND effective_from>? LIMIT 1""",
            (endpoint_id, effective_from),
        ).fetchone()
        if future_profiles:
            return (
                'EXPECTED_FACTORS_CONFIGURATION_CONFLICT',
                '端点已有未来生效的监测 profile，不能自动建立重叠配置',
            )
        schedules = db.execute("""SELECT * FROM monitoring_business_schedules
            WHERE endpoint_id=? AND protocol_code IS NULL AND enabled=1
              AND effective_from<=? AND (effective_to IS NULL OR effective_to>?)
            ORDER BY effective_from DESC,id DESC""",
            (endpoint_id, effective_from, effective_from),
        ).fetchall()
        if len(schedules) > 1:
            return (
                'EXPECTED_FACTORS_CONFIGURATION_CONFLICT',
                '端点存在重叠业务时槽，不能自动补齐配置',
            )
        if schedules:
            schedule = schedules[0]
            if (schedule['timezone'] != 'Asia/Shanghai'
                    or int(schedule['interval_seconds']) != _STATION_EXPECTED_AUTO_PROFILE_INTERVAL_SECONDS
                    or schedule['anchor_local_time'] != '00:00:00'
                    or int(schedule['tolerance_seconds']) != _STATION_EXPECTED_AUTO_PROFILE_TOLERANCE_SECONDS
                    or int(schedule['result_delay_seconds']) != _STATION_EXPECTED_AUTO_PROFILE_RESULT_DELAY_SECONDS):
                return (
                    'EXPECTED_FACTORS_CONFIGURATION_CONFLICT',
                    '端点现有业务时槽与已批准四小时口径冲突，不能静默改写',
                )
        elif db.execute("""SELECT 1 FROM monitoring_business_schedules
                WHERE endpoint_id=? AND protocol_code IS NULL AND enabled=1
                  AND effective_from>? LIMIT 1""", (endpoint_id, effective_from)).fetchone():
            return (
                'EXPECTED_FACTORS_CONFIGURATION_CONFLICT',
                '端点已有未来生效的业务时槽，不能自动建立重叠配置',
            )
        db.execute("""INSERT INTO monitoring_endpoint_profiles(
                endpoint_id,business_site_id,timezone,enabled,expected_granularity,
                expected_interval_seconds,effective_from,effective_to)
            VALUES (?,?,'Asia/Shanghai',1,'periodic',?,?,NULL)""", (
                endpoint_id, site_id,
                _STATION_EXPECTED_AUTO_PROFILE_INTERVAL_SECONDS, effective_from,
            ))
        if not schedules:
            db.execute("""INSERT INTO monitoring_business_schedules(
                    endpoint_id,protocol_code,timezone,interval_seconds,anchor_local_time,
                    tolerance_seconds,result_delay_seconds,effective_from,effective_to,enabled)
                VALUES (?,NULL,'Asia/Shanghai',?,'00:00:00',?,?,?,NULL,1)""", (
                    endpoint_id, _STATION_EXPECTED_AUTO_PROFILE_INTERVAL_SECONDS,
                    _STATION_EXPECTED_AUTO_PROFILE_TOLERANCE_SECONDS,
                    _STATION_EXPECTED_AUTO_PROFILE_RESULT_DELAY_SECONDS, effective_from,
                ))
    return None


def _ensure_station_expected_hj212_standard_mappings(
        db, site_id, selected_metrics, effective_from, *, evidence_by_endpoint=None):
    """Add canonical HJ212 mappings and only endpoint-evidenced approved aliases."""
    requested = [metric for metric in _STATION_EXPECTED_HJ212_STANDARD_MAPPINGS
                 if metric in selected_metrics]
    if not requested:
        return None
    profiles = db.execute("""SELECT profile.endpoint_id,profile.instrument_asset_code,
                                      profile.expected_interval_seconds
        FROM monitoring_endpoint_profiles profile
        JOIN trusted_endpoints endpoint ON endpoint.id=profile.endpoint_id
        WHERE profile.business_site_id=? AND profile.enabled=1
          AND profile.effective_from<=?
          AND (profile.effective_to IS NULL OR profile.effective_to>?)
          AND endpoint.business_site_id=? AND endpoint.enabled=1
          AND endpoint.endpoint_state='bound'
          AND (EXISTS (
                  SELECT 1 FROM monitoring_factor_mappings evidence
                  WHERE evidence.endpoint_id=profile.endpoint_id
                    AND evidence.protocol_code LIKE 'HJ212:%' AND evidence.enabled=1
                    AND evidence.effective_from<=?
                    AND (evidence.effective_to IS NULL OR evidence.effective_to>?)
               ) OR EXISTS (
                  SELECT 1 FROM ingest_raw_frames raw
                  INDEXED BY idx_ingest_raw_authenticated_endpoint_received
                  JOIN ingest_frame_protocols protocol ON protocol.raw_frame_id=raw.id
                  WHERE raw.endpoint_id=profile.endpoint_id
                    AND raw.authentication_status='authenticated'
                    AND protocol.protocol_family='hj212'
               ) OR EXISTS (
                  SELECT 1 FROM monitoring_archive_evidence_refs evidence
                  WHERE evidence.endpoint_id=profile.endpoint_id
                    AND evidence.authentication_status='authenticated'
                    AND evidence.protocol_family='hj212'
               ))
        ORDER BY profile.endpoint_id,profile.effective_from DESC""", (
            site_id, effective_from, effective_from, site_id,
            effective_from, effective_from,
        )).fetchall()
    by_endpoint = {}
    for row in profiles:
        endpoint_id = int(row['endpoint_id'])
        if endpoint_id in by_endpoint:
            return '同一 HJ212 端点存在重叠 profile，不能自动补充因子映射'
        by_endpoint[endpoint_id] = row
    if not by_endpoint:
        return None
    for endpoint_id, profile in by_endpoint.items():
        evidence = (evidence_by_endpoint or {}).get(endpoint_id)
        if evidence is None:
            evidence = _station_hj212_endpoint_evidence(db, endpoint_id)
        observed_codes = evidence['factor_codes']
        missing_mappings = []
        for metric in requested:
            canonical_code = _STATION_EXPECTED_HJ212_STANDARD_MAPPINGS[metric]
            candidate_codes = [canonical_code]
            if observed_codes:
                marks = ','.join('?' * len(observed_codes))
                alias_rows = db.execute(f"""SELECT protocol_code
                    FROM monitoring_factor_definitions
                    WHERE protocol_code IN ({marks}) AND business_metric=? AND is_published=1
                    ORDER BY protocol_code""", (*sorted(observed_codes), metric)).fetchall()
                candidate_codes.extend(
                    row['protocol_code'] for row in alias_rows
                    if row['protocol_code'] != canonical_code)
            for protocol_code in candidate_codes:
                definition = db.execute(
                    'SELECT business_metric,is_published FROM monitoring_factor_definitions '
                    'WHERE protocol_code=?', (protocol_code,),
                ).fetchone()
                if not definition or definition['business_metric'] != metric \
                        or not definition['is_published']:
                    return f'{protocol_code} 标准定义不可用，不能自动补充映射'
                current = db.execute("""SELECT mapping.business_metric,
                           definition.business_metric AS definition_metric
                    FROM monitoring_factor_mappings mapping
                    JOIN monitoring_factor_definitions definition
                      ON definition.protocol_code=mapping.protocol_code
                    WHERE mapping.endpoint_id=? AND mapping.protocol_code=? AND mapping.enabled=1
                      AND mapping.effective_from<=?
                      AND (mapping.effective_to IS NULL OR mapping.effective_to>?)""",
                    (endpoint_id, protocol_code, effective_from, effective_from),
                ).fetchall()
                if len(current) > 1:
                    return f'{protocol_code} 存在重叠映射，不能自动补充配置'
                if current:
                    resolved_metric = current[0]['business_metric'] or current[0]['definition_metric']
                    if resolved_metric != metric:
                        return f'{protocol_code} 已映射到其他业务因子，不能静默改写'
                    continue
                missing_mappings.append((protocol_code, metric))
        if not missing_mappings:
            continue
        timing_rows = db.execute("""SELECT DISTINCT
                   expected_interval_seconds,tolerance_seconds
            FROM monitoring_factor_mappings
            WHERE endpoint_id=? AND protocol_code LIKE 'HJ212:%' AND enabled=1
              AND effective_from<=? AND (effective_to IS NULL OR effective_to>?)""",
            (endpoint_id, effective_from, effective_from),
        ).fetchall()
        timing_pairs = {
            (row['expected_interval_seconds'], row['tolerance_seconds'])
            for row in timing_rows
        }
        if len(timing_pairs) > 1:
            return '现有 HJ212 因子的周期或容差不一致，不能猜测新增因子配置'
        mapping_interval, mapping_tolerance = (
            next(iter(timing_pairs)) if timing_pairs else (None, None))
        schedules = db.execute("""SELECT * FROM monitoring_business_schedules
            WHERE endpoint_id=? AND protocol_code IS NULL AND enabled=1
              AND effective_from<=? AND (effective_to IS NULL OR effective_to>?)
            ORDER BY effective_from DESC,id DESC""",
            (endpoint_id, effective_from, effective_from),
        ).fetchall()
        if len(schedules) > 1:
            return '现有 HJ212 端点存在重叠业务时槽，不能猜测新增因子配置'
        schedule = schedules[0] if schedules else None
        if mapping_interval is None and schedule is not None:
            mapping_interval = schedule['interval_seconds']
        if mapping_tolerance is None and schedule is not None:
            mapping_tolerance = schedule['tolerance_seconds']
        if profile['expected_interval_seconds'] is not None \
                and mapping_interval is not None \
                and profile['expected_interval_seconds'] != mapping_interval:
            return 'HJ212 profile 与现有因子周期不一致，不能猜测新增因子配置'
        expected_interval = (profile['expected_interval_seconds']
                             if profile['expected_interval_seconds'] is not None
                             else mapping_interval)
        if expected_interval is None or mapping_tolerance is None:
            return 'HJ212 端点缺少周期或容差依据，不能自动补充因子映射'
        for protocol_code, metric in missing_mappings:
            db.execute("""INSERT INTO monitoring_factor_mappings(
                    endpoint_id,protocol_code,business_metric,instrument_asset_code,
                    expected_interval_seconds,tolerance_seconds,effective_from,effective_to,enabled)
                VALUES (?,?,?,?,?,?,?,NULL,1)""", (
                    endpoint_id, protocol_code, metric, profile['instrument_asset_code'],
                    expected_interval, mapping_tolerance, effective_from,
                ))
    return None


def _station_expected_factor_payload(db, site_id, user, *, config=None, replayed=False):
    now = datetime.now(timezone.utc).isoformat(timespec='microseconds')
    if config is None:
        selection = monitoring_expected_factor_selections_batch(
            db, [site_id], at_time=now).get(site_id)
        selected = [item['business_metric'] for item in (selection or {}).get('items', [])]
        config_id = selection.get('config_id') if selection else None
        version = selection.get('version') if selection else 0
        effective_from = selection.get('effective_from') if selection else None
    else:
        config = dict(config)
        selected = [row['business_metric'] for row in db.execute(
            'SELECT business_metric FROM station_expected_factors '
            'WHERE config_id=? ORDER BY display_order,business_metric',
            (config['id'],),
        ).fetchall()]
        config_id = config['id']
        version = config['version']
        effective_from = config['effective_from']
    selected_set = set(selected)
    mapped_metrics = _station_expected_factor_mapping_metrics(db, site_id, now)
    candidates = [{
        'business_metric': metric,
        'label': _STATION_MONITORING_FACTOR_LABELS[metric],
        'selected': metric in selected_set,
        'mapping_available': metric in mapped_metrics,
    } for metric in _STATION_EXPECTED_FACTOR_METRICS]
    return {
        'site_id': site_id,
        'configured': bool(config_id),
        'config_id': config_id,
        'version': version,
        'effective_from': effective_from,
        'selected_metrics': selected,
        'candidates': candidates,
        'can_edit': _station_expected_factor_can_edit(db, site_id, user),
        'replayed': bool(replayed),
    }


def _station_monitoring_list_version(db):
    """Cheap projection version; raw communication freshness remains TTL bounded."""
    return (
        db.execute("""SELECT COALESCE(MAX(id),0) FROM observation_batches
                      WHERE is_current=1 AND projection_state='completed'""").fetchone()[0],
        db.execute("""SELECT COALESCE(MAX(id),0) FROM monitoring_business_observations
                      WHERE is_current=1 AND slot_state IN ('selected','conflict')""").fetchone()[0],
        db.execute('SELECT COALESCE(MAX(id),0) FROM monitoring_factor_mappings').fetchone()[0],
        db.execute('SELECT COALESCE(MAX(id),0) FROM monitoring_endpoint_profiles').fetchone()[0],
        db.execute('SELECT COALESCE(MAX(id),0) FROM monitoring_business_schedules').fetchone()[0],
        db.execute('SELECT COALESCE(MAX(id),0) FROM station_expected_factor_configs').fetchone()[0],
        tuple(db.execute('SELECT COUNT(*),COALESCE(MAX(id),0) FROM sites').fetchone()),
        tuple(db.execute('SELECT COUNT(*),COALESCE(MAX(rowid),0) FROM user_sites').fetchone()),
        tuple(db.execute("""SELECT COUNT(*),COALESCE(MAX(id),0)
                            FROM monitoring_quality_issues WHERE status='open'""").fetchone()),
    )


def _station_monitoring_batch_projections_cached(db, site_rows, projection_version):
    site_rows = list(site_rows)
    cache_key = (os.path.abspath(_database_path()), _station_monitoring_cache_generation,
                 projection_version,
                 tuple(tuple(row) for row in site_rows))
    while True:
        owner = False
        with _station_monitoring_batch_cache_lock:
            cached = _station_monitoring_batch_cache.get(cache_key)
            if cached and cached['expires_at'] > time.monotonic():
                return [dict(item) for item in cached['items']]
            inflight = _station_monitoring_batch_inflight.get(cache_key)
            if inflight is None:
                inflight = threading.Event()
                _station_monitoring_batch_inflight[cache_key] = inflight
                owner = True
        if not owner:
            inflight.wait()
            continue
        try:
            items = _station_monitoring_batch_projections(db, site_rows)
            with _station_monitoring_batch_cache_lock:
                _station_monitoring_batch_cache[cache_key] = {
                    'expires_at': time.monotonic() + _STATION_MONITORING_LIST_REFRESH_SECONDS,
                    'items': items,
                }
                if len(_station_monitoring_batch_cache) > 128:
                    expired = [key for key, value in _station_monitoring_batch_cache.items()
                               if value['expires_at'] <= time.monotonic()]
                    for key in expired:
                        _station_monitoring_batch_cache.pop(key, None)
            return [dict(item) for item in items]
        finally:
            with _station_monitoring_batch_cache_lock:
                if _station_monitoring_batch_inflight.get(cache_key) is inflight:
                    _station_monitoring_batch_inflight.pop(cache_key, None)
                inflight.set()


def _station_monitoring_sites_body(
        db, scope, service_scope, keyword, user, projection_version):
    is_admin = _has_any_role(user, 'admin')
    can_view_all = is_admin or _station_monitoring_access_mode() == 'public'
    responsible_ids = _active_master_site_ids(
        db, _active_operator_site_ids(db, user['id']))
    if service_scope == 'current':
        responsible_ids = _current_service_site_ids(db, responsible_ids)
    mine_count = len(responsible_ids)
    all_where = [_active_master_site_predicate(db, 's')]
    if service_scope == 'current':
        all_where.append(_current_service_site_predicate(db, 's'))
    all_count = db.execute(
        'SELECT COUNT(*) FROM sites s WHERE ' + ' AND '.join(all_where)
    ).fetchone()[0] if can_view_all else None
    where = list(all_where)
    params = []
    if scope == 'mine':
        if not responsible_ids:
            site_rows = []
        else:
            where.append('s.id IN (' + ','.join('?' * len(responsible_ids)) + ')')
            params.extend(responsible_ids)
    if keyword and (scope != 'mine' or responsible_ids):
        escaped = keyword.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        where.append("(s.name LIKE ? ESCAPE '\\' OR s.code LIKE ? ESCAPE '\\')")
        params.extend((f'%{escaped}%', f'%{escaped}%'))
    if scope != 'mine' or responsible_ids:
        order = 's.name, s.id'
        if scope == 'all' and responsible_ids:
            responsible_marks = ','.join('?' * len(responsible_ids))
            order = f'CASE WHEN s.id IN ({responsible_marks}) THEN 0 ELSE 1 END, s.name, s.id'
            params.extend(responsible_ids)
        clause = (' WHERE ' + ' AND '.join(where)) if where else ''
        site_rows = db.execute(
            'SELECT s.id, s.code, s.name, s.type, s.district, s.address, s.river, '
            's.basin, s.gps_lat AS lat, s.gps_lng AS lng, s.manager, s.phone '
            'FROM sites s' + clause + ' ORDER BY ' + order,
            params,
        ).fetchall()
    items = _station_monitoring_batch_projections_cached(
        db, site_rows, projection_version)
    policies = _station_service_policy_rows_batch(
        db, [item['site_id'] for item in items])
    responsible_set = set(responsible_ids)
    for item in items:
        item['is_responsible'] = item['site_id'] in responsible_set
        item['service_policy'] = _station_service_policy_payload(
            db, item['site_id'], policy=policies[item['site_id']])
    counts = {state: sum(item['monitoring_status'] == state for item in items) for state in ('not_connected', 'awaiting_first_frame', 'raw_received_config_pending', 'waiting_first_valid', 'interval_unconfigured', 'normal', 'attention')}
    return {
        'scope': scope,
        'available_scopes': ['mine', 'all'] if can_view_all else ['mine'],
        'service_scope': service_scope,
        'available_service_scopes': ['current', 'all'] if is_admin else ['current'],
        'scope_counts': {'mine': mine_count, 'all': all_count},
        'summary': dict(total=len(items), **counts),
        'items': items,
        'updated_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        'result_snapshot': {'state': 'fresh', 'refreshing': False},
    }


def _station_monitoring_cache_entry(version, body):
    now = time.monotonic()
    return {
        'version': version,
        'refresh_after': now + _STATION_MONITORING_LIST_REFRESH_SECONDS,
        'fresh_until': now + _STATION_MONITORING_LIST_CACHE_SECONDS,
        'stale_until': now + _STATION_MONITORING_LIST_STALE_SECONDS,
        'refreshing': False,
        'refresh_token': None,
        'last_refresh_error_at': None,
        'body': body,
    }


def _station_monitoring_sites_snapshot(scope, service_scope, keyword, user):
    """Read the projection and its version from one SQLite snapshot."""
    with get_db() as db:
        db.execute('BEGIN')
        version = _station_monitoring_list_version(db)
        body = _station_monitoring_sites_body(
            db, scope, service_scope, keyword, user, version)
        return version, body


def _refresh_station_monitoring_sites_cache(
        cache_key, scope, service_scope, keyword, user, site_ids, refresh_token,
        refresh_event):
    try:
        with _app_context():
            g.current_user = dict(user)
            g.user_site_ids = list(site_ids)
            version, body = _station_monitoring_sites_snapshot(
                scope, service_scope, keyword, g.current_user)
            entry = _station_monitoring_cache_entry(version, body)
        with _station_monitoring_list_cache_lock:
            cached = _station_monitoring_list_cache.get(cache_key)
            if cached and cached.get('refresh_token') is refresh_token:
                _station_monitoring_list_cache[cache_key] = entry
    except Exception as exc:
        print(f'[Station monitoring cache] 后台刷新失败，保留短时旧值: {exc}')
        with _station_monitoring_list_cache_lock:
            cached = _station_monitoring_list_cache.get(cache_key)
            if cached and cached.get('refresh_token') is refresh_token:
                cached['refreshing'] = False
                cached['refresh_token'] = None
                cached['last_refresh_error_at'] = datetime.now(timezone.utc).replace(
                    microsecond=0).isoformat()
    finally:
        with _station_monitoring_list_cache_lock:
            if _station_monitoring_list_inflight.get(cache_key) is refresh_event:
                _station_monitoring_list_inflight.pop(cache_key, None)
            refresh_event.set()


def _station_monitoring_sites_cold_body(
        cache_key, scope, service_scope, keyword, user, expected_version):
    while True:
        owner = False
        with _station_monitoring_list_cache_lock:
            cached = _station_monitoring_list_cache.get(cache_key)
            if (cached and cached['version'] == expected_version
                    and cached['stale_until'] > time.monotonic()):
                return cached['body']
            inflight = _station_monitoring_list_inflight.get(cache_key)
            if inflight is None:
                inflight = threading.Event()
                _station_monitoring_list_inflight[cache_key] = inflight
                owner = True
        if not owner:
            inflight.wait()
            continue
        try:
            version, body = _station_monitoring_sites_snapshot(
                scope, service_scope, keyword, user)
            entry = _station_monitoring_cache_entry(version, body)
            with _station_monitoring_list_cache_lock:
                _station_monitoring_list_cache[cache_key] = entry
                if len(_station_monitoring_list_cache) > 256:
                    expired = [key for key, value in _station_monitoring_list_cache.items()
                               if value['stale_until'] <= time.monotonic()]
                    for key in expired:
                        _station_monitoring_list_cache.pop(key, None)
            return body
        finally:
            with _station_monitoring_list_cache_lock:
                if _station_monitoring_list_inflight.get(cache_key) is inflight:
                    _station_monitoring_list_inflight.pop(cache_key, None)
                inflight.set()


def station_monitoring_sites():
    scope = (request.args.get('scope') or 'mine').strip().lower()
    if scope not in {'mine', 'all'}:
        return jsonify({'error': '站点范围参数无效', 'code': 'INVALID_MONITORING_SCOPE'}), 400
    service_scope = (request.args.get('service_scope') or 'current').strip().lower()
    if service_scope not in {'current', 'all'}:
        return jsonify({
            'error': '服务范围参数无效',
            'code': 'INVALID_MONITORING_SERVICE_SCOPE',
        }), 400
    is_admin = _has_any_role(g.current_user, 'admin')
    can_view_all = is_admin or _station_monitoring_access_mode() == 'public'
    if scope == 'all' and not can_view_all:
        return jsonify({'error': '无权查看全部站点', 'code': 'FORBIDDEN_MONITORING_SCOPE'}), 403
    if service_scope == 'all' and not is_admin:
        return jsonify({
            'error': '无权查看已结束服务站点',
            'code': 'FORBIDDEN_MONITORING_SERVICE_SCOPE',
        }), 403
    keyword = (request.args.get('keyword') or '').strip()
    cache_key = (
        os.path.abspath(_database_path()), _station_monitoring_cache_generation,
        g.current_user.get('id'),
        tuple(_normalize_user_roles(
            g.current_user.get('roles') or [g.current_user.get('role')])),
        tuple(sorted(getattr(g, 'user_site_ids', None) or [])),
        _station_monitoring_access_mode(), scope,
        service_scope, keyword,
    )
    now = time.monotonic()
    try:
        with get_db() as db:
            current_version = _station_monitoring_list_version(db)
    except Exception:
        failed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with _station_monitoring_list_cache_lock:
            cached = _station_monitoring_list_cache.get(cache_key)
            if cached and cached['stale_until'] > now:
                cached['last_refresh_error_at'] = failed_at
                response_body = dict(cached['body'])
                response_body['result_snapshot'] = {
                    'state': 'stale', 'refreshing': False,
                    'source_updated_at': cached['body'].get('updated_at'),
                    'last_refresh_error_at': failed_at,
                }
                return jsonify(response_body)
        raise
    refresh = False
    refresh_token = None
    refresh_event = None
    with _station_monitoring_list_cache_lock:
        cached = _station_monitoring_list_cache.get(cache_key)
        if (cached and cached['version'] == current_version
                and cached['stale_until'] > now):
            if (cached['refresh_after'] <= now and not cached['refreshing'] and
                    cache_key not in _station_monitoring_list_inflight):
                cached['refreshing'] = True
                refresh_token = object()
                cached['refresh_token'] = refresh_token
                refresh_event = threading.Event()
                _station_monitoring_list_inflight[cache_key] = refresh_event
                refresh = True
            stale_body = cached['body']
        else:
            stale_body = None
    if stale_body is not None:
        if refresh:
            threading.Thread(
                target=_refresh_station_monitoring_sites_cache,
                args=(cache_key, scope, service_scope, keyword, dict(g.current_user),
                      tuple(getattr(g, 'user_site_ids', None) or []), refresh_token,
                      refresh_event),
                name='station-monitoring-cache-refresh', daemon=True,
            ).start()
        response_body = dict(stale_body)
        is_stale = cached['fresh_until'] <= now
        response_body['result_snapshot'] = {
            'state': 'stale' if is_stale else 'fresh',
            'refreshing': bool(cached.get('refreshing')),
            'source_updated_at': stale_body.get('updated_at'),
            'last_refresh_error_at': cached.get('last_refresh_error_at'),
        }
        return jsonify(response_body)
    return jsonify(_station_monitoring_sites_cold_body(
        cache_key, scope, service_scope, keyword, dict(g.current_user),
        current_version))


def _station_monitoring_overview_projection_body(site_id, version):
    cache_key = (os.path.abspath(_database_path()), site_id, version)
    while True:
        owner = False
        with _station_monitoring_overview_projection_cache_lock:
            cached = _station_monitoring_overview_projection_cache.get(cache_key)
            if cached and cached['expires_at'] > time.monotonic():
                return cached['body'], cached['expires_at']
            inflight = _station_monitoring_overview_projection_inflight.get(cache_key)
            if inflight is None:
                inflight = threading.Event()
                _station_monitoring_overview_projection_inflight[cache_key] = inflight
                owner = True
        if not owner:
            inflight.wait()
            continue
        try:
            with get_db() as db:
                result = _station_monitoring_overview(db, site_id)
            expires_at = time.monotonic() + _STATION_MONITORING_LIST_CACHE_SECONDS
            with _station_monitoring_overview_projection_cache_lock:
                _station_monitoring_overview_projection_cache[cache_key] = {
                    'expires_at': expires_at,
                    'body': result,
                }
                if len(_station_monitoring_overview_projection_cache) > 512:
                    expired = [
                        key for key, value in _station_monitoring_overview_projection_cache.items()
                        if value['expires_at'] <= time.monotonic()
                    ]
                    for key in expired:
                        _station_monitoring_overview_projection_cache.pop(key, None)
            return result, expires_at
        finally:
            with _station_monitoring_overview_projection_cache_lock:
                if _station_monitoring_overview_projection_inflight.get(cache_key) is inflight:
                    _station_monitoring_overview_projection_inflight.pop(cache_key, None)
                inflight.set()


def _station_monitoring_overview_cached_body(cache_key, site_id):
    while True:
        with get_db() as db:
            version = _station_monitoring_list_version(db)
        owner = False
        with _station_monitoring_overview_cache_lock:
            cached = _station_monitoring_overview_cache.get(cache_key)
            if (cached and cached['version'] == version and
                    cached['expires_at'] > time.monotonic()):
                return cached['body']
            inflight = _station_monitoring_overview_inflight.get(cache_key)
            if inflight is None:
                inflight = threading.Event()
                _station_monitoring_overview_inflight[cache_key] = inflight
                owner = True
        if not owner:
            inflight.wait()
            continue
        try:
            result, projection_expires_at = _station_monitoring_overview_projection_body(
                site_id, version)
            if result is None:
                return None
            now = time.monotonic()
            entry = {
                'version': version,
                'expires_at': min(
                    now + _STATION_MONITORING_LIST_CACHE_SECONDS,
                    projection_expires_at,
                ),
                'body': result,
            }
            with _station_monitoring_overview_cache_lock:
                _station_monitoring_overview_cache[cache_key] = entry
                if len(_station_monitoring_overview_cache) > 512:
                    expired = [key for key, value in _station_monitoring_overview_cache.items()
                               if value['expires_at'] <= time.monotonic()]
                    for key in expired:
                        _station_monitoring_overview_cache.pop(key, None)
            return result
        finally:
            with _station_monitoring_overview_cache_lock:
                if _station_monitoring_overview_inflight.get(cache_key) is inflight:
                    _station_monitoring_overview_inflight.pop(cache_key, None)
                inflight.set()


def station_monitoring_expected_factors(site_id):
    with get_db() as db:
        denied = _monitoring_read_access_denied(db, site_id, '读取站点应监测因子')
        if denied:
            return denied
        if not db.execute('SELECT 1 FROM sites WHERE id=?', (site_id,)).fetchone():
            return jsonify({
                'error': '站点不存在', 'code': 'MONITORING_SITE_NOT_FOUND',
            }), 404
        return jsonify(_station_expected_factor_payload(
            db, site_id, g.current_user))


def update_station_monitoring_expected_factors(site_id):
    denied = _site_access_denied(site_id, '设置站点应监测因子')
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    raw_metrics = data.get('metrics')
    if not isinstance(raw_metrics, list):
        return jsonify({
            'error': '应监测因子必须是数组', 'code': 'EXPECTED_FACTORS_INVALID',
        }), 400
    if not raw_metrics:
        return jsonify({
            'error': '设置监测中的站点至少选择一项', 'code': 'EXPECTED_FACTORS_EMPTY',
        }), 400
    if any(not isinstance(metric, str) for metric in raw_metrics):
        return jsonify({
            'error': '应监测因子包含无效值', 'code': 'EXPECTED_FACTORS_INVALID',
        }), 400
    invalid_metrics = sorted(set(raw_metrics) - set(_STATION_EXPECTED_FACTOR_METRICS))
    if invalid_metrics:
        return jsonify({
            'error': '应监测因子超出允许范围',
            'code': 'EXPECTED_FACTORS_INVALID',
            'invalid_metrics': invalid_metrics,
        }), 400
    selected_set = set(raw_metrics)
    metrics = [metric for metric in _STATION_EXPECTED_FACTOR_METRICS
               if metric in selected_set]
    expected_version = data.get('version')
    if isinstance(expected_version, bool):
        expected_version = None
    try:
        expected_version = int(expected_version)
    except (TypeError, ValueError):
        expected_version = None
    if expected_version is None or expected_version < 0:
        return jsonify({
            'error': '配置版本无效，请刷新后重试',
            'code': 'EXPECTED_FACTORS_VERSION_REQUIRED',
        }), 400
    idempotency_key = str(data.get('idempotency_key') or '').strip()
    if not idempotency_key or len(idempotency_key) > 128:
        return jsonify({
            'error': '缺少有效的保存重试标识',
            'code': 'EXPECTED_FACTORS_IDEMPOTENCY_REQUIRED',
        }), 400
    selected_json = json.dumps(metrics, ensure_ascii=False, separators=(',', ':'))
    user_id = g.current_user.get('id')

    with get_db() as db:
        if not db.execute('SELECT 1 FROM sites WHERE id=?', (site_id,)).fetchone():
            return jsonify({
                'error': '站点不存在', 'code': 'MONITORING_SITE_NOT_FOUND',
            }), 404
        if not _station_expected_factor_can_edit(db, site_id, {'id': user_id}):
            return jsonify({
                'error': '当前角色无权设置该站点应监测因子',
                'code': 'EXPECTED_FACTORS_FORBIDDEN',
            }), 403
        replay = db.execute(
            'SELECT * FROM station_expected_factor_configs '
            'WHERE site_id=? AND idempotency_key=?',
            (site_id, idempotency_key),
        ).fetchone()
        if replay:
            if replay['selected_metrics_json'] != selected_json:
                return jsonify({
                    'error': '同一保存重试标识不能用于不同选择',
                    'code': 'EXPECTED_FACTORS_IDEMPOTENCY_CONFLICT',
                }), 409
            return jsonify(_station_expected_factor_payload(
                db, site_id, {'id': user_id}, config=replay, replayed=True))
        current = db.execute(
            'SELECT * FROM station_expected_factor_configs '
            'WHERE site_id=? AND effective_to IS NULL',
            (site_id,),
        ).fetchone()
        current_version = int(current['version']) if current else 0
        if expected_version != current_version:
            return jsonify({
                'error': '应监测因子已被其他操作更新，请刷新后重试',
                'code': 'EXPECTED_FACTORS_VERSION_CONFLICT',
                'current_version': current_version,
            }), 409

    with get_db() as db:
        try:
            db.execute('BEGIN IMMEDIATE')
        except sqlite3.OperationalError as exc:
            if not _sqlite_busy(exc):
                raise
            return jsonify({
                'error': '系统正在提交其他数据，请保留当前选择并使用原保存重试标识重试',
                'code': 'EXPECTED_FACTORS_BUSY',
            }), 503
        site = db.execute('SELECT id FROM sites WHERE id=?', (site_id,)).fetchone()
        if not site:
            db.rollback()
            return jsonify({
                'error': '站点不存在', 'code': 'MONITORING_SITE_NOT_FOUND',
            }), 404
        if not _station_expected_factor_can_edit(db, site_id, {'id': user_id}):
            db.rollback()
            return jsonify({
                'error': '当前角色无权设置该站点应监测因子',
                'code': 'EXPECTED_FACTORS_FORBIDDEN',
            }), 403
        replay = db.execute(
            'SELECT * FROM station_expected_factor_configs '
            'WHERE site_id=? AND idempotency_key=?',
            (site_id, idempotency_key),
        ).fetchone()
        if replay:
            if replay['selected_metrics_json'] != selected_json:
                db.rollback()
                return jsonify({
                    'error': '同一保存重试标识不能用于不同选择',
                    'code': 'EXPECTED_FACTORS_IDEMPOTENCY_CONFLICT',
                }), 409
            result = _station_expected_factor_payload(
                db, site_id, {'id': user_id}, config=replay, replayed=True)
            db.rollback()
            return jsonify(result)
        current = db.execute(
            'SELECT * FROM station_expected_factor_configs '
            'WHERE site_id=? AND effective_to IS NULL',
            (site_id,),
        ).fetchone()
        current_version = int(current['version']) if current else 0
        if expected_version != current_version:
            db.rollback()
            return jsonify({
                'error': '应监测因子已被其他操作更新，请刷新后重试',
                'code': 'EXPECTED_FACTORS_VERSION_CONFLICT',
                'current_version': current_version,
            }), 409
        previous_rows = db.execute(
            'SELECT business_metric,effective_from FROM station_expected_factors '
            'WHERE config_id=? ORDER BY display_order,business_metric',
            (current['id'],),
        ).fetchall() if current else []
        previous_metrics = [row['business_metric'] for row in previous_rows]
        previous_effective = {
            row['business_metric']: row['effective_from'] for row in previous_rows
        }
        now_dt = datetime.now(timezone.utc)
        if current:
            current_from = datetime.fromisoformat(current['effective_from'])
            if current_from.tzinfo is None:
                current_from = current_from.replace(tzinfo=timezone.utc)
            if now_dt <= current_from:
                now_dt = current_from + timedelta(microseconds=1)
        effective_from = now_dt.isoformat(timespec='microseconds')
        if current:
            db.execute(
                'UPDATE station_expected_factor_configs SET effective_to=? WHERE id=?',
                (effective_from, current['id']),
            )
        cursor = db.execute("""INSERT INTO station_expected_factor_configs
            (site_id,version,effective_from,effective_to,previous_metrics_json,
             selected_metrics_json,changed_by_user_id,idempotency_key,created_at)
            VALUES (?,?,?,?,?,?,?,?,?)""", (
                site_id, current_version + 1, effective_from, None,
                json.dumps(previous_metrics, ensure_ascii=False, separators=(',', ':')),
                selected_json, user_id, idempotency_key, effective_from,
            ))
        config_id = cursor.lastrowid
        db.executemany("""INSERT INTO station_expected_factors
            (config_id,site_id,business_metric,display_order,effective_from)
            VALUES (?,?,?,?,?)""", [
                (config_id, site_id, metric, index,
                 previous_effective.get(metric, effective_from))
                for index, metric in enumerate(metrics)
            ])
        db.commit()
        saved = db.execute(
            'SELECT * FROM station_expected_factor_configs WHERE id=?',
            (config_id,),
        ).fetchone()
        result = _station_expected_factor_payload(
            db, site_id, {'id': user_id}, config=saved)
    _invalidate_station_monitoring_projection_caches()
    return jsonify(result)


def station_monitoring_overview(site_id):
    with get_db() as db:
        denied = _monitoring_read_access_denied(db, site_id)
        if denied:
            return denied
        if request.args.get('detail') == 'summary':
            result = _station_monitoring_overview(
                db, site_id, include_diagnostics=False)
            if result is None:
                return jsonify({'error': '站点不存在', 'code': 'MONITORING_SITE_NOT_FOUND'}), 404
            return jsonify(result)
    cache_key = (
        os.path.abspath(_database_path()), _station_monitoring_cache_generation,
        g.current_user.get('id'),
        tuple(_normalize_user_roles(
            g.current_user.get('roles') or [g.current_user.get('role')])),
        tuple(sorted(getattr(g, 'user_site_ids', None) or [])),
        _station_monitoring_access_mode(), site_id,
    )
    result = _station_monitoring_overview_cached_body(cache_key, site_id)
    if result is None:
        return jsonify({'error': '站点不存在', 'code': 'MONITORING_SITE_NOT_FOUND'}), 404
    return jsonify(result)


def station_monitoring_diagnostics(site_id):
    denied = _site_access_denied(site_id, '读取站点监测排障')
    if denied:
        return denied
    with get_db() as db:
        result = _station_monitoring_overview(
            db, site_id, include_diagnostics=True)
    if result is None:
        return jsonify({'error': '站点不存在', 'code': 'MONITORING_SITE_NOT_FOUND'}), 404
    return jsonify({
        'site_id': site_id,
        'axes': result.get('axes') or {},
        'updated_at': result.get('updated_at'),
    })


def station_monitoring_access_summary():
    if not _has_any_role(g.current_user, 'admin'):
        return jsonify({'error': '监测接入摘要仅管理员可读', 'code': 'MONITORING_ACCESS_FORBIDDEN'}), 403
    with get_db() as db:
        endpoint_count = db.execute("SELECT COUNT(*) FROM trusted_endpoints WHERE enabled=1").fetchone()[0]
        site_count = db.execute("SELECT COUNT(DISTINCT business_site_id) FROM trusted_endpoints WHERE enabled=1 AND endpoint_state='bound' AND business_site_id IS NOT NULL").fetchone()[0]
        raw_sites = db.execute("""SELECT COUNT(*) FROM trusted_endpoints endpoint
            WHERE endpoint.enabled=1 AND endpoint.endpoint_state='bound'
              AND endpoint.business_site_id IS NOT NULL AND EXISTS (
                  SELECT 1 FROM station_ingest_current_state state
                  WHERE state.endpoint_id=endpoint.id
                    AND state.latest_authenticated_at IS NOT NULL)""").fetchone()[0]
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        auth_cutoff = _monitoring_auth_diagnostic_cutoff()
        auth_cutoff_hour = datetime.fromisoformat(auth_cutoff).replace(
            minute=0, second=0, microsecond=0).isoformat()
        auth_rows = db.execute("""SELECT authentication_status,SUM(receipt_count) AS frame_count,
                MAX(last_received_at) AS last_received_at
            FROM station_ingest_receipt_hourly
            WHERE bucket_hour>=? AND last_received_at>=?
            GROUP BY authentication_status""", (auth_cutoff_hour, auth_cutoff)).fetchall()
        auth_by_status = {row['authentication_status']: row for row in auth_rows}
        last_transport = db.execute(
            'SELECT MAX(latest_received_at) FROM station_ingest_current_state').fetchone()[0]
        last_authenticated = db.execute(
            """SELECT MAX(latest_authenticated_at) AS received_at
               FROM station_ingest_current_state""").fetchone()
        configured_sites = db.execute("""SELECT COUNT(DISTINCT profile.business_site_id) FROM monitoring_endpoint_profiles profile
            JOIN trusted_endpoints endpoint ON endpoint.id=profile.endpoint_id AND endpoint.enabled=1 AND endpoint.endpoint_state='bound' AND endpoint.business_site_id IS NOT NULL
              AND endpoint.business_site_id=profile.business_site_id
            JOIN station_expected_factor_configs expected_config
              ON expected_config.site_id=profile.business_site_id
             AND expected_config.effective_from<=?
             AND (expected_config.effective_to IS NULL OR expected_config.effective_to>?)
            JOIN station_expected_factors expected ON expected.config_id=expected_config.id
            WHERE profile.enabled=1 AND profile.effective_from<=?
              AND (profile.effective_to IS NULL OR profile.effective_to>?)""",
            (now, now, now, now)).fetchone()[0]
        valid_sites = db.execute("""SELECT COUNT(*) FROM (
            SELECT DISTINCT profile.business_site_id
            FROM monitoring_endpoint_profiles profile
            JOIN trusted_endpoints endpoint ON endpoint.id=profile.endpoint_id
              AND endpoint.enabled=1 AND endpoint.endpoint_state='bound'
              AND endpoint.business_site_id=profile.business_site_id
            JOIN monitoring_factor_mappings mapping ON mapping.endpoint_id=profile.endpoint_id
              AND mapping.enabled=1 AND mapping.effective_from<=?
              AND (mapping.effective_to IS NULL OR mapping.effective_to>?)
            JOIN monitoring_factor_definitions definition ON definition.protocol_code=mapping.protocol_code
              AND definition.is_published=1
            JOIN station_expected_factor_configs expected_config
              ON expected_config.site_id=profile.business_site_id
             AND expected_config.effective_from<=?
             AND (expected_config.effective_to IS NULL OR expected_config.effective_to>?)
            JOIN station_expected_factors expected ON expected.config_id=expected_config.id
              AND expected.business_metric=COALESCE(mapping.business_metric,definition.business_metric)
            WHERE profile.enabled=1 AND profile.effective_from<=?
              AND (profile.effective_to IS NULL OR profile.effective_to>?)
              AND EXISTS (
                  SELECT 1 FROM monitoring_business_observations business
                  INDEXED BY idx_monitoring_business_valid_endpoint_factor
                  JOIN observation_batches batch
                    ON batch.id=business.observation_batch_id AND batch.is_current=1
                  WHERE business.endpoint_id=profile.endpoint_id
                    AND business.business_site_id=profile.business_site_id
                    AND business.protocol_code=mapping.protocol_code
                    AND business.business_metric=COALESCE(mapping.business_metric,business.business_metric)
                    AND business.instrument_asset_code IS COALESCE(
                        mapping.instrument_asset_code,profile.instrument_asset_code)
                    AND business.is_current=1 AND business.quality='valid'
                    AND (business.protocol_code NOT LIKE 'HJ212:%'
                         OR (batch.function_code=2061 AND business.projection_version=?))
              )
        )""", (now, now, now, now, now, now,
                 MONITORING_BUSINESS_PROJECTION_VERSION)).fetchone()[0]
        quality_count = db.execute("SELECT COUNT(*) FROM monitoring_quality_issues WHERE status='open'").fetchone()[0]
        def auth_count(status):
            row = auth_by_status.get(status)
            return int(row['frame_count']) if row else 0
        return jsonify({'runtime': {'enabled_endpoints': endpoint_count}, 'identity': {'bound_sites': site_count, 'received_raw_sites': raw_sites}, 'authentication': {'diagnostic_window_hours': _MONITORING_AUTH_DIAGNOSTIC_HOURS, 'authenticated_frames': auth_count('authenticated'), 'credential_failed_frames': auth_count('credential_failed'), 'unknown_endpoint_frames': auth_count('unknown_endpoint'), 'unbound_authenticated_frames': auth_count('unbound_authenticated'), 'last_transport_received_at': last_transport, 'last_authenticated_received_at': last_authenticated['received_at']}, 'configuration': {'configured_sites': configured_sites}, 'observation': {'valid_sites': valid_sites}, 'quality': {'open_items': quality_count}, 'storage': {'raw_frames': db.execute('SELECT COUNT(*) FROM ingest_raw_frames').fetchone()[0], 'observation_batches': db.execute('SELECT COUNT(*) FROM observation_batches').fetchone()[0]}, 'updated_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat()})


def station_monitoring_summary(site_id):
    with get_db() as db:
        profile, error = _monitoring_site_context(db, site_id, allow_public_read=True)
        if error:
            return error
        last_communication = db.execute(
            """SELECT event.occurred_at, event.received_at FROM monitoring_status_events event
               JOIN observation_batches batch ON batch.id=event.observation_batch_id AND batch.is_current=1
               WHERE event.business_site_id=? AND event.endpoint_id=? AND event.event_axis='communication'
               ORDER BY event.received_at DESC, event.id DESC LIMIT 1""",
            (site_id, profile['endpoint_id']),
        ).fetchone()
        configurations = monitoring_factor_configurations(db, site_id)
        values = monitoring_latest_values(db, site_id)
        communication = _monitoring_freshness(last_communication['received_at'] if last_communication else None,
                                              profile['expected_interval_seconds'])
        factor_states = _station_monitoring_factor_states(db, site_id, configurations, values)
        data_state = _station_monitoring_data_axis(
            configurations, values, db, site_id=site_id, factor_states=factor_states)
        axes = {
            'communication': communication,
            'data': dict(data_state, factors=factor_states),
        }
        attention = ('normal' if communication['state'] == 'fresh' and data_state['status'] != 'attention'
                     else 'attention')
        reason = communication['reason_code'] or data_state.get('reason_code')
        return jsonify({
            'site_id': site_id, 'attention_level': attention, 'reason_code': reason,
            'last_received_at': last_communication['received_at'] if last_communication else None,
            'last_valid_observation_at': max(
                (item.get('observed_at') or item.get('scheduled_at') for item in values
                 if item.get('observed_at') or item.get('scheduled_at')),
                default=None),
            'updated_at': datetime.now().astimezone().replace(microsecond=0).isoformat(), 'axes': axes,
        })


def station_monitoring_latest(site_id):
    with get_db() as db:
        _, error = _monitoring_site_context(db, site_id, allow_public_read=True)
        if error:
            return error
        items = monitoring_latest_values(db, site_id)
        items = [_station_monitoring_presented_value(item) for item in items]
        return jsonify({'site_id': site_id, 'status': 'ok' if items else 'no_observations', 'items': items})


def station_monitoring_trend(site_id):
    metric = str(request.args.get('metric') or '').strip()
    if not metric:
        return jsonify({'error': '必须指定业务因子', 'code': 'MONITORING_METRIC_REQUIRED'}), 400
    range_key = str(request.args.get('range') or '7d').strip().lower()
    range_durations = {
        '24h': timedelta(hours=24),
        '7d': timedelta(days=7),
        '30d': timedelta(days=30),
    }
    if range_key not in range_durations:
        return jsonify({
            'error': '趋势范围仅支持24h、7d、30d或自定义起止时间',
            'code': 'MONITORING_TREND_RANGE_INVALID',
        }), 400
    explicit_window = bool(request.args.get('start') or request.args.get('end'))
    start, start_error = _monitoring_query_time(request.args.get('start'), 'start')
    if start_error:
        return start_error
    end, end_error = _monitoring_query_time(request.args.get('end'), 'end')
    if end_error:
        return end_error
    now = datetime.now(timezone.utc).replace(microsecond=0)
    end = end or now.isoformat()
    if start is None:
        start = (datetime.fromisoformat(end) - range_durations[range_key]).isoformat()
    try:
        start_time, end_time = datetime.fromisoformat(start), datetime.fromisoformat(end)
    except ValueError:  # Defensive: both values were normalized above.
        return jsonify({'error': '趋势时间无效', 'code': 'MONITORING_INVALID_TIME'}), 400
    if end_time < start_time:
        return jsonify({'error': '结束时间早于开始时间', 'code': 'MONITORING_INVALID_WINDOW'}), 400
    point_limit = request.args.get('limit', 1000, type=int)
    if point_limit is None or not 1 <= point_limit <= 1000:
        return jsonify({
            'error': '趋势单页点数必须在1到1000之间',
            'code': 'MONITORING_TREND_LIMIT_INVALID',
        }), 400
    cursor, cursor_error = _monitoring_trend_cursor_decode(request.args.get('cursor'))
    if cursor_error:
        return cursor_error
    with get_db() as db:
        profile, error = _monitoring_site_context(db, site_id, allow_public_read=True)
        if error:
            return error
        configurations = [item for item in monitoring_factor_configurations(db, site_id) if item['business_metric'] == metric]
        if not configurations:
            return jsonify({'error': '业务因子未发布或不存在', 'code': 'MONITORING_METRIC_NOT_PUBLISHED'}), 404
        policy_events = _station_service_policy_events_batch(
            db, [site_id], through=min(end, now.isoformat())).get(site_id, [])
        points, has_more, next_boundary = monitoring_result_trend_page(
            db, site_id, metric, start, end, limit=point_limit, before=cursor)
        points = [point for point in points if _site_reporting_required_at(
            policy_events, site_id, point.get('observed_at')) or point.get('status') == 'value']
        intervals = {
            int(item['expected_interval_seconds']) for item in configurations
            if item.get('expected_interval_seconds')
        }
        interval = next(iter(intervals)) if len(intervals) == 1 else None
        gaps = []
        expected_slots = []
        pending_slots = []
        diagnostics = {
            'late_points': 0, 'suspect_points': 0,
            'duplicate_records': 0, 'conflict_slots': 0,
        }
        coverage_is_complete = (
            cursor is None and not has_more
            and end_time - start_time <= timedelta(days=30)
        )
        if coverage_is_complete:
            diagnostics = monitoring_business_slot_diagnostics(
                db, site_id, metric, start, end)
            slot_records = {
                slot['scheduled_at']: slot
                for configuration in configurations
                for slot in monitoring_expected_business_slots(
                    db, configuration, start, end)
            }
            all_scheduled_slots = sorted(slot_records)
            due_slots = {
                scheduled_at for scheduled_at, slot in slot_records.items()
                if datetime.fromisoformat(slot['result_window_end_at']) <= now
                and _site_reporting_required_at(policy_events, site_id, scheduled_at)
            }
            expected_slots = sorted(due_slots)
            pending_slots = sorted(
                scheduled_at for scheduled_at in set(all_scheduled_slots) - set(expected_slots)
                if _site_reporting_required_at(policy_events, site_id, scheduled_at)
            )
        valid_slots = {
            point['observed_at'] for point in points
            if point['observed_at'] in expected_slots
        }
        missing_slots = [
            index for index, slot in enumerate(expected_slots) if slot not in valid_slots
        ]
        if expected_slots:
            gap_start = None
            previous_slot = None
            for slot in missing_slots:
                if gap_start is None:
                    gap_start = slot
                elif slot != previous_slot + 1:
                    gap_end = previous_slot
                    gap_from = datetime.fromisoformat(expected_slots[gap_start])
                    gap_to = datetime.fromisoformat(expected_slots[gap_end])
                    gaps.append({'from': gap_from.isoformat(), 'to': gap_to.isoformat(),
                                 'seconds': (gap_to - gap_from).total_seconds(),
                                 'missing_points': gap_end - gap_start + 1})
                    gap_start = slot
                previous_slot = slot
            if gap_start is not None:
                gap_end = previous_slot
                gap_from = datetime.fromisoformat(expected_slots[gap_start])
                gap_to = datetime.fromisoformat(expected_slots[gap_end])
                gaps.append({'from': gap_from.isoformat(), 'to': gap_to.isoformat(),
                             'seconds': (gap_to - gap_from).total_seconds(),
                             'missing_points': gap_end - gap_start + 1})
        expected_points = len(expected_slots) if coverage_is_complete else None
        valid_points = len(valid_slots) if coverage_is_complete else None
        displayed_points = len(points)
        if not coverage_is_complete:
            coverage_state = 'partial_page' if cursor is not None or has_more else 'not_evaluated'
        elif expected_slots or all(item.get('schedule_id') for item in configurations):
            coverage_state = 'configured'
        else:
            coverage_state = 'unconfigured'
        return jsonify({
            'site_id': site_id, 'metric': metric,
            'factor_name_cn': _STATION_MONITORING_FACTOR_LABELS.get(metric, '监测因子'),
            'standard_unit': configurations[0].get('standard_unit') or '',
            'decimal_precision': max(
                int(item.get('decimal_precision') or 0) for item in configurations),
            'points': points,
            'window': {
                'preset': 'custom' if explicit_window else range_key,
                'start': start, 'end': end,
            },
            'pagination': {
                'limit': point_limit, 'has_more': has_more,
                'next_cursor': _monitoring_trend_cursor_encode(next_boundary),
            },
            'coverage': {
                'state': coverage_state,
                'expected_interval_seconds': interval,
                'valid_points': valid_points, 'displayed_points': displayed_points, 'expected_points': expected_points,
                'coverage_rate': valid_points / expected_points if expected_points else None,
                'gap_count': len(gaps) if coverage_is_complete else None,
                'missing_points': len(missing_slots) if coverage_is_complete else None,
                'pending_points': len(pending_slots) if coverage_is_complete else None,
                **diagnostics,
                'window_start': start, 'window_end': end, 'point_limit': point_limit,
            }, 'gaps': gaps, 'normalization_version': points[-1].get('normalization_version') if points else None,
        })


def station_monitoring_instruments(site_id):
    with get_db() as db:
        profile, error = _monitoring_site_context(db, site_id, allow_public_read=True)
        if error:
            return error
        rows = [item for item in monitoring_factor_configurations(db, site_id) if item['endpoint_id'] == profile['endpoint_id']]
        items = [{
            'business_metric': row.get('business_metric'),
            'protocol_code': row.get('protocol_code'),
            'factor_name_cn': _STATION_MONITORING_FACTOR_LABELS.get(row.get('business_metric'), '监测因子'),
            'standard_unit': row.get('standard_unit') or '',
            'expected_interval_seconds': row.get('expected_interval_seconds'),
        } for row in rows]
        return jsonify({'site_id': site_id, 'status': 'ok' if items else 'no_instruments', 'items': items})


def station_monitoring_quality_issues():
    if not _has_any_role(g.current_user, 'admin'):
        return jsonify({'error': '数据质量事项仅管理员可读', 'code': 'MONITORING_QUALITY_FORBIDDEN'}), 403
    site_id = request.args.get('site_id', type=int)
    page = request.args.get('page', 1, type=int)
    page_size = request.args.get('page_size', 50, type=int)
    if page is None or page < 1 or page_size is None or not 1 <= page_size <= 100:
        return jsonify({'error': '分页参数无效', 'code': 'MONITORING_INVALID_PAGINATION'}), 400
    with get_db() as db:
        if site_id is not None and not db.execute('SELECT 1 FROM sites WHERE id=?', (site_id,)).fetchone():
            return jsonify({'error': '站点不存在', 'code': 'MONITORING_SITE_NOT_FOUND'}), 404
        where, params = ('', []) if site_id is None else (' WHERE business_site_id=?', [site_id])
        total = db.execute('SELECT COUNT(*) FROM monitoring_quality_issues' + where, params).fetchone()[0]
        rows = db.execute(
            'SELECT * FROM monitoring_quality_issues' + where + ' ORDER BY last_seen_at DESC, id DESC LIMIT ? OFFSET ?',
            params + [page_size, (page - 1) * page_size],
        ).fetchall()
        return jsonify({'site_id': site_id, 'items': [dict(row) for row in rows],
                        'page': page, 'page_size': page_size, 'total': total})



def register_station_monitoring_api(flask_app, dependencies):
    """Register monitoring routes without importing the application module."""
    global _shared, _STATION_MONITORING_SITE_TYPE_LABELS
    _shared = dict(dependencies)
    _STATION_MONITORING_SITE_TYPE_LABELS = dict(
        _dependency("site_type_labels"))
    extension_key = "station_monitoring_api_registered"
    if flask_app.extensions.get(extension_key):
        return
    login_required = _dependency("login_required")
    routes = (
        ("/api/station-monitoring/sites", "station_monitoring_sites", station_monitoring_sites, None),
        ("/api/station-monitoring/sites/<int:site_id>/expected-factors", "station_monitoring_expected_factors", station_monitoring_expected_factors, None),
        ("/api/station-monitoring/sites/<int:site_id>/expected-factors", "update_station_monitoring_expected_factors", update_station_monitoring_expected_factors, ("PUT",)),
        ("/api/station-monitoring/sites/<int:site_id>/overview", "station_monitoring_overview", station_monitoring_overview, None),
        ("/api/station-monitoring/sites/<int:site_id>/diagnostics", "station_monitoring_diagnostics", station_monitoring_diagnostics, None),
        ("/api/station-monitoring/access-summary", "station_monitoring_access_summary", station_monitoring_access_summary, None),
        ("/api/station-monitoring/sites/<int:site_id>/summary", "station_monitoring_summary", station_monitoring_summary, None),
        ("/api/station-monitoring/sites/<int:site_id>/latest", "station_monitoring_latest", station_monitoring_latest, None),
        ("/api/station-monitoring/sites/<int:site_id>/trend", "station_monitoring_trend", station_monitoring_trend, None),
        ("/api/station-monitoring/sites/<int:site_id>/instruments", "station_monitoring_instruments", station_monitoring_instruments, None),
        ("/api/station-monitoring/quality-issues", "station_monitoring_quality_issues", station_monitoring_quality_issues, None),
    )
    for rule, endpoint, view, methods in routes:
        options = {"methods": methods} if methods else {}
        flask_app.add_url_rule(
            rule, endpoint=endpoint, view_func=login_required(view), **options)
    flask_app.extensions[extension_key] = True


COMPAT_EXPORTS = tuple(sorted(
    name for name in globals()
    if (name.startswith("_station_monitoring")
        or name == "_invalidate_station_monitoring_projection_caches")
))
