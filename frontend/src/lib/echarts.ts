import * as echarts from "echarts/core";
import { CandlestickChart, LineChart, BarChart, HeatmapChart, ScatterChart, PieChart, TreemapChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkPointComponent,
  ToolboxComponent,
  MarkLineComponent,
  MarkAreaComponent,
  VisualMapComponent,
  TitleComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  CandlestickChart, LineChart, BarChart, HeatmapChart, ScatterChart, PieChart, TreemapChart,
  GridComponent, TooltipComponent, LegendComponent,
  DataZoomComponent, MarkPointComponent,
  ToolboxComponent, MarkLineComponent, MarkAreaComponent,
  VisualMapComponent, TitleComponent,
  CanvasRenderer,
]);

export const CHART_GROUP = "quant-charts";

let _connected = false;

export function connectCharts() {
  if (!_connected) {
    echarts.connect(CHART_GROUP);
    _connected = true;
  }
}

export { echarts };
