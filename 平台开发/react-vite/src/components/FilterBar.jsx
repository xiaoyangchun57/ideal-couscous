import { Card } from 'antd';
import { filterCardBody } from '../services/pageStyles';

/**
 * 统一筛选工具栏（结构一致性规范 #2 / B 类跳动源治理）
 *
 * 全站管理页的筛选区唯一形态：Card 容器 + 左对齐 + 标准控件宽度。
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
    <Card
      style={{ flexShrink: 0, ...style }}
      styles={{
        body: {
          ...filterCardBody,
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          flexWrap: 'wrap',
        },
      }}
    >
      {children}
      {extra ? <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>{extra}</div> : null}
    </Card>
  );
}
