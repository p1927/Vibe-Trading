declare module "react-plotly.js/factory" {
  import type { ComponentType } from "react";
  import type Plotly from "plotly.js";

  function createPlotlyComponent(plotly: typeof Plotly): ComponentType<Record<string, unknown>>;
  export default createPlotlyComponent;
}
