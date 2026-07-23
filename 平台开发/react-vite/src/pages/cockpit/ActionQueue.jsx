import { useState, useEffect, useCallback } from 'react';
import { Badge, Spin, Empty } from 'antd';
import {
  AuditOutlined,
  WarningOutlined,
  FileTextOutlined,
  CloseOutlined,
  RightOutlined,
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
        api.get('/audit/pending').catch(() => []),
        api.get('/plan-schedules?status=submitted').catch(() => []),
        api.get('/plan-schedules?status=change_submitted').catch(() => []),
        api.get('/inspection-v2/items/pending').catch(() => []),
        api.get('/alerts?status=pending').catch(() => []),
        api.get('/workorders?status=pending').catch(() => []),
      ]);
      const auditList = Array.isArray(audit) ? audit : [];
      const alertList = Array.isArray(alerts) ? alerts : [];
      const woList = Array.isArray(wos) ? wos : [];
      // 相同类型且在 30 分钟窗口内发生的告警视为一个事件，避免系统性离线淹没待决队列。
      const incidentKeys = new Set(alertList.map((a) => {
        const timestamp = new Date(String(a.created_at || '').replace(' ', 'T')).getTime();
        const bucket = Number.isFinite(timestamp) ? Math.floor(timestamp / 1800000) : a.id;
        return `${a.metric || a.event_type || 'unknown'}:${bucket}`;
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
    } catch {
      /* 静默：行动队列加载失败不应阻塞首页地图 */
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
    { key: 'businessApprovals', icon: <AuditOutlined />, label: '业务申请待审批', count: items.businessApprovals, to: '/audit?tab=spareparts', color: '#fa8c16' },
    { key: 'planApprovals', icon: <AuditOutlined />, label: '巡检计划待审批', count: items.planApprovals, to: '/plan-schedules?status=submitted', color: '#fa8c16' },
    { key: 'photoReviews', icon: <AuditOutlined />, label: '巡检照片待审核', count: items.photoReviews, to: '/batch-review', color: '#fa8c16' },
    { key: 'incidents', icon: <WarningOutlined />, label: '异常事件（按站点聚合）', count: items.incidents, to: '/alerts?status=pending', color: '#f5222d' },
    { key: 'workorders', icon: <FileTextOutlined />, label: '工单待处理', count: items.workorders, to: '/workorders', color: '#2b6cff' },
  ].filter((row) => row.count > 0);

  // 折叠态：仅一个悬浮计数按钮
  if (!open) {
    return (
      <div
        onClick={() => setOpen(true)}
        style={{
          position: 'absolute', bottom: 56, left: '50%', transform: 'translateX(-50%)', zIndex: 1500,
          cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6,
          background: tokens.colorBgElevated, border: `1px solid ${tokens.colorBorder}`,
          borderRadius: 24, padding: '8px 14px', boxShadow: tokens.shadowNav,
          color: tokens.colorText,
        }}
      >
        <AuditOutlined style={{ color: tokens.colorPrimary }} />
        <span style={{ fontSize: 13, fontWeight: 600 }}>待决 {total}</span>
      </div>
    );
  }

  return (
    <div
      style={{
        position: 'absolute', bottom: 56, left: '50%', transform: 'translateX(-50%)', zIndex: 1500,
        width: 320, maxHeight: '45vh', display: 'flex', flexDirection: 'column',
        background: tokens.colorBgElevated, border: `1px solid ${tokens.colorBorder}`,
        borderRadius: 12, boxShadow: tokens.shadowNav, overflow: 'hidden',
      }}
    >
      <div
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '8px 12px', borderBottom: `1px solid ${tokens.colorBorder}`,
          background: tokens.colorPrimaryBg,
        }}
      >
        <span style={{ fontSize: 14, fontWeight: 600, color: tokens.colorText }}>
          接下来要我决定的事（{total}）
        </span>
        <CloseOutlined
          onClick={() => setOpen(false)}
          style={{ cursor: 'pointer', color: tokens.colorTextSecondary, fontSize: 12 }}
        />
      </div>

      <div style={{ padding: 8, overflowY: 'auto' }}>
        {loading ? (
          <div style={{ textAlign: 'center', padding: 24 }}><Spin /></div>
        ) : total === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="暂无可决事项"
            style={{ padding: 16 }}
          />
        ) : (
          rows.map((r) => (
            <div
              key={r.key}
              onClick={() => navigate(r.to)}
              style={{
                display: 'flex', alignItems: 'center', gap: 10,
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
            </div>
          ))
        )}
      </div>
    </div>
  );
}
