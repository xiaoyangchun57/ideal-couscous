import { Button, Card, Empty, Space, Table, Tooltip, Typography } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { useTheme } from '../hooks/useTheme';
import { useTableAutoHeight } from '../hooks/useTableAutoHeight';
import { visiblePageRowCount } from '../utils/listPagination';
import { useClientPagination } from '../utils/clientPagination';
import './WorkspacePage.css';

const { Title, Text } = Typography;

// 空状态动态图标映射（outline 风格，常动）
const emptyIconMap = {
  empty: 'inbox',
  filtered: 'magnifier',
  sample: 'bar-chart',
  error: 'warning-triangle',
  'no-permission': 'arrow-out-square',
  success: 'confetti',
};

const EmptyGif = ({ type, size = 72 }) => {
  const name = emptyIconMap[type] || 'inbox';
  return (
    <img
      src={`/icons/empty/${name}.gif`}
      alt=""
      style={{ width: size, height: size, objectFit: 'contain' }}
      draggable={false}
    />
  );
};

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

export function WorkspaceEmpty({ type = 'empty', onRefresh, description, children }) {
  const descriptions = {
    empty: '当前没有业务记录',
    filtered: '没有符合当前条件的记录',
    sample: '本周期尚无可用统计样本',
    error: '数据加载失败，请稍后重试',
    'no-permission': '当前账号没有可查看内容',
    success: '操作成功',
  };
  return (
    <div className="workspace-empty" style={{ minHeight: 240, display: 'grid', placeItems: 'center' }}>
      <Empty image={<EmptyGif type={type} />} description={description || descriptions[type]}>
        {(children || onRefresh) && <Space wrap>{children}{onRefresh && <Button icon={<ReloadOutlined />} onClick={onRefresh}>刷新</Button>}</Space>}
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

export function formatTablePaginationTotal(total, range) {
  const start = range?.[0] || 0;
  const end = range?.[1] || 0;
  return `共${total}条，当前第${start}—${end}条`;
}

export function standardListPagination(overrides = {}) {
  return {
    pageSize: 20,
    showSizeChanger: true,
    pageSizeOptions: [20, 50, 100],
    ...overrides,
  };
}

export function WorkspaceTable({ dataSource = [], columns, loading, rowKey, emptyType, onRefresh, pagination = false, scroll, fillHeight = false, ...rest }) {
  const { tokens } = useTheme();
  const hasRows = dataSource.length > 0;
  // 客户端分页自管状态：否则写死的 pageSize 会把用户选的 50/100 覆盖回 20。
  const { config: resolvedPagination, current: clientCurrent, pageSize: clientPageSize } = useClientPagination(pagination);
  const paginationConfig = resolvedPagination && typeof resolvedPagination === 'object'
    ? { showTotal: formatTablePaginationTotal, ...resolvedPagination }
    : resolvedPagination;
  const paginationTotal = paginationConfig && typeof paginationConfig === 'object'
    && Number.isFinite(Number(paginationConfig.total))
    ? Number(paginationConfig.total)
    : dataSource.length;
  const currentPage = Number(paginationConfig?.current || clientCurrent || 1);
  const pageSize = Number(paginationConfig?.pageSize || clientPageSize || dataSource.length || 1);
  const visibleRowCount = visiblePageRowCount(dataSource.length, currentPage, pageSize);
  const liveStart = visibleRowCount ? ((currentPage - 1) * pageSize) + 1 : 0;
  const liveEnd = visibleRowCount ? Math.min(liveStart + visibleRowCount - 1, paginationTotal) : 0;
  const hasPaginatedRecords = Boolean(paginationConfig) && paginationTotal > 0;
  // 声明了分页配置的列表统一把分页栏停靠在容器底部：表格主体撑满剩余高度，
  // 分页栏位置与当前页行数无关，也不会因数据少而随内容上浮。
  // fillHeight 保留为历史页面的显式开关（归档、站点目录）。
  const dockPagination = fillHeight || Boolean(paginationConfig && typeof paginationConfig === 'object');
  const [tableRef, bodyHeight] = useTableAutoHeight({
    deps: [dataSource.length, loading, currentPage, pageSize, paginationTotal],
  });
  const tableScroll = scroll
    ? {
        ...scroll,
        ...(!scroll.y && bodyHeight ? { y: bodyHeight } : {}),
        scrollToFirstRowOnChange: scroll.scrollToFirstRowOnChange ?? true,
      }
    : (bodyHeight ? { y: bodyHeight, scrollToFirstRowOnChange: true } : undefined);
  return (
    <Card ref={tableRef} className={`workspace-table${dockPagination ? ' workspace-table--fill' : ''}`} aria-busy={loading ? 'true' : 'false'} style={{ borderColor: tokens.colorBorder }} styles={{ body: { padding: 0 } }}>
      <span className="sr-only" role="status" aria-live="polite">
        {loading ? '正在加载列表' : `列表已更新，共${paginationTotal}条，当前第${liveStart}—${liveEnd}条`}
      </span>
      {hasRows || hasPaginatedRecords || loading ? (
        <Table
          dataSource={dataSource}
          columns={columns}
          loading={loading}
          rowKey={rowKey}
          size="small"
          pagination={paginationConfig}
          scroll={tableScroll}
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
