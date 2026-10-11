import * as React from "react";

import { cn } from "@/lib/utils";

export function Section({
  id,
  eyebrow,
  title,
  intro,
  className,
  children,
}: {
  id: string;
  eyebrow: string;
  title: React.ReactNode;
  intro?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className={cn("mx-auto w-full max-w-6xl px-4 py-20 sm:px-6 sm:py-24", className)}>
      <div className="max-w-2xl">
        <p className="font-mono text-xs font-medium uppercase tracking-[0.18em] text-amber">
          {eyebrow}
        </p>
        <h2 className="mt-3 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">
          {title}
        </h2>
        {intro ? (
          <p className="mt-4 text-pretty text-base leading-relaxed text-muted-foreground sm:text-lg">
            {intro}
          </p>
        ) : null}
      </div>
      <div className="mt-12">{children}</div>
    </section>
  );
}
