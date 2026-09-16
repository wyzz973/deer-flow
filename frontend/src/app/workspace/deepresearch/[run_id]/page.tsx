import { ResearchConversation } from "@/components/deepresearch/research-conversation";

export default async function DeepResearchRunPage({
  params,
}: {
  params: Promise<{ run_id: string }>;
}) {
  const { run_id } = await params;
  return <ResearchConversation key={run_id} initialRunId={run_id} />;
}
