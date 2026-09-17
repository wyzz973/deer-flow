"use client";

import { useQuery } from "@tanstack/react-query";
import { FileText, Loader2 } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useMemo } from "react";

import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";
import { researchApi } from "@/core/deepresearch/api";
import { firstText } from "@/core/deepresearch/presentation";
import { terminal } from "@/core/deepresearch/types";

/** Research history occupies the existing sidebar history slot, never a
 * second sidebar inside the conversation page. */
export function ResearchHistory() {
  const { setOpenMobile } = useSidebar();
  const pathname = usePathname();
  const api = useMemo(() => researchApi(), []);
  const history = useQuery({
    queryKey: ["research-history", api.root],
    queryFn: api.list,
    retry: false,
  });
  return (
    <SidebarGroup>
      <SidebarGroupLabel>研究记录</SidebarGroupLabel>
      <SidebarMenu>
        {history.data?.map((run) => (
          <SidebarMenuItem key={run.run_id}>
            <SidebarMenuButton asChild isActive={pathname.endsWith(run.run_id)}>
              <Link
                href={`/workspace/deepresearch/${encodeURIComponent(run.run_id)}`}
                title={run.query}
                onClick={() => setOpenMobile(false)}
              >
                {!terminal.has(run.status) &&
                ![
                  "AWAITING_PLAN_CONFIRMATION",
                  "EDITING_PLAN",
                  "AWAITING_CLARIFICATION",
                ].includes(run.status) ? (
                  <Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
                ) : (
                  <FileText className="size-4" />
                )}
                <span className="truncate">
                  {firstText(run.plan?.title, run.query)}
                </span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        ))}
      </SidebarMenu>
      {!history.data?.length && (
        <p className="text-muted-foreground px-2 py-3 text-xs">
          研究会话会保存在这里
        </p>
      )}
    </SidebarGroup>
  );
}
