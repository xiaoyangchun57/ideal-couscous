// Design Tokens for the hydrological monitoring platform
// Dual theme: dark (default) and light

export const darkTokens = {
  // Brand
  colorPrimary: '#00c9a7',
  colorPrimaryHover: '#00e0c0',
  colorPrimaryBg: 'rgba(0,201,167,0.08)',

  // Backgrounds
  colorBgBase: '#0a1628',
  colorBgContainer: 'rgba(12,28,52,0.85)',
  colorBgElevated: '#0e1f38',
  colorBgLayout: '#0a1628',

  // Text
  colorText: '#d0e8ff',
  colorTextSecondary: '#a8cee0',
  colorTextTertiary: '#8db4c8',
  colorTextQuaternary: '#789db1',

  // Border
  colorBorder: 'rgba(0,200,180,0.15)',
  colorBorderSecondary: 'rgba(0,200,180,0.08)',

  // Status
  colorSuccess: '#10b981',
  colorWarning: '#fbbf24',
  colorWarningBg: 'rgba(251,191,36,0.12)',
  colorWarningBgHover: 'rgba(251,191,36,0.18)',
  colorWarningBorder: 'rgba(251,191,36,0.30)',
  colorError: '#ef4444',
  colorInfo: '#38bdf8',

  // Component
  borderRadius: 8,
  borderRadiusLG: 12,
  borderRadiusSM: 4,

  // Custom tokens (not part of Ant Design)
  navBg: 'rgba(8,20,42,0.98)',
  panelBg: 'rgba(12,28,52,0.68)',
  panelGlass: true,
  glowAccent: 'rgba(0,200,180,0.15)',
  shadowCard: '0 2px 12px rgba(0,0,0,0.35)',
  shadowNav: '0 2px 8px rgba(0,0,0,0.5)',
  summaryBorder: 'rgba(0,200,180,0.15)',
  summaryDivider: 'rgba(0,200,180,0.15)',
  summaryShadow: 'none',
};

export const lightTokens = {
  colorPrimary: '#0f766e',
  colorPrimaryHover: '#115e59',
  colorPrimaryBg: 'rgba(15,118,110,0.07)',

  colorBgBase: '#f5f7fa',
  colorBgContainer: '#ffffff',
  colorBgElevated: '#ffffff',
  colorBgLayout: '#f5f7fa',

  colorText: '#0f172a',
  colorTextSecondary: '#475569',
  colorTextTertiary: '#64748b',
  colorTextQuaternary: '#64748b',

  colorBorder: '#e2e8f0',
  colorBorderSecondary: '#f1f5f9',

  colorSuccess: '#059669',
  colorWarning: '#d97706',
  colorWarningBg: '#fff7e6',
  colorWarningBgHover: '#ffedd5',
  colorWarningBorder: '#f6c453',
  colorError: '#dc2626',
  colorInfo: '#0284c7',

  borderRadius: 8,
  borderRadiusLG: 12,
  borderRadiusSM: 4,

  navBg: '#ffffff',
  panelBg: 'rgba(255,255,255,0.92)',
  panelGlass: true,
  glowAccent: 'rgba(15,118,110,0.08)',
  shadowCard: '0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04)',
  shadowNav: '0 1px 3px rgba(0,0,0,0.08)',
  summaryBorder: '#cbd5e1',
  summaryDivider: '#d7dee8',
  summaryShadow: '0 1px 2px rgba(15,23,42,0.05)',
};

// Semantic color helpers for status indicators (theme-agnostic)
export const statusColors = {
  danger: { dark: '#ef4444', light: '#dc2626' },
  warning: { dark: '#fbbf24', light: '#d97706' },
  success: { dark: '#10b981', light: '#059669' },
  info: { dark: '#38bdf8', light: '#0284c7' },
  accent: { dark: '#00c9a7', light: '#0f766e' },
  purple: { dark: '#a855f7', light: '#7c3aed' },
};

// Chart color palette
export const chartPalette = [
  '#00c9a7', // teal
  '#38bdf8', // sky
  '#f59e0b', // amber
  '#ef4444', // red
  '#a855f7', // purple
  '#ec4899', // pink
];

// Station type colors (for map markers and legends)
export const stationTypeColors = {
  rainfall: '#ef4444',
  water_level: '#ef4444',
  hydrology: '#ef4444',
  soil_moisture: '#f59e0b',
  evaporation: '#38bdf8',
  groundwater: '#06b6d4',
  station_yard: '#38bdf8',
};
