import { ResearchWorkbench } from "@/components/deepresearch/workbench";
import {
  WorkspaceBody,
  WorkspaceContainer,
  WorkspaceHeader,
} from "@/components/workspace/workspace-container";

export default function DeepResearchPage() {
  return (
    <WorkspaceContainer>
      <WorkspaceHeader />
      <WorkspaceBody>
        <ResearchWorkbench />
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}
