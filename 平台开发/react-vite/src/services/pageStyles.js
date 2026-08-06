/**
 * 全系统页面样式统一标准（唯一面板规范）
 * 以告警管理中心（AlertsPage）为基准。
 *
 * 约束（结构一致性规范，见 docs/superpowers/ui-specs/web-antd-ui-spec.md §8）：
 * 1. 基准页本身须先符合本规范——pageStyles 是全部管理页的唯一面板标准，
 *    任何页不得自建一套 padding / 工具栏 / 状态色；
 * 2. 新管理页必须套 <ManagementPage> 模板组件（components/ManagementPage.jsx），
 *    不得新建「裸 Table + 自建工具栏」页；
 * 3. 状态色一律走 services/constants.js 的 *Map + theme/tokens.js 的 statusColors，
 *    业务代码禁止硬编码 hex。
 */
// ---- 卡片样式 ----
export const cardStyleBase = (tokens) => ({
  borderRadius: 8,
  background: tokens.colorBgContainer,
  border: `1px solid ${tokens.colorBorder}`,
  boxShadow: tokens.shadowCard,
});

// ---- 页面根容器 ----
export const pageRootStyle = {
  flex: 1,
  minHeight: 0,
  display: 'flex',
  flexDirection: 'column',
  padding: 20,
};

// ---- 统计卡片 ----
export const statCardStyle = (tokens) => ({
  ...cardStyleBase(tokens),
  transition: 'all 0.2s ease',
});

export const statCardBody = { padding: '12px 16px' };

export const statValueStyle = (color) => ({
  color,
  fontWeight: 600,
  fontSize: 22,
});

export const statTitleStyle = (tokens) => ({
  color: tokens.colorTextSecondary,
  fontSize: 12,
});

// ---- 筛选工具栏 ----
export const filterCardBody = { padding: '10px 16px' };
export const filterInputWidth = 260;
export const filterSelectWidth = 140;
export const filterSmallSelectWidth = 120;

// ---- 表格容器 ----
export const tableCardStyle = (tokens) => ({
  ...cardStyleBase(tokens),
  marginTop: 12,
  flex: 1,
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
  minHeight: 0,
});

export const tableCardBody = {
  padding: 0,
  flex: 1,
  display: 'flex',
  flexDirection: 'column',
  minHeight: 0,
};

// ---- Tag 标签 ----
export const tagStyle = {
  borderRadius: 4,
  fontSize: 11,
};

export const tagBoldStyle = {
  borderRadius: 4,
  fontSize: 11,
  fontWeight: 600,
};

// ---- 按钮 ----
export const refreshBtnStyle = { borderRadius: 8 };

// ---- 文本 ----
export const secondaryText = (tokens) => ({
  color: tokens.colorTextSecondary,
  fontSize: 13,
});

export const filterResultText = {
  fontSize: 12,
};

// ---- 统计栏 Row ----
export const statRowStyle = {
  marginBottom: 16,
  flexShrink: 0,
};
