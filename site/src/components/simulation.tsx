"use client";

import { AnimatePresence, motion, useInView } from "motion/react";
import {
  ChevronLeft,
  ChevronRight,
  FileDiff,
  FlaskConical,
  GitBranch,
  Network,
  Pause,
  Play,
  RotateCcw,
  ScanSearch,
  Timer,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { Demo, DemoChange } from "@/lib/data";
import { cn, plural } from "@/lib/utils";

/* ------------------------------------------------------------------ the model */

type Phase = "diff" | "units" | "wave" | "select" | "run";

type Step = { phase: Phase; title: string; log: string; wave?: number };

type Node = { id: string; depth: number; label: string; file: string; isTest: boolean };

type Graph = { nodes: Node[]; edges: [string, string][]; depth: number };

/** The propagation graph behind a change: every chain Karma reported, merged. */
function graphFor(change: DemoChange): Graph {
  const depthOf = new Map<string, number>();
  const edges = new Set<string>();
  for (const chain of Object.values(change.function_level.item_reasons)) {
    const path = [...chain].reverse().map((step) => step.replace(/ \(changed.*\)$/, ""));
    path.forEach((id, depth) => {
      depthOf.set(id, Math.min(depthOf.get(id) ?? Infinity, depth));
      if (depth) edges.add(`${path[depth - 1]}→${id}`);
    });
  }
  if (!depthOf.size) depthOf.set(`${change.file}::${change.symbol}`, 0);
  const nodes = [...depthOf].map(([id, depth]) => {
    const [file, name] = id.split("::");
    return { id, depth, file, label: name, isTest: file.startsWith("tests/test_") && /^(test|Test)/.test(name) };
  });
  nodes.sort((a, b) => a.depth - b.depth || a.id.localeCompare(b.id));
  return {
    nodes,
    edges: [...edges].map((e) => e.split("→") as [string, string]),
    depth: Math.max(...nodes.map((n) => n.depth)),
  };
}

function stepsFor(change: DemoChange, graph: Graph, demo: Demo): Step[] {
  const key = `${change.file}::${change.symbol}`;
  const units = demo.symbols[change.file] ?? [];
  const steps: Step[] = [
    {
      phase: "diff",
      title: "Read the diff",
      log: `git diff (merge base → working tree): 1 changed file, ${change.file}`,
    },
    {
      phase: "units",
      title: "Compare it unit by unit",
      log: `parsed both versions: ${plural(units.length, "unit")}, 1 changed: ${change.symbol} (line ${change.start})`,
    },
  ];
  for (let wave = 1; wave <= graph.depth; wave++) {
    const reached = graph.nodes.filter((n) => n.depth === wave);
    steps.push({
      phase: "wave",
      wave,
      title: wave === 1 ? "Follow the names" : `Follow the names, wave ${wave}`,
      log: `wave ${wave}: ${reached.map((n) => `${n.label} (${n.file.split("/").pop()}${n.isTest ? ", test" : ""})`).join(", ")} ${reached.length === 1 ? "uses" : "use"} ${wave === 1 ? change.symbol : "what changed"}`,
    });
  }
  const selected = change.function_level.targets.length;
  steps.push({
    phase: "select",
    title: "Select the test functions",
    log: `selected ${plural(selected, "test function")} of ${demo.total_tests}; ${demo.total_tests - selected} skipped (${key} cannot reach them)`,
  });
  steps.push({
    phase: "run",
    title: "Run them with pytest",
    log: `pytest ${change.function_level.targets.length ? change.function_level.targets.map((t) => t.split("/").pop()).join(" ") : "(nothing to run)"}`,
  });
  return steps;
}

/* ------------------------------------------------------------------ the component */

const PHASES: { phase: Phase; label: string; icon: typeof FileDiff }[] = [
  { phase: "diff", label: "Diff", icon: GitBranch },
  { phase: "units", label: "Units", icon: ScanSearch },
  { phase: "wave", label: "Names", icon: Network },
  { phase: "select", label: "Select", icon: FlaskConical },
  { phase: "run", label: "Run", icon: Timer },
];

export function Simulation({ demo }: { demo: Demo }) {
  const [changeKey, setChangeKey] = useState("billing/tax.py::rate");
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const log = useRef<HTMLDivElement>(null);
  const inView = useInView(root, { once: true, margin: "-120px" });

  const change = demo.changes.find((c) => `${c.file}::${c.symbol}` === changeKey)!;
  const graph = useMemo(() => graphFor(change), [change]);
  const steps = useMemo(() => stepsFor(change, graph, demo), [change, graph, demo]);
  const step = steps[Math.min(index, steps.length - 1)];

  // Start playing the first time the section scrolls into view.
  useEffect(() => {
    if (inView && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) setPlaying(true);
  }, [inView]);

  useEffect(() => {
    if (!playing) return;
    if (index >= steps.length - 1) {
      setPlaying(false);
      return;
    }
    const timer = setTimeout(() => setIndex((i) => i + 1), step.phase === "run" ? 2600 : 2100);
    return () => clearTimeout(timer);
  }, [playing, index, steps.length, step.phase]);

  // Keep the newest log line in view.
  useEffect(() => {
    log.current?.scrollTo({ top: log.current.scrollHeight });
  }, [index, changeKey]);

  const restart = (key = changeKey) => {
    setChangeKey(key);
    setIndex(0);
    setPlaying(true);
  };

  return (
    <div ref={root} className="overflow-hidden rounded-2xl border bg-card shadow-sm">
      {/* ------------------------------------------------ controls */}
      <div className="flex flex-wrap items-center gap-3 border-b bg-muted/40 px-4 py-3 sm:px-5">
        <label className="flex min-w-0 items-center gap-2 text-sm">
          <span className="text-muted-foreground">Simulate a change to</span>
          <select
            value={changeKey}
            onChange={(e) => restart(e.target.value)}
            className="min-w-0 rounded-md border bg-card px-2 py-1.5 font-mono text-xs"
          >
            {demo.changes.map((c) => (
              <option key={`${c.file}::${c.symbol}`} value={`${c.file}::${c.symbol}`}>
                {c.file.replace("billing/", "")}::{c.symbol}
              </option>
            ))}
          </select>
        </label>
        <div className="ml-auto flex items-center gap-1">
          <IconButton label="Previous step" onClick={() => { setPlaying(false); setIndex((i) => Math.max(0, i - 1)); }} disabled={index === 0}>
            <ChevronLeft />
          </IconButton>
          <button
            type="button"
            onClick={() => (index >= steps.length - 1 ? restart() : setPlaying(!playing))}
            className="inline-flex h-9 items-center gap-1.5 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
            {playing ? "Pause" : index >= steps.length - 1 ? "Replay" : "Play"}
          </button>
          <IconButton label="Next step" onClick={() => { setPlaying(false); setIndex((i) => Math.min(steps.length - 1, i + 1)); }} disabled={index >= steps.length - 1}>
            <ChevronRight />
          </IconButton>
          <IconButton label="Restart" onClick={() => { setPlaying(false); setIndex(0); }}>
            <RotateCcw />
          </IconButton>
        </div>
      </div>

      {/* ------------------------------------------------ phase rail */}
      <ol className="grid grid-cols-5 border-b">
        {PHASES.map((p, i) => {
          const current = PHASES.findIndex((q) => q.phase === step.phase);
          const state = i < current ? "done" : i === current ? "active" : "todo";
          return (
            <li key={p.phase} className="relative">
              <button
                type="button"
                onClick={() => { setPlaying(false); setIndex(steps.findIndex((s) => s.phase === p.phase)); }}
                className={cn(
                  "flex w-full flex-col items-center gap-1 px-1 py-3 text-xs transition-colors sm:flex-row sm:justify-center sm:gap-2 sm:text-sm",
                  state === "active" ? "text-foreground" : state === "done" ? "text-teal" : "text-muted-foreground",
                )}
              >
                <p.icon className="size-4" />
                {p.label}
              </button>
              <span
                className={cn(
                  "absolute inset-x-0 bottom-0 h-0.5 transition-colors",
                  state === "active" ? "bg-amber" : state === "done" ? "bg-teal/50" : "bg-transparent",
                )}
              />
            </li>
          );
        })}
      </ol>

      <div>
        {/* ------------------------------------------------ stage */}
        <div className="min-w-0 border-b p-4 sm:p-6">
          <p className="text-xs uppercase tracking-wider text-muted-foreground">
            Step {index + 1} of {steps.length}
          </p>
          <h3 className="mt-1 text-lg font-semibold">{step.title}</h3>
          <div className="mt-5 min-h-[340px]">
            <AnimatePresence mode="wait">
              {step.phase === "diff" ? (
                <Fade key="diff">
                  <DiffView change={change} />
                </Fade>
              ) : step.phase === "units" ? (
                <Fade key="units">
                  <UnitsView change={change} demo={demo} />
                </Fade>
              ) : step.phase === "run" ? (
                <Fade key="run">
                  <RunView change={change} demo={demo} />
                </Fade>
              ) : (
                <Fade key="graph">
                  <GraphView
                    graph={graph}
                    wave={step.phase === "wave" ? step.wave ?? 0 : graph.depth}
                    select={step.phase === "select"}
                    demo={demo}
                    change={change}
                  />
                </Fade>
              )}
            </AnimatePresence>
          </div>
        </div>

        {/* ------------------------------------------------ log */}
        <div
          ref={log}
          className="max-h-52 min-w-0 overflow-y-auto scroll-smooth bg-terminal p-4 font-mono text-[12px] leading-5 text-terminal-foreground"
        >
          <p className="mb-3 flex items-center gap-2 text-[#7d9199]">
            <span className="size-2 rounded-full bg-[#52c79b]" /> karma --explain
          </p>
          <ol className="space-y-2">
            {steps.slice(0, index + 1).map((s, i) => (
              <motion.li
                key={`${changeKey}-${i}`}
                initial={{ opacity: 0, x: -6 }}
                animate={{ opacity: 1, x: 0 }}
                className={cn("break-words", i === index ? "text-terminal-foreground" : "text-[#7d9199]")}
              >
                <span className="text-[#f0a03a]">{String(i + 1).padStart(2, "0")}</span> {s.log}
              </motion.li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}

function IconButton({
  label,
  children,
  ...props
}: React.ComponentProps<"button"> & { label: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      className="inline-flex size-9 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-40 [&_svg]:size-4"
      {...props}
    >
      {children}
    </button>
  );
}

function Fade({ children }: { children: React.ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      transition={{ duration: 0.25 }}
    >
      {children}
    </motion.div>
  );
}

/* ------------------------------------------------------------------ stage views */

function DiffView({ change }: { change: DemoChange }) {
  const rows: { sign: string; text: string; line: number }[] = [];
  change.before.forEach((text, i) => {
    if (text !== change.after[i]) rows.push({ sign: "-", text, line: change.start + i });
    else rows.push({ sign: " ", text, line: change.start + i });
  });
  change.after.forEach((text, i) => {
    if (text !== change.before[i]) rows.splice(rows.findIndex((r) => r.line === change.start + i) + 1, 0, { sign: "+", text, line: change.start + i });
  });
  return (
    <div>
      <p className="text-sm text-muted-foreground">
        Karma diffs from the <strong className="text-foreground">merge base</strong>: exactly
        what the pull request contains, plus uncommitted work when run locally.
      </p>
      <div className="mt-4 overflow-x-auto rounded-xl border bg-muted/40 font-mono text-[13px]">
        <p className="border-b px-4 py-2 text-xs text-muted-foreground">
          <FileDiff className="mr-1.5 inline size-3.5" />
          {change.file}
        </p>
        <div className="py-2">
          {rows.map((row, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: i * 0.12 }}
              className={cn(
                "flex min-w-max whitespace-pre px-4 leading-7",
                row.sign === "-" && "bg-fail/10 text-fail",
                row.sign === "+" && "bg-pass/10 text-pass",
              )}
            >
              <span className="w-8 shrink-0 select-none text-muted-foreground/60">{row.line}</span>
              <span className="w-4 shrink-0 select-none">{row.sign}</span>
              {row.text}
            </motion.div>
          ))}
        </div>
      </div>
    </div>
  );
}

function UnitsView({ change, demo }: { change: DemoChange; demo: Demo }) {
  const units = demo.symbols[change.file] ?? [];
  return (
    <div>
      <p className="text-sm text-muted-foreground">
        Both versions are parsed. Each top-level unit is compared by its syntax tree, ignoring
        comments, formatting and position, so only real changes count.
      </p>
      <ul className="mt-4 space-y-2">
        {units.map((unit, i) => {
          const changed = unit.name === change.symbol;
          return (
            <motion.li
              key={unit.name}
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: i * 0.18 }}
              className={cn(
                "flex items-center justify-between gap-3 rounded-lg border px-4 py-2.5",
                changed ? "border-amber/50 bg-amber-soft" : "bg-card",
              )}
            >
              <span className="min-w-0 truncate font-mono text-sm">
                {unit.name}
                <span className="ml-2 font-sans text-xs text-muted-foreground">{unit.kind}</span>
              </span>
              <motion.span
                initial={{ opacity: 0, scale: 0.8 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ delay: i * 0.18 + 0.35 }}
                className={cn(
                  "shrink-0 rounded-full px-2.5 py-0.5 text-xs font-semibold",
                  changed ? "bg-amber text-primary-foreground" : "bg-muted text-muted-foreground",
                )}
              >
                {changed ? `changed · line ${change.start}` : "same tree"}
              </motion.span>
            </motion.li>
          );
        })}
      </ul>
    </div>
  );
}

const COL = 178;
const ROW = 56;
const NODE_W = 156;
const NODE_H = 42;

function GraphView({
  graph,
  wave,
  select,
  demo,
  change,
}: {
  graph: Graph;
  wave: number;
  select: boolean;
  demo: Demo;
  change: DemoChange;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  // On narrow screens the graph scrolls sideways: keep the newest wave in view.
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const right = 5 + Math.min(wave, graph.depth) * COL + NODE_W + 12;
    el.scrollTo({ left: Math.max(0, right - el.clientWidth), behavior: "smooth" });
  }, [wave, graph.depth]);
  const columns = graph.depth + 1;
  const byDepth = Array.from({ length: columns }, (_, d) => graph.nodes.filter((n) => n.depth === d));
  const tallest = Math.max(...byDepth.map((c) => c.length), 1);
  const width = columns * COL + 10;
  const height = tallest * ROW + 10;
  const position = new Map<string, { x: number; y: number }>();
  byDepth.forEach((column, d) =>
    column.forEach((node, i) => {
      const offset = ((tallest - column.length) * ROW) / 2;
      position.set(node.id, { x: 5 + d * COL, y: 5 + offset + i * ROW });
    }),
  );
  const skipped = Object.entries(demo.tests).flatMap(([file, names]) =>
    names.map((n) => `${file}::${n}`).filter((id) => !change.function_level.targets.includes(id)),
  );

  return (
    <div>
      <p className="text-sm text-muted-foreground">
        {select
          ? "The test functions at the end of these chains are selected. Everything else cannot reach the change."
          : "Who mentions the changed name: imports, attribute chains, fixtures by parameter name, through every conftest.py. Each wave reaches one step further."}
      </p>
      <div ref={scroller} className="mt-4 overflow-x-auto">
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="max-w-none">
          {graph.edges.map(([from, to]) => {
            const a = position.get(from)!;
            const b = position.get(to)!;
            const visible = (graph.nodes.find((n) => n.id === to)?.depth ?? 0) <= wave;
            const x1 = a.x + NODE_W;
            const y1 = a.y + NODE_H / 2;
            const x2 = b.x;
            const y2 = b.y + NODE_H / 2;
            const mid = (x1 + x2) / 2;
            return (
              <motion.path
                key={`${from}-${to}`}
                d={`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`}
                fill="none"
                strokeWidth={1.5}
                className="stroke-teal"
                initial={{ pathLength: 0, opacity: 0 }}
                animate={{ pathLength: visible ? 1 : 0, opacity: visible ? 0.7 : 0 }}
                transition={{ duration: 0.6 }}
              />
            );
          })}
          {graph.nodes.map((node) => {
            const p = position.get(node.id)!;
            const visible = node.depth <= wave;
            const fresh = node.depth === wave && !select;
            const tone =
              node.depth === 0
                ? "fill-amber-soft stroke-amber"
                : node.isTest && select
                  ? "fill-pass/15 stroke-pass"
                  : fresh
                    ? "fill-teal-soft stroke-teal"
                    : "fill-card stroke-border";
            return (
              <motion.g
                key={node.id}
                initial={{ opacity: 0, scale: 0.85 }}
                animate={{ opacity: visible ? 1 : 0.12, scale: visible ? 1 : 0.95 }}
                transition={{ duration: 0.35 }}
                style={{ transformOrigin: `${p.x + NODE_W / 2}px ${p.y + NODE_H / 2}px` }}
              >
                <rect x={p.x} y={p.y} width={NODE_W} height={NODE_H} rx={8} strokeWidth={1.5} className={tone} />
                <text x={p.x + 10} y={p.y + 17} className="fill-foreground font-mono text-[11.5px] font-medium">
                  {node.label.length > 20 ? `${node.label.slice(0, 19)}…` : node.label}
                </text>
                <text x={p.x + 10} y={p.y + 32} className="fill-muted-foreground font-mono text-[9.5px]">
                  {node.file.length > 25 ? `…${node.file.slice(-24)}` : node.file}
                </text>
              </motion.g>
            );
          })}
        </svg>
      </div>
      {select ? (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mt-4">
          <p className="text-xs text-muted-foreground">Skipped ({skipped.length})</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {skipped.map((id) => (
              <span key={id} className="rounded-md border border-dashed px-2 py-0.5 font-mono text-[11px] text-muted-foreground/70 line-through">
                {id.split("::")[1]}
              </span>
            ))}
          </div>
        </motion.div>
      ) : null}
    </div>
  );
}

function RunView({ change, demo }: { change: DemoChange; demo: Demo }) {
  const targets = change.function_level.targets;
  const fileLevel = change.file_level.files.reduce((n, f) => n + (demo.tests[f]?.length ?? 0), 0);
  const [done, setDone] = useState(0);
  useEffect(() => {
    setDone(0);
    let n = 0;
    const timer = setInterval(() => {
      n += 1;
      setDone(n);
      if (n >= targets.length) clearInterval(timer);
    }, 260);
    return () => clearInterval(timer);
  }, [targets.length]);

  return (
    <div>
      <div className="rounded-xl bg-terminal p-4 font-mono text-[12.5px] leading-6 text-terminal-foreground">
        <p>
          <span className="text-[#52c79b]">$</span> pytest{" "}
          {targets.length ? `${targets.length} node ids` : "(nothing to run)"}
        </p>
        {targets.map((t, i) => (
          <p key={t} className={cn("transition-opacity", i < done ? "opacity-100" : "opacity-25")}>
            {t}{" "}
            <span className={i < done ? "text-[#52c79b]" : "text-[#7d9199]"}>{i < done ? "PASSED" : "…"}</span>
          </p>
        ))}
        {done >= targets.length ? (
          <p className="mt-2 text-[#52c79b]">
            ===== {targets.length} passed in {(0.04 + targets.length * 0.07).toFixed(2)}s =====
          </p>
        ) : null}
      </div>
      <div className="mt-5 grid grid-cols-3 gap-3 text-center">
        <Stat value={targets.length} label="tests run, function level" tone="amber" />
        <Stat value={fileLevel} label="file level would run" />
        <Stat value={demo.total_tests} label="in the whole suite" />
      </div>
    </div>
  );
}

function Stat({ value, label, tone }: { value: number; label: string; tone?: "amber" }) {
  return (
    <div className={cn("rounded-xl border p-3", tone === "amber" && "border-amber/40 bg-amber-soft")}>
      <p className={cn("font-mono text-2xl font-semibold", tone === "amber" && "text-amber")}>{value}</p>
      <p className="mt-0.5 text-[11px] leading-tight text-muted-foreground">{label}</p>
    </div>
  );
}
