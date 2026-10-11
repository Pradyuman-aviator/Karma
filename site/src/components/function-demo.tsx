"use client";

import { AnimatePresence, motion } from "motion/react";
import { FileCode2, FlaskConical, MousePointerClick } from "lucide-react";
import { useMemo, useState } from "react";

import type { Demo, DemoChange } from "@/lib/data";
import { cn, plural } from "@/lib/utils";

type Mode = "function" | "file";

export function FunctionDemo({
  demo,
  highlighted,
}: {
  demo: Demo;
  highlighted: Record<string, string[]>;
}) {
  const files = Object.keys(highlighted);
  const [file, setFile] = useState("billing/tax.py");
  const [changeKey, setChangeKey] = useState("billing/tax.py::rate");
  const [mode, setMode] = useState<Mode>("function");
  const [focus, setFocus] = useState<string | null>(null);

  const change = demo.changes.find((c) => `${c.file}::${c.symbol}` === changeKey)!;
  const symbolsHere = demo.changes.filter((c) => c.file === file);

  const pick = (c: DemoChange) => {
    setChangeKey(`${c.file}::${c.symbol}`);
    setFocus(null);
  };

  return (
    <div className="grid overflow-hidden rounded-2xl border bg-card shadow-sm lg:grid-cols-[1.1fr_1fr]">
      {/* ------------------------------------------------------------ the code */}
      <div className="min-w-0 border-b lg:border-b-0 lg:border-r">
        <div className="flex items-center gap-1 overflow-x-auto border-b bg-muted/50 px-2 py-2">
          {files.map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => {
                setFile(f);
                const first = demo.changes.find((c) => c.file === f);
                if (first) pick(first);
              }}
              className={cn(
                "flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1.5 font-mono text-xs transition-colors",
                f === file
                  ? "bg-card text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <FileCode2 className="size-3.5" />
              {f.replace("billing/", "")}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2 px-4 pt-3 text-xs text-muted-foreground">
          <MousePointerClick className="size-3.5" /> Click a highlighted symbol to change it
        </div>
        <div className="overflow-x-auto py-3 font-mono text-[13px] leading-6">
          {highlighted[file].map((html, index) => {
            const line = index + 1;
            const owner = symbolsHere.find((c) => line >= c.start && line <= c.end);
            const active = owner && `${owner.file}::${owner.symbol}` === changeKey;
            return (
              <div
                key={line}
                onClick={owner ? () => pick(owner) : undefined}
                className={cn(
                  "group flex min-w-max pr-4",
                  owner && "cursor-pointer",
                  active ? "bg-amber-soft" : owner && "hover:bg-accent",
                )}
              >
                <span
                  className={cn(
                    "w-12 shrink-0 select-none border-l-2 pr-4 text-right text-muted-foreground/60",
                    active ? "border-amber text-amber" : "border-transparent",
                  )}
                >
                  {line}
                </span>
                <span className="shiki whitespace-pre" dangerouslySetInnerHTML={{ __html: html || " " }} />
                {active && line === owner.start ? (
                  <span className="ml-4 self-center rounded-full bg-amber px-2 py-px font-sans text-[10px] font-semibold uppercase tracking-wide text-primary-foreground">
                    changed
                  </span>
                ) : null}
              </div>
            );
          })}
        </div>
      </div>

      {/* ------------------------------------------------------------ the selection */}
      <Results
        demo={demo}
        change={change}
        mode={mode}
        setMode={setMode}
        focus={focus}
        setFocus={setFocus}
      />
    </div>
  );
}

function Results({
  demo,
  change,
  mode,
  setMode,
  focus,
  setFocus,
}: {
  demo: Demo;
  change: DemoChange;
  mode: Mode;
  setMode: (m: Mode) => void;
  focus: string | null;
  setFocus: (f: string | null) => void;
}) {
  const fn = change.function_level;
  const fileLevel = change.file_level;
  const selected = useMemo(() => {
    if (mode === "function") return new Set(fn.targets);
    const ids = new Set<string>();
    for (const f of fileLevel.files) for (const t of demo.tests[f] ?? []) ids.add(`${f}::${t}`);
    return ids;
  }, [mode, fn, fileLevel, demo]);
  const fileCount = fileLevel.files.reduce((n, f) => n + (demo.tests[f]?.length ?? 0), 0);
  const total = demo.total_tests;
  const shownFocus = focus && selected.has(focus) ? focus : mode === "function" ? fn.targets[0] : null;
  const chain = shownFocus ? fn.item_reasons[shownFocus] ?? [] : [];

  return (
    <div className="flex min-w-0 flex-col p-5 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs uppercase tracking-wider text-muted-foreground">You changed</p>
          <p className="truncate font-mono text-sm font-medium text-amber">
            {change.file}::{change.symbol}
          </p>
        </div>
        <div className="inline-flex rounded-lg bg-muted p-1 text-sm" role="tablist">
          {(["function", "file"] as const).map((m) => (
            <button
              key={m}
              type="button"
              role="tab"
              aria-selected={mode === m}
              onClick={() => setMode(m)}
              className={cn(
                "rounded-md px-3 py-1.5 font-medium transition-all",
                mode === m ? "bg-card text-foreground shadow-sm" : "text-muted-foreground",
              )}
            >
              {m === "function" ? "Function level" : "File level"}
            </button>
          ))}
        </div>
      </div>

      <Meter
        function={fn.targets.length}
        file={fileCount}
        total={total}
        mode={mode}
      />

      <div className="mt-5 space-y-3">
        {Object.entries(demo.tests).map(([testFile, names]) => (
          <div key={testFile}>
            <p className="flex items-center gap-1.5 font-mono text-xs text-muted-foreground">
              <FlaskConical className="size-3.5" /> {testFile}
            </p>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {names.map((name) => {
                const id = `${testFile}::${name}`;
                const on = selected.has(id);
                return (
                  <motion.button
                    key={id}
                    type="button"
                    layout
                    disabled={!on || mode === "file"}
                    onClick={() => setFocus(id)}
                    className={cn(
                      "rounded-md border px-2 py-1 font-mono text-xs transition-colors",
                      on
                        ? "border-pass/40 bg-pass/10 text-pass"
                        : "border-dashed text-muted-foreground/60 line-through decoration-muted-foreground/40",
                      on && mode === "function" && "cursor-pointer hover:bg-pass/20",
                      id === shownFocus && "ring-2 ring-pass/50",
                    )}
                  >
                    {name}
                  </motion.button>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      <div className="mt-6 min-h-36 rounded-xl border bg-muted/40 p-4">
        <AnimatePresence mode="wait">
          {mode === "function" && shownFocus ? (
            <motion.div
              key={shownFocus + change.symbol}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
            >
              <p className="text-xs uppercase tracking-wider text-muted-foreground">
                Why {shownFocus.split("::")[1]} runs
              </p>
              <ol className="mt-3 space-y-0">
                {chain.map((step, i) => {
                  const changed = step.includes("(changed");
                  return (
                    <li key={step} className="relative flex gap-3 pb-3 last:pb-0">
                      {i < chain.length - 1 ? (
                        <span className="absolute left-[5px] top-4 h-full w-px bg-border" />
                      ) : null}
                      <span
                        className={cn(
                          "relative mt-1.5 size-[11px] shrink-0 rounded-full border-2",
                          changed ? "border-amber bg-amber" : i === 0 ? "border-pass bg-pass" : "border-teal bg-card",
                        )}
                      />
                      <span className={cn("break-all font-mono text-xs leading-5", changed && "text-amber")}>
                        {i > 0 ? <span className="text-muted-foreground">uses </span> : null}
                        {step}
                      </span>
                    </li>
                  );
                })}
              </ol>
            </motion.div>
          ) : (
            <motion.p
              key="file-mode"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="text-sm text-muted-foreground"
            >
              {mode === "file"
                ? `File level runs every test in each file that imports the change: ${plural(fileLevel.files.length, "file")}, ${plural(fileCount, "test")}. ${
                    fileLevel.files.length > 1
                      ? "tests/conftest.py imports billing.invoice, and a conftest applies to every test beneath it."
                      : ""
                  }`
                : "Nothing to run: no test uses this symbol."}
            </motion.p>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

function Meter({
  function: fnCount,
  file,
  total,
  mode,
}: {
  function: number;
  file: number;
  total: number;
  mode: Mode;
}) {
  const shown = mode === "function" ? fnCount : file;
  return (
    <div className="mt-5">
      <div className="flex items-baseline justify-between">
        <p className="text-sm">
          <span className="font-mono text-2xl font-semibold">{shown}</span>
          <span className="text-muted-foreground"> of {total} tests run</span>
        </p>
        {mode === "function" && file > fnCount ? (
          <p className="text-xs font-medium text-pass">
            {file - fnCount} fewer than file level
          </p>
        ) : null}
      </div>
      <div className="relative mt-2 h-2 overflow-hidden rounded-full bg-muted">
        <motion.div
          className="absolute inset-y-0 left-0 rounded-full bg-muted-foreground/25"
          animate={{ width: `${(file / total) * 100}%` }}
        />
        <motion.div
          className="absolute inset-y-0 left-0 rounded-full bg-amber"
          animate={{ width: `${(shown / total) * 100}%` }}
          transition={{ type: "spring", stiffness: 140, damping: 20 }}
        />
      </div>
    </div>
  );
}
