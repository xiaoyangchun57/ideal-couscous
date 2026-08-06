import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Empty,
  Progress,
  Spin,
  Table,
  Tag,
  Timeline,
  Typography,
} from 'antd';
import {
  CalendarOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  EnvironmentOutlined,
  ExclamationCircleOutlined,
  FileDoneOutlined,
  ReloadOutlined,
  TeamOutlined,
  ToolOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { api } from '../../services/api';
import { useTheme } from '../../hooks/useTheme';
import './OperationsTodayView.css';

const { Text } = Typography;

const STATUS_COLOR = {
  no_task: 'default',
  no_checkin: 'default',
  carryover: 'orange',
  inspection: 'processing',
  workorder: 'gold',
  mixed: 'cyan',
  completed: 'success',
};

function timeOnly(value) {
  if (!value) return '—';
  const match = String(value).match(/(?:\s|T)(\d{2}:\d{2})/);
  return match ? match[1] : String(value);
}

function formatStamp(value) {
  if (!value) return '暂无记录';
  return String(value).replace('T', ' ').slice(0, 16);
}

function workLabel(person) {
  if (person.status_code === 'mixed') return '巡检 + 工单';
  if (person.status_code === 'workorder') return person.active_workorder?.title || '现场工单';
  if (person.status_code === 'inspection') return '巡检执行';
  if (person.status_code === 'completed') return '今日任务已完成';
  if (person.status_code === 'carryover') return `${person.carryover_executions || 0} 个历史执行包待处理`;
  if (person.status_code === 'no_checkin' && person.assigned_site_count) return `${person.assigned_site_count} 个今日站点待执行`;
  if (person.status_code === 'no_checkin' && person.carryover_executions) return `${person.carryover_executions} 个遗留执行包待处理`;
  if (person.status_code === 'no_checkin') return '有任务待开始';
  return '—';
}

export default function OperationsTodayView() {
  const navigate = useNavigate();
  const { tokens } = useTheme();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [selectedUserId, setSelectedUserId] = useState(null);

  const load = useCallback(async (manual = false) => {
    if (manual) setRefreshing(true);
    else setLoading(true);
    setError('');
    try {
      const result = await api.getStrict('/cockpit/operations-today');
      if (!result || !result.summary || !Array.isArray(result.people)) {
        throw new Error('接口未返回完整的人员执行数据');
      }
      setData(result);
      setSelectedUserId((current) => {
        if (current && result.people.some((person) => person.user_id === current)) return current;
        const attentionUser = result.attention?.[0]?.user_id;
        return attentionUser || result.people.find((person) => person.has_task)?.user_id || result.people[0]?.user_id || null;
      });
    } catch (requestError) {
      setError(requestError?.message || '今日运维数据加载失败');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const selectedPerson = useMemo(
    () => data?.people?.find((person) => person.user_id === selectedUserId) || null,
    [data, selectedUserId],
  );

  const metrics = useMemo(() => {
    const summary = data?.summary || {};
    return [
      { key: 'tasks', label: '今日有任务', value: summary.people_with_tasks || 0, icon: <TeamOutlined /> },
      { key: 'checkin', label: '已有首站打卡', value: summary.people_checked_in || 0, icon: <EnvironmentOutlined /> },
      { key: 'working', label: '现场作业中', value: summary.people_working || 0, icon: <ClockCircleOutlined /> },
      { key: 'attention', label: '需管理者关注', value: summary.people_attention || 0, icon: <ExclamationCircleOutlined /> },
      { key: 'completed', label: '今日已完成', value: summary.people_completed || 0, icon: <CheckCircleOutlined /> },
    ];
  }, [data]);

  const columns = useMemo(() => [
    {
      title: '人员 / 状态', key: 'person', width: 128,
      render: (_, person) => (
        <button
          type="button"
          className="operations-person-cell"
          aria-label={`查看 ${person.real_name} 的今日执行详情`}
          aria-pressed={person.user_id === selectedUserId}
          onClick={(event) => {
            event.stopPropagation();
            setSelectedUserId(person.user_id);
          }}
        >
          <Text strong>{person.real_name}</Text>
          <Tag color={STATUS_COLOR[person.status_code]}>{person.status_label}</Tag>
        </button>
      ),
    },
    {
      title: '首站打卡', dataIndex: 'first_checkin_at', key: 'first_checkin_at', width: 82,
      render: (value) => value ? <Text strong>{timeOnly(value)}</Text> : <Text type="secondary">未到站</Text>,
    },
    {
      title: '当前或最近站点', key: 'site', width: 148,
      render: (_, person) => person.latest_site_name ? (
        <div><Text>{person.latest_site_name}</Text><div className="operations-secondary">{timeOnly(person.latest_site_checkin_at)} 到站</div></div>
      ) : (
        <div>
          <Text type="secondary">尚无到站记录</Text>
          <div className="operations-secondary">
            {person.assigned_site_count
              ? `今日安排 ${person.assigned_site_count} 站`
              : person.carryover_executions
                ? `${person.carryover_executions} 个遗留执行包待处理`
                : '暂无站点安排'}
          </div>
        </div>
      ),
    },
    {
      title: '正在处理', key: 'work', width: 150,
      render: (_, person) => (
        <div>
          <Text>{workLabel(person)}</Text>
          {person.open_workorders > 0 && (
            <div className="operations-secondary">{person.open_workorders} 件开放工单</div>
          )}
        </div>
      ),
    },
    {
      title: '全天巡检进度', key: 'progress', width: 136,
      render: (_, person) => person.today_items ? (
        <div className="operations-progress-cell">
          <span>{person.completed_items}/{person.today_items}</span>
          <Progress percent={person.completion_rate} size="small" showInfo={false} strokeColor={tokens.colorPrimary} />
        </div>
      ) : <Text type="secondary">无巡检项</Text>,
    },
    {
      title: '最后动态', key: 'last_activity', width: 124,
      render: (_, person) => person.last_activity_at ? (
        <div><Text>{timeOnly(person.last_activity_at)}</Text><div className="operations-secondary">{person.last_activity_label}</div></div>
      ) : <Text type="secondary">暂无业务记录</Text>,
    },
  ], [selectedUserId, tokens.colorPrimary]);

  if (loading) {
    return (
      <div className="operations-state" style={{ flexDirection: 'column', gap: 12 }}>
        <Spin size="large" />
        <Text type="secondary">正在加载今日运维数据</Text>
      </div>
    );
  }

  if (error) {
    return (
      <div className="operations-page">
        <Alert
          type="error"
          showIcon
          message="今日运维数据加载失败"
          description={`${error}。请确认本地后端正常后重试。`}
          action={<Button icon={<ReloadOutlined />} onClick={() => load(true)}>重新加载</Button>}
        />
      </div>
    );
  }

  return (
    <div className="operations-page">
      <div className="operations-toolbar">
        <div className="operations-date">
          <CalendarOutlined />
          <Text strong>{data?.date || '今日'}</Text>
          <Text type="secondary">今日</Text>
        </div>
        <div className="operations-refresh">
          <Text type="secondary">{formatStamp(data?.refreshed_at)} 更新</Text>
          <Button icon={<ReloadOutlined spin={refreshing} />} loading={refreshing} onClick={() => load(true)}>刷新</Button>
        </div>
      </div>

      <section className="operations-metrics" aria-label="今日运维摘要">
        {metrics.map((metric) => (
          <div className={`operations-metric operations-metric--${metric.key}`} key={metric.key}>
            <span className="operations-metric-icon">{metric.icon}</span>
            <div><div className="operations-metric-label">{metric.label}</div><div className="operations-metric-value">{metric.value}<small>人</small></div></div>
          </div>
        ))}
      </section>

      {data?.attention?.length > 0 && (
        <section className="operations-attention" aria-label="需要关注">
          <div className="operations-section-heading">
            <span><ExclamationCircleOutlined /> 需要关注</span>
            <Tag color="warning">{data.attention.length} 项</Tag>
          </div>
          <div className="operations-attention-list">
            {data.attention.map((item, index) => (
              <button
                type="button"
                className="operations-attention-item"
                key={`${item.user_id}-${item.type}-${index}`}
                onClick={() => setSelectedUserId(item.user_id)}
              >
                <strong>{item.real_name} · {item.label}</strong>
                <span>{item.detail}</span>
              </button>
            ))}
          </div>
        </section>
      )}

      <div className="operations-workspace">
        <section className="operations-table-panel">
          <div className="operations-section-heading">
            <span><TeamOutlined /> 人员今日执行</span>
            <Text type="secondary">共 {data?.people?.length || 0} 人</Text>
          </div>
          {data?.people?.length ? (
            <Table
              rowKey="user_id"
              size="small"
              columns={columns}
              dataSource={data.people}
              pagination={false}
              rowClassName={(person) => person.user_id === selectedUserId ? 'operations-row-selected' : ''}
              onRow={(person) => ({
                onClick: () => setSelectedUserId(person.user_id),
                'aria-selected': person.user_id === selectedUserId,
              })}
            />
          ) : (
            <Empty description="暂无可展示的运维人员" />
          )}
        </section>

        <aside className="operations-detail-panel">
          <div className="operations-section-heading">
            <span><FileDoneOutlined /> 人员详情</span>
          </div>
          {selectedPerson ? (
            <div className="operations-detail-body">
              <div className="operations-detail-title">
                <div><Text strong>{selectedPerson.real_name}</Text><Tag color={STATUS_COLOR[selectedPerson.status_code]}>{selectedPerson.status_label}</Tag></div>
                <Text type="secondary">首站打卡 {timeOnly(selectedPerson.first_checkin_at)}</Text>
              </div>
              <dl className="operations-detail-facts">
                <div><dt>全天巡检</dt><dd>{selectedPerson.completed_items}/{selectedPerson.today_items}</dd></div>
                <div><dt>最近到站巡检</dt><dd>{selectedPerson.current_site_completed}/{selectedPerson.current_site_items}</dd></div>
                <div><dt>开放工单</dt><dd>{selectedPerson.open_workorders} 件</dd></div>
                <div><dt>异常检查项</dt><dd>{selectedPerson.abnormal_items} 项</dd></div>
              </dl>
              {selectedPerson.timeline?.length ? (
                <Timeline
                  className="operations-timeline"
                  items={selectedPerson.timeline.map((event) => ({
                    color: event.event_type.includes('checkin') ? 'green' : 'blue',
                    children: (
                      <div>
                        <Text strong>{timeOnly(event.occurred_at)} {event.event_label}</Text>
                        <div className="operations-secondary">{event.site_name}{event.detail ? ` · ${event.detail}` : ''}</div>
                      </div>
                    ),
                  }))}
                />
              ) : (
                <div className="operations-empty-track">
                  <ClockCircleOutlined />
                  <div>
                    <Text>今日尚无业务轨迹</Text>
                    <div className="operations-secondary">到站、巡检提交或工单处理后将在这里显示</div>
                  </div>
                </div>
              )}
              <div className="operations-detail-actions">
                <Button icon={<EnvironmentOutlined />} onClick={() => navigate('/plan-schedules')}>查看巡检计划</Button>
                <Button icon={<ToolOutlined />} onClick={() => navigate(`/workorders?assignee=${encodeURIComponent(selectedPerson.real_name)}`)}>查看相关工单</Button>
              </div>
            </div>
          ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择人员查看详情" />}
        </aside>
      </div>
    </div>
  );
}
