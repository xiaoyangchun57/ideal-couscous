import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Card, Button, Space, Tag, Typography, message, Modal, Input, Empty,
  Image, Badge, Tooltip, Spin, Result,
} from 'antd';
import {
  ReloadOutlined, CheckOutlined, CloseOutlined, ExclamationCircleOutlined,
  CameraOutlined, CheckCircleOutlined,
} from '@ant-design/icons';
import { api } from '../../services/api';
import { useTheme } from '../../hooks/useTheme';
import { useAuth } from '../../hooks/useAuth';

const { Title, Text } = Typography;

/**
 * 异常驱动审核页（P2）
 * 核心反转：默认全部通过，只标记异常项，其余一键通过。
 * 48张照片只有1张问题时，只需点2次（点问题项+确认），而非48次"通过"。
 */
export default function BatchReviewPage() {
  const { tokens } = useTheme();
  const { user } = useAuth();
  const canReview = user?.role === 'admin' || user?.role === 'manager';

  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [markedIds, setMarkedIds] = useState(new Set()); // 标记为异常的 item ids
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/inspection-v2/items/pending');
      // 解析 photo_urls JSON
      const parsed = (Array.isArray(res) ? res : []).map(it => {
        let photos = [];
        try { photos = it.photo_urls ? JSON.parse(it.photo_urls) : []; } catch { photos = []; }
        return { ...it, photos_arr: photos };
      });
      setItems(parsed);
      setMarkedIds(new Set());
    } catch {
      message.error('加载待审项失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // 按站点分组
  const grouped = useMemo(() => {
    const map = {};
    items.forEach(it => {
      const key = it.site_name || `站点${it.site_id}`;
      if (!map[key]) map[key] = [];
      map[key].push(it);
    });
    return Object.entries(map);
  }, [items]);

  const passCount = items.length - markedIds.size;
  const rejectCount = markedIds.size;

  const toggleMark = (id) => {
    setMarkedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const onConfirm = async () => {
    if (rejectCount > 0 && !rejectReason.trim()) {
      message.warning('请填写驳回原因');
      return;
    }
    setSubmitting(true);
    try {
      const approveIds = items.filter(it => !markedIds.has(it.id)).map(it => it.id);
      const rejectItems = items.filter(it => markedIds.has(it.id)).map(it => ({
        id: it.id,
        reason: rejectReason.trim() || '照片不合格',
      }));
      const res = await api.post('/inspection-v2/items/batch-review', {
        approve_ids: approveIds,
        reject_items: rejectItems,
      });
      message.success(res.message || `通过 ${res.approved} 项，驳回 ${res.rejected} 项`);
      setConfirmOpen(false);
      setRejectReason('');
      load(); // 刷新
    } catch (e) {
      message.error(e?.message || '提交失败');
    } finally {
      setSubmitting(false);
    }
  };

  if (!canReview) {
    return <Result status="403" title="无权限" subTitle="仅管理员/审批者可进行审核" />;
  }

  return (
    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* 标题 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <Title level={4} style={{ margin: 0 }}>巡检照片审核</Title>
          <Text type="secondary" style={{ fontSize: 12 }}>
            异常驱动：点击标记问题项，其余一键通过。共 {items.length} 项待审
          </Text>
        </div>
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
      </div>

      {/* 内容区 */}
      <div style={{ flex: 1, overflow: 'auto', marginBottom: 72 }}>
        {loading && <div style={{ textAlign: 'center', padding: 60 }}><Spin /></div>}
        {!loading && items.length === 0 && (
          <Empty description="暂无待审核项目" style={{ marginTop: 60 }} />
        )}
        {!loading && grouped.map(([siteName, siteItems]) => (
          <div key={siteName} style={{ marginBottom: 20 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
              <Text strong style={{ fontSize: 14 }}>{siteName}</Text>
              <Tag style={{ fontSize: 11 }}>{siteItems.length} 项</Tag>
              <Tag color="blue" style={{ fontSize: 11 }}>
                {siteItems.reduce((s, it) => s + it.photos_arr.length, 0)} 张照片
              </Tag>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 12 }}>
              {siteItems.map(it => {
                const isMarked = markedIds.has(it.id);
                return (
                  <Card
                    key={it.id}
                    size="small"
                    onClick={() => toggleMark(it.id)}
                    style={{
                      cursor: 'pointer',
                      border: isMarked ? '2px solid #f5222d' : `1px solid ${tokens.colorBorder}`,
                      background: isMarked ? '#fff1f0' : tokens.colorBgContainer,
                      transition: 'all 0.2s',
                    }}
                    bodyStyle={{ padding: 12 }}
                  >
                    {/* 检查项信息 */}
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                      <Space size={6}>
                        {isMarked
                          ? <ExclamationCircleOutlined style={{ color: '#f5222d' }} />
                          : <CheckCircleOutlined style={{ color: '#52c41a' }} />}
                        <Text strong style={{ fontSize: 12 }}>{it.item_name}</Text>
                      </Space>
                      <Tag color={isMarked ? 'error' : 'success'} style={{ fontSize: 10 }}>
                        {isMarked ? '驳回' : '通过'}
                      </Tag>
                    </div>
                    {/* 照片网格 */}
                    {it.photos_arr.length > 0 ? (
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                        {it.photos_arr.map((url, i) => (
                          <Image
                            key={i}
                            src={url}
                            width={64}
                            height={64}
                            style={{ objectFit: 'cover', borderRadius: 4, border: isMarked ? '1px solid #ffa39e' : '1px solid #f0f0f0' }}
                            preview={{ mask: <CameraOutlined /> }}
                            onClick={e => e.stopPropagation()}
                          />
                        ))}
                      </div>
                    ) : (
                      <Text type="secondary" style={{ fontSize: 11 }}>无照片</Text>
                    )}
                    {/* 元信息 */}
                    <div style={{ marginTop: 6, display: 'flex', gap: 8, fontSize: 11, color: tokens.colorTextSecondary }}>
                      <span>{it.check_time || ''}</span>
                      {it.remark && <Tooltip title={it.remark}><span>备注</span></Tooltip>}
                    </div>
                  </Card>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {/* 底部操作栏 */}
      {items.length > 0 && (
        <div style={{
          position: 'fixed', bottom: 0, left: 200, right: 0,
          background: tokens.colorBgContainer,
          borderTop: `1px solid ${tokens.colorBorder}`,
          padding: '12px 24px',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          zIndex: 10,
        }}>
          <Space size={16}>
            <Text style={{ fontSize: 13 }}>
              <span style={{ color: '#52c41a', fontWeight: 600 }}>{passCount}</span> 项通过
            </Text>
            <Text style={{ fontSize: 13 }}>
              <span style={{ color: rejectCount > 0 ? '#f5222d' : undefined, fontWeight: 600 }}>{rejectCount}</span> 项驳回
            </Text>
            {rejectCount === 0 && (
              <Text type="secondary" style={{ fontSize: 11 }}>点击卡片标记异常项</Text>
            )}
          </Space>
          <Button
            type="primary"
            size="large"
            icon={<CheckOutlined />}
            onClick={() => setConfirmOpen(true)}
            disabled={items.length === 0}
          >
            确认：通过 {passCount} 项{rejectCount > 0 ? `，驳回 ${rejectCount} 项` : ''}
          </Button>
        </div>
      )}

      {/* 确认弹窗（驳回需填原因） */}
      <Modal
        open={confirmOpen}
        title="确认审核结果"
        okText="确认提交"
        cancelText="取消"
        onOk={onConfirm}
        onCancel={() => setConfirmOpen(false)}
        confirmLoading={submitting}
        okButtonProps={{ danger: rejectCount > 0 }}
      >
        <div style={{ marginBottom: 12 }}>
          <Text>通过 <Text strong style={{ color: '#52c41a' }}>{passCount}</Text> 项</Text>
          {rejectCount > 0 && (
            <Text style={{ marginLeft: 16 }}>驳回 <Text strong style={{ color: '#f5222d' }}>{rejectCount}</Text> 项</Text>
          )}
        </div>
        {rejectCount > 0 && (
          <div>
            <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 6 }}>
              驳回原因（将通知运维人员）：
            </Text>
            <Input.TextArea
              rows={3}
              placeholder="如：照片模糊/拍错/未拍到关键内容…"
              value={rejectReason}
              onChange={e => setRejectReason(e.target.value)}
            />
          </div>
        )}
      </Modal>
    </div>
  );
}
