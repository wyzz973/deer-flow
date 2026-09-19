"use client";

import { Check, ChevronDown, Copy, ScrollText, Wand2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { writeTextToClipboard } from "@/core/clipboard";
import type { ResearchMessage } from "@/core/deepresearch/types";
import { cn } from "@/lib/utils";

/** The conversation rewritten into the research request, shown like ChatGPT's
 * collapsed research tool call: one line by default, the full request on demand. */
export function ResearchRequestCard({
  message,
  onInspect,
}: {
  message: ResearchMessage;
  onInspect?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const request = message.rewrite?.user_query ?? message.text;
  const questions = message.rewrite?.clarification_questions ?? [];
  return (
    <Collapsible
      open={open}
      onOpenChange={setOpen}
      className="w-full max-w-2xl"
      data-testid="research-request-card"
    >
      <CollapsibleTrigger asChild>
        <button
          type="button"
          // A footnote to the turn, not a card: the plan and the report lead.
          className="hover:text-foreground inline-flex items-center gap-1.5 text-[13px] leading-5 text-(--dr-text-tertiary) transition-colors motion-reduce:transition-none"
          aria-label={open ? "收起研究请求" : "查看改写后的研究请求"}
        >
          <Wand2 className="size-3" />
          <span>
            {message.rewrite?.revision ? "已更新研究请求" : "已改写研究请求"}
          </span>
          <ChevronDown
            className={cn(
              "size-3 transition-transform motion-reduce:transition-none",
              open && "rotate-180",
            )}
          />
        </button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="bg-card mt-2 rounded-[16px] border border-(--dr-line) p-3">
          <div className="mb-2 flex items-center gap-2">
            <span className="text-foreground text-xs font-medium">
              研究请求
            </span>
            <span className="text-muted-foreground text-xs">
              由对话改写，规划、研究和写作都以它为准
            </span>
            <div className="ml-auto flex items-center gap-1">
              {onInspect && (
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label="查看改写调用"
                  title="查看改写这一步的模型调用"
                  onClick={onInspect}
                >
                  <ScrollText className="size-3.5" />
                </Button>
              )}
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="复制研究请求"
                onClick={() => {
                  void writeTextToClipboard(request).then(() => {
                    setCopied(true);
                    setTimeout(() => setCopied(false), 1500);
                  });
                }}
              >
                {copied ? (
                  <Check className="size-3.5" />
                ) : (
                  <Copy className="size-3.5" />
                )}
              </Button>
            </div>
          </div>
          <p className="text-sm leading-6 break-words whitespace-pre-wrap">
            {request}
          </p>
          {questions.length > 0 && (
            <ul className="text-muted-foreground mt-2 list-disc space-y-1 pl-5 text-sm">
              {questions.map((question) => (
                <li key={question}>{question}</li>
              ))}
            </ul>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}
