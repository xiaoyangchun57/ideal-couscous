import { Card, Table, Typography } from 'antd';
import { useTheme } from '../hooks/useTheme';
import { useTableAutoHeight } from '../hooks/useTableAutoHeight';
import { pageRootStyle, tableCardStyle, tableCardBody } from '../services/pageStyles';

/**
 * 管理页模板组件（结构一致性规范 #8，消除根因 J）
 *
 * 把面板规范「编译」成默认路径：新管理页必须套用本组件，
 * 不得新建「裸 Table + 自建工具栏」页（见 web-antd-ui-spec.md §8）。
 *
 * 内置统一默认：
 * - 外层 pageRootStyle（padding:24，flex 列）
 * - 列表包 Card + 统一 tableCardBody
 * - 配套 UnifiedTable：size="small" + pagination={false} + 自动高度滚动
 *
 * 用法：
 *   <ManagementPage
 *     pageTitle="设备管理"
 *     pageSub="维护设备台账"
 *     headerExtra={<Button type="primary">新增</Button>}
 *     statSlots={<Row gutter={16}>…统计卡…</Row>}
 *     filterSlot={<FilterBar>…筛选控件…</FilterBar>}
 *     tableSlot={<UnifiedTable rowKey="id" dataSource={data} columns={columns} />}
 *   />
 */
export default function ManagementPage({
  pageTitle, pageSub, headerExtra, statSlots, filterSlot, tableSlot, children, tableMode = 'content',
}) {
  const { tokens, isDark } = useTheme();
  return (
    <div style={pageRootStyle}>
      {(pageTitle || headerExtra) && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          marginBottom: 16, flexShrink: 0, gap: 12, flexWrap: 'wrap',
        }}>
          <div>
            {pageTitle && (
              <Typography.Title level={4} style={{ margin: 0 }}>{pageTitle}</Typography.Title>
            )}
            {pageSub && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>{pageSub}</Typography.Text>
            )}
          </div>
          {headerExtra && <div style={{ display: 'flex', gap: 8 }}>{headerExtra}</div>}
        </div>
      )}
      {statSlots && <div style={{ marginBottom: 16, flexShrink: 0 }}>{statSlots}</div>}
      {filterSlot && <div style={{ flexShrink: 0 }}>{filterSlot}</div>}
      {tableSlot && (
        <Card
          style={tableMode === 'fill'
            ? tableCardStyle(tokens, isDark)
            : { ...tableCardStyle(tokens, isDark), flex: 'none', overflow: 'visible' }}
          styles={{ body: tableMode === 'fill' ? tableCardBody : { padding: 0 } }}
        >
          {tableSlot}
        </Card>
      )}
      {children}
    </div>
  );
}

/**
 * 统一表格（管理页模板配套）
 *
 * 默认：size="small"、自动高度填满卡片（useTableAutoHeight）。
 * - mode="content"：内容自适应，适合少量记录或启用分页的台账，避免空白撑高
 * - mode="fill"：内滚动填满管理页，适合高密度监控列表（默认）
 * - scrollX 传数字即设 scroll.x
 * - heightDeps：依赖变化时重新测量高度（如筛选切换导致容器变化）
 */
export function UnifiedTable({
  mode = 'fill', heightDeps = [], scrollX, size = 'small', pagination = false, ...rest
}) {
  const [wrapRef, bodyHeight] = useTableAutoHeight({ deps: heightDeps });

  if (mode === 'content') {
    return (
      <Table
        size={size}
        pagination={pagination}
        scroll={scrollX ? { x: scrollX } : undefined}
        {...rest}
      />
    );
  }

  const scroll = bodyHeight
    ? { y: bodyHeight, ...(scrollX ? { x: scrollX } : {}) }
    : (scrollX ? { x: scrollX } : undefined);

  return (
    <div ref={wrapRef} style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
      <Table size={size} pagination={pagination} scroll={scroll} {...rest} />
    </div>
  );
}
