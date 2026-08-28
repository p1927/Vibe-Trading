import { Suspense, lazy, type ComponentType } from "react";
import { createBrowserRouter } from "react-router-dom";
import { Layout } from "@/components/layout/Layout";
import { RouteErrorFallback } from "@/components/common/RouteErrorFallback";
import { Agent } from "@/pages/Agent";

const Home = lazy(() => import("@/pages/Home").then((m) => ({ default: m.Home })));
const RunDetail = lazy(() =>
  import("@/pages/RunDetail").then((m) => ({ default: m.RunDetail })),
);
const Compare = lazy(() =>
  import("@/pages/Compare").then((m) => ({ default: m.Compare })),
);
const Settings = lazy(() =>
  import("@/pages/Settings").then((m) => ({ default: m.Settings })),
);
const Runtime = lazy(() =>
  import("@/pages/Runtime").then((m) => ({ default: m.Runtime })),
);
const Autonomous = lazy(() =>
  import("@/pages/Autonomous").then((m) => ({ default: m.Autonomous })),
);
const AgentBoard = lazy(() =>
  import("@/pages/AgentBoard").then((m) => ({ default: m.AgentBoard })),
);
const AdvisoryBoard = lazy(() =>
  import("@/pages/AdvisoryBoard").then((m) => ({ default: m.AdvisoryBoard })),
);
const PositionsBoard = lazy(() =>
  import("@/pages/PositionsBoard").then((m) => ({ default: m.PositionsBoard })),
);
const Reports = lazy(() =>
  import("@/pages/Reports").then((m) => ({ default: m.Reports })),
);
const Correlation = lazy(() =>
  import("@/pages/Correlation").then((m) => ({ default: m.Correlation })),
);
const Prediction = lazy(() =>
  import("@/pages/Prediction").then((m) => ({ default: m.Prediction })),
);
const Hub = lazy(() => import("@/pages/Hub").then((m) => ({ default: m.Hub })));
const Simulator = lazy(() =>
  import("@/pages/Simulator").then((m) => ({ default: m.Simulator })),
);
const Scheduled = lazy(() =>
  import("@/pages/Scheduled").then((m) => ({ default: m.Scheduled })),
);
const AlphaZoo = lazy(() =>
  import("@/pages/AlphaZoo").then((m) => ({ default: m.AlphaZoo })),
);
const OptionsLab = lazy(() =>
  import("@/pages/OptionsLab").then((m) => ({ default: m.OptionsLab })),
);
const ModelAdapters = lazy(() =>
  import("@/pages/ModelAdapters").then((m) => ({ default: m.ModelAdapters })),
);
const ExecutionAdvisor = lazy(() =>
  import("@/pages/ExecutionAdvisor").then((m) => ({ default: m.ExecutionAdvisor })),
);
const KnowledgeEngine = lazy(() =>
  import("@/pages/KnowledgeEngine").then((m) => ({ default: m.KnowledgeEngine })),
);
const CommandCenter = lazy(() =>
  import("@/pages/CommandCenter").then((m) => ({ default: m.CommandCenter })),
);

function PageLoader() {
  return (
    <div className="flex h-[60vh] items-center justify-center text-muted-foreground">
      Loading…
    </div>
  );
}

function wrap(Component: ComponentType) {
  return (
    <Suspense fallback={<PageLoader />}>
      <Component />
    </Suspense>
  );
}

export const router = createBrowserRouter([
  // Standalone — deliberately outside <Layout>'s sidebar/chrome. This is the
  // page the UI shell's "Dashboard" tab embeds directly; it should use the
  // full iframe, not share space with Vibe's nav rail.
  {
    path: "/command-center",
    errorElement: <RouteErrorFallback />,
    element: wrap(CommandCenter),
  },
  {
    element: <Layout />,
    errorElement: <RouteErrorFallback />,
    children: [
      { path: "/", element: wrap(Home) },
      { path: "/about", element: wrap(Home) },
      { path: "/agent", element: <Agent /> },
      { path: "/autonomous", element: wrap(Autonomous) },
      { path: "/agent-board", element: wrap(AgentBoard) },
      { path: "/advisory-board", element: wrap(AdvisoryBoard) },
      { path: "/positions-board", element: wrap(PositionsBoard) },
      { path: "/runtime", element: wrap(Runtime) },
      { path: "/scheduled", element: wrap(Scheduled) },
      { path: "/reports", element: wrap(Reports) },
      { path: "/settings", element: wrap(Settings) },
      { path: "/model-adapters", element: wrap(ModelAdapters) },
      { path: "/runs/:runId", element: wrap(RunDetail) },
      { path: "/compare", element: wrap(Compare) },
      { path: "/correlation", element: wrap(Correlation) },
      { path: "/prediction", element: wrap(Prediction) },
      { path: "/hub", element: wrap(Hub) },
      { path: "/simulator", element: wrap(Simulator) },
      { path: "/options", element: wrap(OptionsLab) },
      { path: "/execution-advisor", element: wrap(ExecutionAdvisor) },
      { path: "/knowledge", element: wrap(KnowledgeEngine) },
      { path: "/alpha-zoo", element: wrap(AlphaZoo) },
      { path: "/alpha-zoo/bench", element: wrap(AlphaZoo) },
      { path: "/alpha-zoo/compare", element: wrap(AlphaZoo) },
      { path: "/alpha-zoo/:alphaId", element: wrap(AlphaZoo) },
    ],
  },
]);
