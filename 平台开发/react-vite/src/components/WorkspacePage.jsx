import { Button, Card, Empty, Space, Table, Tooltip, Typography } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { useTheme } from '../hooks/useTheme';
import './WorkspacePage.css';

const { Title, Text } = Typography;

export function TableLongText({ value, lines = 2, empty = '-' }) {
  const content = value == null || value === '' ? empty : String(value);
  if (content === empty) return <Text type="secondary">{empty}</Text>;
  return (
    <Tooltip title={content}>
      <span style={{
        display: '-webkit-box', WebkitBoxOrient: 'vertical', WebkitLineClamp: lines,
        overflow: 'hidden', whiteSpace: 'normal', lineHeight: '20px',
      }}>
        {content}
      </span>
    </Tooltip>
  );
}

export function WorkspaceEmpty({ type = 'empty', onRefresh, description }) {
  const descriptions = {
    empty: '当前没有业务记录',
    filtered: '没有符合当前条件的记录',
    sample: '本周期尚无可用统计样本',
    error: '数据加载失败，请稍后重试',
  };
  return (
    <div className="workspace-empty" style={{ minHeight: 240, display: 'grid', placeItems: 'center' }}>
      <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={description || descriptions[type]}>
        {onRefresh && <Button icon={<ReloadOutlined />} onClick={onRefresh}>刷新</Button>}
      </Empty>
    </div>
  );
}

export function StatusStrip({ items = [] }) {
  const { tokens } = useTheme();
  const visible = items.filter((item) => item.always || Number(item.value) > 0);
  if (!visible.length) return null;
  return (
    <div className="workspace-status-strip" style={{
      '--workspace-summary-border': tokens.summaryBorder,
      '--workspace-summary-divider': tokens.summaryDivider,
      background: tokens.colorBgContainer,
      boxShadow: tokens.summaryShadow,
    }}>
      {visible.map((item) => (
        <div className="workspace-status-strip__item" key={item.key || item.label}>
          <Text type="secondary">{item.label}</Text>
          <strong style={{ color: item.color }}>{item.value}</strong>
        </div>
      ))}
    </div>
  );
}

export function WorkspaceToolbar({ children, actions, layout = 'inline', className = '', style }) {
  const toolbarClassName = [
    'workspace-toolbar',
    layout === 'stacked' ? 'workspace-toolbar--stacked' : '',
    className,
  ].filter(Boolean).join(' ');

  return (
    <div className={toolbarClassName} style={style}>
      <div className="workspace-toolbar__filters">{children}</div>
      {actions && <div className="workspace-toolbar__actions">{actions}</div>}
    </div>
  );
}

export function FilterField({ label, children }) {
  return (
    <label className="workspace-filter-field">
      <span className="sr-only">{label}</span>
      {children}
    </label>
  );
}

export function ToolbarMeta({ label, children }) {
  return (
    <div className="workspace-toolbar-meta">
      <span className="workspace-toolbar-meta__label">{label}</span>
      <span className="workspace-toolbar-meta__value">{children}</span>
    </div>
  );
}

export function WorkspaceTable({ dataSource = [], columns, loading, rowKey, emptyType, onRefresh, pagination = false, scroll, ...rest }) {
  const { tokens } = useTheme();
  const hasRows = dataSource.length > 0;
  return (
    <Card className="workspace-table" aria-busy={loading ? 'true' : 'false'} style={{ borderColor: tokens.colorBorder }} styles={{ body: { padding: 0 } }}>
      <span className="sr-only" role="status" aria-live="polite">
        {loading ? '正在加载列表' : `列表已更新，共 ${dataSource.length} 条记录`}
      </span>
      {hasRows || loading ? (
        <Table
          dataSource={dataSource}
          columns={columns}
          loading={loading}
          rowKey={rowKey}
          size="small"
          pagination={pagination}
          scroll={scroll || { y: 'calc(100vh - 350px)', scrollToFirstRowOnChange: true }}
          {...rest}
        />
      ) : <WorkspaceEmpty type={emptyType} onRefresh={onRefresh} />}
    </Card>
  );
}

export default function WorkspacePage({ title, subtitle, primaryAction, secondaryAction, statusItems, toolbar, children }) {
  return (
    <div className="workspace-page">
      <div className="workspace-page__header">
        <div>
          <Title level={4}>{title}</Title>
          {subtitle && <Text type="secondary">{subtitle}</Text>}
        </div>
        {(primaryAction || secondaryAction) && <Space>{secondaryAction}{primaryAction}</Space>}
      </div>
      <StatusStrip items={statusItems} />
      {toolbar}
      <div className="workspace-page__main">{children}</div>
    </div>
  );
}
