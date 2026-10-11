"use client";

import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

export type TerminalLine = {
  kind: "command" | "info" | "file" | "item" | "chain" | "changed" | "result" | "blank";
  text: string;
};

const STYLE: Record<TerminalLine["kind"], string> = {
  command: "text-terminal-foreground",
  info: "text-[#7d9199]",
  file: "text-terminal-foreground font-medium",
  item: "text-[#f0a03a]",
  chain: "text-[#7d9199]",
  changed: "text-[#f0a03a]",
  result: "text-[#52c79b]",
  blank: "",
};

/** A terminal that plays back a real Karma session. The server renders every line (so
 * it reads fine without JavaScript); after hydration the lines are replayed one by one. */
export function Terminal({
  lines,
  title = "zsh — karma",
  className,
}: {
  lines: TerminalLine[];
  title?: string;
  className?: string;
}) {
  const [shown, setShown] = useState(lines.length);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    setShown(0);
    let count = 0;
    let timer: ReturnType<typeof setTimeout>;
    const next = () => {
      count += 1;
      setShown(count);
      if (count < lines.length) timer = setTimeout(next, count === 1 ? 650 : 95);
    };
    timer = setTimeout(next, 300);
    return () => clearTimeout(timer);
  }, [lines.length]);

  return (
    <div
      className={cn(
        "overflow-hidden rounded-xl border border-white/10 bg-terminal shadow-2xl shadow-black/30",
        className,
      )}
    >
      <div className="flex items-center gap-2 border-b border-white/10 px-4 py-3">
        <span className="size-3 rounded-full bg-[#f07a70]/80" />
        <span className="size-3 rounded-full bg-[#f0a03a]/80" />
        <span className="size-3 rounded-full bg-[#52c79b]/80" />
        <span className="ml-3 font-mono text-xs text-[#7d9199]">{title}</span>
      </div>
      <pre className="overflow-x-auto p-4 font-mono text-[12.5px] leading-6 sm:p-5 sm:text-[13px]">
        {lines.map((line, i) => (
          <div
            key={i}
            aria-hidden={i >= shown}
            className={cn(
              "whitespace-pre transition-opacity duration-200",
              STYLE[line.kind],
              i < shown ? "opacity-100" : "opacity-0",
            )}
          >
            {line.kind === "command" ? (
              <>
                <span className="text-[#52c79b]">$</span> {line.text}
                {shown === 0 ? <span className="ml-0.5 inline-block w-2 animate-pulse bg-terminal-foreground">&nbsp;</span> : null}
              </>
            ) : (
              line.text || " "
            )}
          </div>
        ))}
      </pre>
    </div>
  );
}
