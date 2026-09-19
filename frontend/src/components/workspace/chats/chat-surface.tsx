import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** Shared layout, not a second conversation implementation.
 * Ordinary chat and domain conversations retain the same widths, welcome
 * transition, sticky composer, header and message scrolling boundaries.
 */
export function ChatSurface({
  header,
  messages,
  composer,
  isWelcomeMode,
  headerClassName,
}: {
  header: ReactNode;
  messages: ReactNode;
  composer: ReactNode;
  isWelcomeMode: boolean;
  /** Lets a domain surface restyle the header bar (e.g. a reader without the
   * resting shadow); layout stays shared. */
  headerClassName?: string;
}) {
  return (
    <div className="relative flex size-full min-h-0 justify-between">
      <header
        className={cn(
          "absolute top-0 right-0 left-0 flex h-12 shrink-0 items-center gap-2 px-2 sm:px-4",
          isWelcomeMode
            ? "bg-background/0 z-40 backdrop-blur-none"
            : "bg-background/80 z-30 shadow-xs backdrop-blur",
          headerClassName,
        )}
      >
        {header}
      </header>
      <main className="flex min-h-0 max-w-full grow flex-col">
        <div className="flex min-h-0 flex-1 justify-center">{messages}</div>
        <div
          className={cn(
            "right-0 bottom-0 left-0 z-30 flex justify-center px-3 sm:px-4",
            isWelcomeMode ? "absolute" : "relative shrink-0 pb-4",
          )}
        >
          <div
            className={cn(
              "relative w-full",
              isWelcomeMode &&
                "-translate-y-[calc(50vh-48px)] sm:-translate-y-[calc(50vh-96px)]",
              isWelcomeMode
                ? "max-w-(--container-width-sm)"
                : "max-w-(--container-width-md)",
            )}
          >
            {composer}
          </div>
        </div>
      </main>
    </div>
  );
}
