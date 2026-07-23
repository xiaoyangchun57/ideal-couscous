/**
 * 全系统页面样式统一标准
 * 以告警管理中心（AlertsPage）为基准
 */
import { theme } from 'antd';

// ---- 卡片样式 ----
export const cardStyleBase = (tokens, isDark) => ({
  borderRadius: 12,
  background: isDark
    ? 'linear-gradient(135deg, rgba(12,28,52,0.85), rgba(8,20,42,0.9))'
    : '#ffffff',
  border: `1px solid ${tokens.colorBorder}`,
  boxShadow: isDark
    ? '0 2px 12px rgba(0,0,0,0.3)'
    : '0 2px 8px rgba(0,0,0,0.06)',
});

// ---- 页面根容器 ----
export const pageRootStyle = {
  flex: 1,
  minHeight: 0,
  display: 'flex',
  flexDirection: 'column',
  padding: 24,
};

// ---- 统计卡片 ----
export const statCardStyle = (tokens, isDark) => ({
  ...cardStyleBase(tokens, isDark),
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
export const filterCardBody = { padding: '16px 24px' };
export const filterInputWidth = 280;
export const filterSelectWidth = 140;
export const filterSmallSelectWidth = 120;

// ---- 表格容器 ----
export const tableCardStyle = (tokens, isDark) => ({
  ...cardStyleBase(tokens, isDark),
  marginTop: 16,
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
