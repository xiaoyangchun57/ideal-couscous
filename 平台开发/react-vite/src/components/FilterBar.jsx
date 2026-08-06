import './WorkspacePage.css';
import { WorkspaceToolbar } from './WorkspacePage';

/**
 * 统一筛选工具栏（结构一致性规范 #2 / B 类跳动源治理）
 *
 * 全站管理页复用 WorkspaceToolbar 的筛选区、动作区和换行规则。
 * - 输入框宽度用 pageStyles.filterInputWidth（280）
 * - 下拉框宽度用 pageStyles.filterSelectWidth（140）/ filterSmallSelectWidth（120）
 * - 右侧动作按钮通过 extra 传入（如「新增」）
 *
 * 用法：
 *   <FilterBar extra={<Button type="primary">新增</Button>}>
 *     <Input style={{ width: filterInputWidth }} placeholder="搜索…" allowClear />
 *     <Select style={{ width: filterSelectWidth }} … />
 *   </FilterBar>
 */
export default function FilterBar({ children, extra, style }) {
  return (
    <WorkspaceToolbar actions={extra} style={{ flexShrink: 0, ...style }}>
      {children}
    </WorkspaceToolbar>
  );
}
