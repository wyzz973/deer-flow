"use client";

import { ChevronRight } from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/utils";

const LONG_TEXT = 280;

function Text({ value }: { value: string }) {
  const [open, setOpen] = useState(false);
  const long = value.length > LONG_TEXT || value.includes("\n");
  if (!long)
    return (
      <span className="break-words text-emerald-700 dark:text-emerald-400">
        &quot;{value}&quot;
      </span>
    );
  return (
    <span className="block">
      <span className="text-muted-foreground text-[11px]">
        文本 · {value.length} 字符
      </span>
      <button
        type="button"
        className="text-primary ml-2 text-[11px] hover:underline"
        onClick={() => setOpen(!open)}
      >
        {open ? "收起" : "展开"}
      </button>
      <span
        className={cn(
          "bg-muted/40 mt-1 block rounded-md p-2 text-[12px] leading-5 break-words whitespace-pre-wrap",
          !open && "line-clamp-4",
        )}
      >
        {value}
      </span>
    </span>
  );
}

/** Collapsible JSON with readable long strings, for prompts and tool schemas. */
export function JsonView({
  value,
  name,
  depth = 0,
  defaultOpen = 2,
}: {
  value: unknown;
  name?: string;
  depth?: number;
  defaultOpen?: number;
}) {
  const [open, setOpen] = useState(depth < defaultOpen);
  const label = name !== undefined && (
    <span className="text-sky-700 dark:text-sky-400">{name}: </span>
  );
  if (value === null || typeof value !== "object") {
    return (
      <div className="py-0.5 font-mono text-[12px] leading-5">
        {label}
        {typeof value === "string" ? (
          <Text value={value} />
        ) : (
          <span className="text-amber-700 dark:text-amber-400">
            {JSON.stringify(value)}
          </span>
        )}
      </div>
    );
  }
  const entries = Array.isArray(value)
    ? value.map((item, index) => [String(index), item] as const)
    : Object.entries(value as Record<string, unknown>);
  const summary = Array.isArray(value)
    ? `[${entries.length}]`
    : `{${entries.length}}`;
  return (
    <div className="font-mono text-[12px] leading-5">
      <button
        type="button"
        className="hover:bg-muted/50 -ml-1 inline-flex items-center gap-0.5 rounded px-1"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <ChevronRight
          className={cn(
            "size-3 transition-transform motion-reduce:transition-none",
            open && "rotate-90",
          )}
        />
        {label}
        <span className="text-muted-foreground">{summary}</span>
      </button>
      {open && (
        <div className="border-border/50 ml-2 border-l pl-3">
          {entries.map(([key, item]) => (
            <JsonView
              key={key}
              name={key}
              value={item}
              depth={depth + 1}
              defaultOpen={defaultOpen}
            />
          ))}
        </div>
      )}
    </div>
  );
}
