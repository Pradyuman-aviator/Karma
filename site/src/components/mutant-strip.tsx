"use client";

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { Mutant } from "@/lib/data";
import { percent } from "@/lib/utils";

/** One bar per broken function, sorted: the share of the suite Karma ran for it. */
export function MutantStrip({ mutants }: { mutants: Mutant[] }) {
  const sorted = [...mutants].sort((a, b) => a.selected / a.total - b.selected / b.total);
  return (
    <TooltipProvider delayDuration={60}>
      <div className="mt-3">
        <div className="flex h-24 items-end gap-[3px] border-b">
          {sorted.map((m) => {
            const share = m.selected / m.total;
            return (
              <Tooltip key={m.mutant}>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    aria-label={`${m.mutant}: ${m.selected} of ${m.total} tests run`}
                    className="group relative h-full min-w-0 flex-1"
                  >
                    <span
                      className={`absolute inset-x-0 bottom-0 rounded-t-[2px] transition-colors ${
                        m.missed ? "bg-fail" : "bg-amber/70 group-hover:bg-amber"
                      }`}
                      style={{ height: `${Math.max(share * 100, 1.5)}%` }}
                    />
                  </button>
                </TooltipTrigger>
                <TooltipContent>
                  <p className="font-mono text-[11px]">{m.mutant}</p>
                  <p className="mt-1 text-muted-foreground">
                    {m.failing} failing · {m.selected.toLocaleString()} of{" "}
                    {m.total.toLocaleString()} tests run ({percent(share)}) ·{" "}
                    {m.missed ? "missed a failure" : "nothing missed"}
                  </p>
                </TooltipContent>
              </Tooltip>
            );
          })}
        </div>
        <div className="mt-1.5 flex justify-between text-[11px] text-muted-foreground">
          <span>fewest tests run</span>
          <span>most</span>
        </div>
      </div>
    </TooltipProvider>
  );
}
