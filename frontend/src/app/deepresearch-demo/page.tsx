import { ResearchWorkbench } from "@/components/deepresearch/workbench";

// Independent of workspace authentication; backend explicitly accepts only loopback demo traffic.
export default function DeepResearchDemoPage() {
  return <ResearchWorkbench apiBase="http://127.0.0.1:8022" />;
}
