import ReactEChartsCoreModule from 'echarts-for-react/lib/core';
import { BarChart, LineChart, PieChart } from 'echarts/charts';
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from 'echarts/components';
import * as echarts from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';

const ReactEChartsCore = ReactEChartsCoreModule.default || ReactEChartsCoreModule;

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
]);

export const { graphic } = echarts;

export default function EChart({ opts, ...props }) {
  return <ReactEChartsCore echarts={echarts} opts={{ renderer: 'canvas', ...opts }} {...props} />;
}
