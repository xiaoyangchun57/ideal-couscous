import { Card, Table, Tag } from 'antd';

export const waterQualityParams = [
  { key: 'ph', label: 'pH', unit: '', color: '#1677ff', range: [6.0, 9.0] },
  { key: 'ammonia', label: '氨氮', unit: 'mg/L', color: '#52c41a', range: [0, 2.0] },
  { key: 'dissolved_oxygen', label: '溶解氧', unit: 'mg/L', color: '#13c2c2', range: [5.0, 10.0] },
  { key: 'turbidity', label: '浊度', unit: 'NTU', color: '#722ed1', range: [0, 10] },
  { key: 'codmn', label: '高锰酸盐指数', unit: 'mg/L', color: '#eb2f96', range: [2.0, 8.0] },
  { key: 'total_phosphorus', label: '总磷', unit: 'mg/L', color: '#fa8c16', range: [0, 0.2] },
  { key: 'total_nitrogen', label: '总氮', unit: 'mg/L', color: '#f5222d', range: [0.5, 2.0] },
  { key: 'water_temp', label: '水温', unit: '°C', color: '#faad14', range: [15, 30] },
];

export function generateArchiveTrend(code, parameterKey = 'ph') {
  const seed = (code || 'DEFAULT').split('').reduce((sum, char) => sum + char.charCodeAt(0), 0);
  const parameter = waterQualityParams.find((item) => item.key === parameterKey) || waterQualityParams[0];
  const [min, max] = parameter.range;
  const middle = (min + max) / 2;
  const amplitude = (max - min) / 3;

  return Array.from({ length: 24 }, (_, index) => {
    const base = middle + amplitude * Math.sin((seed + index * 15) * Math.PI / 180);
    const noise = amplitude * 0.3 * Math.sin((seed * 3 + index * 37) * Math.PI / 180);
    return {
      hour: `${String(index).padStart(2, '0')}:00`,
      value: Math.round((base + noise) * 100) / 100,
    };
  });
}

function TrendChart({ data, color, textColor, gridColor }) {
  const width = 640;
  const height = 180;
  const padding = { left: 40, right: 16, top: 16, bottom: 28 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const values = data.map((item) => item.value);
  const min = Math.floor(Math.min(...values) - 2);
  const max = Math.ceil(Math.max(...values) + 2);
  const range = max - min || 1;
  const toX = (index) => padding.left + index * chartWidth / (data.length - 1);
  const toY = (value) => padding.top + chartHeight - ((value - min) / range) * chartHeight;
  const path = data.map((item, index) => `${index === 0 ? 'M' : 'L'}${toX(index).toFixed(1)},${toY(item.value).toFixed(1)}`).join(' ');
  const area = `${path} L${toX(data.length - 1).toFixed(1)},${(padding.top + chartHeight).toFixed(1)} L${toX(0).toFixed(1)},${(padding.top + chartHeight).toFixed(1)} Z`;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} style={{ width: '100%', height: 'auto' }}>
      {[0, 0.25, 0.5, 0.75, 1].map((fraction) => {
        const y = padding.top + chartHeight * fraction;
        return (
          <g key={fraction}>
            <line x1={padding.left} y1={y} x2={width - padding.right} y2={y} stroke={gridColor} strokeWidth={0.5} />
            <text x={padding.left - 4} y={y + 3} textAnchor="end" fontSize={9} fill={textColor}>{(max - range * fraction).toFixed(1)}</text>
          </g>
        );
      })}
      <defs>
        <linearGradient id="archiveTrendFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.15} />
          <stop offset="100%" stopColor={color} stopOpacity={0.01} />
        </linearGradient>
      </defs>
      <path d={area} fill="url(#archiveTrendFill)" />
      <path d={path} fill="none" stroke={color} strokeWidth={1.8} strokeLinejoin="round" />
      {data.map((item, index) => <circle key={item.hour} cx={toX(index)} cy={toY(item.value)} r={2.2} fill="#fff" stroke={color} strokeWidth={1.2} />)}
      {data.filter((_, index) => index % 4 === 0).map((item, index) => {
        const pointIndex = index * 4;
        return <text key={item.hour} x={toX(pointIndex)} y={height - 6} textAnchor="middle" fontSize={9} fill={textColor}>{item.hour}</text>;
      })}
    </svg>
  );
}

export default function ArchiveTrendPanel({ code, selectedKey, onSelectedKeyChange, tokens, thresholds, classifyMetric, tagStyle }) {
  const parameter = waterQualityParams.find((item) => item.key === selectedKey) || waterQualityParams[0];
  const data = generateArchiveTrend(code, parameter.key);

  return (
    <Card size="small" title="近24小时水质趋势" style={{ marginTop: 16 }}>
      <div style={{ marginBottom: 12, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {waterQualityParams.map((item) => (
          <Tag.CheckableTag key={item.key} checked={parameter.key === item.key} onChange={() => onSelectedKeyChange(item.key)} style={{ fontSize: 12 }}>
            {item.label}{item.unit ? ` (${item.unit})` : ''}
          </Tag.CheckableTag>
        ))}
      </div>
      <TrendChart data={data} color={parameter.color || tokens.colorPrimary} textColor={tokens.colorTextTertiary} gridColor={tokens.colorBorderSecondary} />
      <Table
        size="small"
        rowKey="hour"
        pagination={false}
        dataSource={data}
        scroll={{ y: 220 }}
        columns={[
          { title: '时间', dataIndex: 'hour', width: 88 },
          { title: '采集值', dataIndex: 'value', width: 100, render: (value) => <strong style={{ color: parameter.color || tokens.colorPrimary }}>{value}</strong> },
          {
            title: '状态', width: 80,
            render: (_, row) => {
              const result = classifyMetric(selectedKey, row.value, thresholds);
              const color = result.status === 'normal' ? 'success' : result.status === 'warning' ? 'warning' : result.status === 'critical' ? 'error' : 'default';
              return <Tag color={color} style={tagStyle}>{result.label}</Tag>;
            },
          },
        ]}
      />
    </Card>
  );
}
