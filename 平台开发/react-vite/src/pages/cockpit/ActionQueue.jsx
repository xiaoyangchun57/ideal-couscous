import { useState, useEffect, useCallback } from 'react';
import { Alert, Badge, Button, Spin } from 'antd';
import {
  AuditOutlined,
  WarningOutlined,
  FileTextOutlined,
  CloseOutlined,
  RightOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { api } from '../../services/api';
import { useAuth } from '../../hooks/useAuth';
import { useTheme } from '../../hooks/useTheme';

// 管理者首屏行动队列：把"接下来要我决定的事"前置，仪表盘（地图/图表）作为下钻背景。
// 仅管理员/主管可见；移动端/巡检员/访客不显示（他们不是决策者）。
export default function ActionQueue() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const { tokens } = useTheme();
  const isDecisionMaker = user?.role === 'admin' || user?.role === 'manager';

  const [open, setOpen] = useState(true);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [items, setItems] = useState({
    businessApprovals: 0,
    planApprovals: 0,
    photoReviews: 0,
    incidents: 0,
    workorders: 0,
  });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [audit, submittedPlans, changedPlans, photoItems, alerts, wos] = await Promise.all([
        api.getStrict('/audit/pending'),
        api.getStrict('/plan-schedules?status=submitted'),
        api.getStrict('/plan-schedules?status=change_submitted'),
        api.getStrict('/inspection-v2/items/pending'),
        api.getStrict('/alerts?status=pending'),
        api.getStrict('/workorders?status=pending'),
      ]);
      const auditList = Array.isArray(audit) ? audit : [];
      const alertList = Array.isArray(alerts) ? alerts : [];
      const woList = Array.isArray(wos) ? wos : [];
      // 同一站点、同一类型且在 30 分钟窗口内发生的告警视为一个事件。
      const incidentKeys = new Set(alertList.map((a) => {
        const timestamp = new Date(String(a.created_at || '').replace(' ', 'T')).getTime();
        const bucket = Number.isFinite(timestamp) ? Math.floor(timestamp / 1800000) : a.id;
        return `${a.site_id || 'unknown-site'}:${a.metric || a.event_type || 'unknown'}:${bucket}`;
      }));
      setItems({
        businessApprovals: auditList.length,
        planApprovals:
          (Array.isArray(submittedPlans) ? submittedPlans.length : 0)
          + (Array.isArray(changedPlans) ? changedPlans.length : 0),
        photoReviews: Array.isArray(photoItems) ? photoItems.length : 0,
        incidents: incidentKeys.size,
        workorders: woList.length,
      });
      setLoadError('');
    } catch (error) {
      setLoadError(error.message || '待办加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isDecisionMaker) load();
  }, [isDecisionMaker, load]);

  // 5 分钟自动刷新，保持待决项新鲜
  useEffect(() => {
    if (!isDecisionMaker) return;
    const t = setInterval(load, 300000);
    return () => clearInterval(t);
  }, [isDecisionMaker, load]);

  if (!isDecisionMaker) return null;

  const total = Object.values(items).reduce((sum, count) => sum + count, 0);

  const rows = [
    { key: 'businessApprovals', icon: <AuditOutlined />, label: '业务申请待审批', count: items.businessApprovals, to: '/audit?tab=spareparts', color: tokens.colorWarning },
    { key: 'planApprovals', icon: <AuditOutlined />, label: '巡检计划待审批', count: items.planApprovals, to: '/plan-schedules?status=submitted', color: tokens.colorWarning },
    { key: 'photoReviews', icon: <AuditOutlined />, label: '巡检照片待审核', count: items.photoReviews, to: '/batch-review', color: tokens.colorWarning },
    { key: 'incidents', icon: <WarningOutlined />, label: '异常事件（按站点聚合）', count: items.incidents, to: '/alerts?status=pending', color: tokens.colorError },
    { key: 'workorders', icon: <FileTextOutlined />, label: '工单待处理', count: items.workorders, to: '/workorders', color: tokens.colorInfo },
  ].filter((row) => row.count > 0);

  // 折叠态保留在右栏文档流中，避免覆盖地图与图例。
  if (!open) {
    return (
      <button
        className="action-queue-collapsed"
        type="button"
        aria-label={`展开待决事项，共 ${total} 项`}
        onClick={() => setOpen(true)}
        style={{
          cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6,
          width: '100%', minHeight: 40,
          background: tokens.colorBgElevated, border: `1px solid ${tokens.colorBorder}`,
          borderRadius: 8, padding: '8px 12px', boxShadow: tokens.shadowNav,
          color: tokens.colorText,
        }}
      >
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <AuditOutlined style={{ color: tokens.colorPrimary }} />
          <span style={{ fontSize: 13, fontWeight: 600 }}>待决事项</span>
        </span>
        <Badge count={total} showZero overflowCount={999} />
      </button>
    );
  }

  return (
    <div
      className="action-queue-panel"
      style={{
        width: '100%', maxHeight: 220, display: 'flex', flexDirection: 'column', flexShrink: 0,
        background: tokens.colorBgElevated, border: `1px solid ${tokens.colorBorder}`,
        borderRadius: 8, boxShadow: tokens.shadowNav, overflow: 'hidden',
      }}
    >
      <div
        className="action-queue-header"
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '8px 12px 8px 28px', borderBottom: `1px solid ${tokens.colorBorder}`,
          background: tokens.colorPrimaryBg,
        }}
      >
        <span style={{ fontSize: 14, fontWeight: 600, color: tokens.colorText }}>
          接下来要我决定的事（{total}）
        </span>
        <Button type="text" size="small" icon={<CloseOutlined />} aria-label="收起待决事项" onClick={() => setOpen(false)} />
      </div>

      <div style={{ padding: 8, overflowY: 'auto' }}>
        {loading ? (
          <div style={{ textAlign: 'center', padding: 24 }}><Spin /></div>
        ) : loadError ? (
          <Alert
            type="error"
            showIcon
            message="待办加载失败，当前数量不完整"
            action={<Button size="small" icon={<ReloadOutlined />} onClick={load}>重试</Button>}
          />
        ) : total === 0 ? (
          <div style={{ padding: '8px 10px', color: tokens.colorTextSecondary, fontSize: 13 }}>
            当前没有待决事项
          </div>
        ) : (
          rows.map((r) => (
            <button
              type="button"
              aria-label={`${r.label}，${r.count} 项`}
              key={r.key}
              onClick={() => {
                api.track('action_queue.entered', { queue_key: r.key, site_id: undefined });
                navigate(r.to);
              }}
              style={{
                display: 'flex', alignItems: 'center', gap: 10,
              width: '100%', border: 0, background: 'transparent', textAlign: 'left',
              padding: '8px 10px', borderRadius: 6, cursor: 'pointer',
                marginBottom: 4,
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = tokens.colorFillSecondary)}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
            >
              <span style={{ color: r.color, fontSize: 16 }}>{r.icon}</span>
              <span style={{ flex: 1, fontSize: 13, color: tokens.colorText }}>{r.label}</span>
              <Badge count={r.count} showZero={false} overflowCount={999} style={{ marginRight: 4 }} />
              <RightOutlined style={{ color: tokens.colorTextTertiary, fontSize: 11 }} />
            </button>
          ))
        )}
      </div>
    </div>
  );
}
