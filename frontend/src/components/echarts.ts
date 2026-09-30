import { BarChart, LineChart } from "echarts/charts";
import {
  AriaComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TooltipComponent,
} from "echarts/components";
import { use } from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import VChart from "vue-echarts";

use([
  SVGRenderer, LineChart, BarChart, GridComponent, TooltipComponent, LegendComponent,
  MarkAreaComponent, MarkLineComponent, DataZoomComponent, AriaComponent,
]);

export default VChart;
