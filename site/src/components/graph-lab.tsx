"use client";

import { motion } from "motion/react";
import { ClipboardPaste, Crosshair, Flame, RotateCcw, Waves } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import type { Graph as Sample } from "@/lib/data";
import { cn, percent, plural } from "@/lib/utils";

/* ------------------------------------------------------------------ Karma's rules */

const DIRECTORY_SCOPED = new Set(["conftest.py", "__init__.py"]);

const base = (path: string) => path.slice(path.lastIndexOf("/") + 1);
const dir = (path: string) => (path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "");

function isTest(path: string) {
  const name = base(path);
  if (DIRECTORY_SCOPED.has(name) || !name.endsWith(".py")) return false;
  return name.startsWith("test_") || name.endsWith("_test.py");
}

type Model = {
  nodes: string[];
  dependents: Map<string, string[]>;
  degree: Map<string, number>;
  tests: string[];
  edges: [string, string][];
};

function model(edges: Record<string, string[]>): Model {
  const nodes = [...new Set([...Object.keys(edges), ...Object.values(edges).flat()])].sort();
  const dependents = new Map<string, string[]>(nodes.map((n) => [n, []]));
  const pairs: [string, string][] = [];
  for (const [file, imports] of Object.entries(edges)) {
    for (const target of imports) {
      dependents.get(target)!.push(file);
      pairs.push([file, target]);
    }
  }
  const degree = new Map(nodes.map((n) => [n, dependents.get(n)!.length + (edges[n]?.length ?? 0)]));
  return { nodes, dependents, degree, tests: nodes.filter(isTest), edges: pairs };
}

/** What a change to `node` directly affects: its importers, and, for a conftest.py or a
 * package __init__.py, every test beneath its directory (pytest loads them implicitly). */
function affected(m: Model, node: string): string[] {
  const found = new Set(m.dependents.get(node) ?? []);
  if (DIRECTORY_SCOPED.has(base(node))) {
    const prefix = dir(node) ? `${dir(node)}/` : "";
    for (const t of m.tests) if (t.startsWith(prefix)) found.add(t);
  }
  found.delete(node);
  return [...found].sort();
}

type Blast = { depth: Map<string, number>; parent: Map<string, string | null>; waves: number };

/** Breadth-first search from the changed file, exactly like `karma select`. */
function blast(m: Model, start: string): Blast {
  const depth = new Map([[start, 0]]);
  const parent = new Map<string, string | null>([[start, null]]);
  const queue = [start];
  while (queue.length) {
    const node = queue.shift()!;
    for (const next of affected(m, node)) {
      if (!depth.has(next)) {
        depth.set(next, depth.get(node)! + 1);
        parent.set(next, node);
        queue.push(next);
      }
    }
  }
  return { depth, parent, waves: Math.max(...depth.values()) };
}

function chain(b: Blast, node: string): string[] {
  const out = [node];
  for (let p = b.parent.get(node); p; p = b.parent.get(p)) out.push(p);
  return out;
}

/* ------------------------------------------------------------------ layout */

const W = 900;
const H = 620;

/** A deterministic Fruchterman–Reingold layout, with tests drawn to the right and
 * source files to the left, scaled to fit the frame. */
function layout(m: Model): Map<string, { x: number; y: number }> {
  let seed = 11;
  const random = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const side = (n: string) => (isTest(n) ? 0.8 : DIRECTORY_SCOPED.has(base(n)) ? 0.55 : 0.25);
  const nodes = m.nodes.map((n) => ({ n, x: side(n) * W + (random() - 0.5) * 120, y: random() * H, dx: 0, dy: 0 }));
  const index = new Map(nodes.map((p, i) => [p.n, i]));
  const k = 0.75 * Math.sqrt((W * H) / Math.max(nodes.length, 1));
  const steps = 260;
  for (let step = 0; step < steps; step++) {
    const t = (W / 8) * (1 - step / steps) + 1;
    for (const p of nodes) {
      p.dx = 0;
      p.dy = 0;
    }
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i];
        const b = nodes[j];
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const d = Math.max(Math.hypot(dx, dy), 0.5);
        const f = (k * k) / d;
        a.dx += (dx / d) * f;
        a.dy += (dy / d) * f;
        b.dx -= (dx / d) * f;
        b.dy -= (dy / d) * f;
      }
    }
    for (const [from, to] of m.edges) {
      const a = nodes[index.get(from)!];
      const b = nodes[index.get(to)!];
      const dx = a.x - b.x;
      const dy = a.y - b.y;
      const d = Math.max(Math.hypot(dx, dy), 0.5);
      const f = (d * d) / k / 4;
      a.dx -= (dx / d) * f;
      a.dy -= (dy / d) * f;
      b.dx += (dx / d) * f;
      b.dy += (dy / d) * f;
    }
    for (const p of nodes) {
      p.dx += (side(p.n) * W - p.x) * 1.4; // tests right, source left
      p.dy += (H / 2 - p.y) * 0.15;
      const d = Math.max(Math.hypot(p.dx, p.dy), 0.01);
      p.x += (p.dx / d) * Math.min(d, t);
      p.y += (p.dy / d) * Math.min(d, t);
    }
  }
  // Fit the drawing to the frame.
  // Fit the drawing to the frame, ignoring a few outliers so they cannot squash the rest.
  const bounds = (values: number[]) => {
    const sorted = [...values].sort((x, y) => x - y);
    const cut = Math.min(3, Math.floor(sorted.length / 10));
    return [sorted[cut], sorted[sorted.length - 1 - cut]];
  };
  const [x0, x1] = bounds(nodes.map((p) => p.x));
  const [y0, y1] = bounds(nodes.map((p) => p.y));
  const pad = 30;
  const fit = (v: number, lo: number, hi: number, size: number) =>
    Math.min(size - pad / 2, Math.max(pad / 2, pad + ((v - lo) / Math.max(hi - lo, 1)) * (size - 2 * pad)));
  return new Map(nodes.map((p) => [p.n, { x: fit(p.x, x0, x1, W - 90), y: fit(p.y, y0, y1, H) }]));
}

/* ------------------------------------------------------------------ the component */

export function GraphLab({ samples }: { samples: Record<string, Sample> }) {
  const [source, setSource] = useState<string>("starlette");
  const [custom, setCustom] = useState<Record<string, string[]> | null>(null);
  const [pasting, setPasting] = useState(false);
  const edges = source === "custom" && custom ? custom : samples[source].edges;
  const m = useMemo(() => model(edges), [edges]);
  const pos = useMemo(() => layout(m), [m]);

  const blasts = useMemo(() => new Map(m.nodes.map((n) => [n, blast(m, n)])), [m]);
  const ranking = useMemo(
    () =>
      m.nodes
        .filter((n) => !isTest(n))
        .map((n) => ({ node: n, tests: m.tests.filter((t) => blasts.get(n)!.depth.has(t)).length }))
        .map((r) => ({ ...r, files: blasts.get(r.node)!.depth.size - 1 }))
        .sort((a, b) => b.tests - a.tests || b.files - a.files || a.node.localeCompare(b.node))
        .slice(0, 6),
    [m, blasts],
  );

  const suggested = useMemo(() => {
    const share = (n: string) => m.tests.filter((t) => blasts.get(n)!.depth.has(t)).length / Math.max(m.tests.length, 1);
    const candidates = m.nodes.filter((n) => !isTest(n) && !DIRECTORY_SCOPED.has(base(n)));
    return candidates.sort((a, b) => Math.abs(share(a) - 0.35) - Math.abs(share(b) - 0.35) || a.localeCompare(b))[0];
  }, [m, blasts]);
  const [changed, setChanged] = useState<string | null>(suggested ?? null);
  const [wave, setWave] = useState(0);
  const [hover, setHover] = useState<string | null>(null);
  const [focus, setFocus] = useState<string | null>(null);

  useEffect(() => {
    setChanged(suggested ?? null);
    setFocus(null);
  }, [suggested, m]);

  const current = changed ? blasts.get(changed) : undefined;

  // Ripple outwards one wave at a time.
  useEffect(() => {
    setWave(0);
    if (!current) return;
    let w = 0;
    const timer = setInterval(() => {
      w += 1;
      setWave(w);
      if (w >= current.waves) clearInterval(timer);
    }, 520);
    return () => clearInterval(timer);
  }, [current]);

  const reached = (n: string) => current !== undefined && (current.depth.get(n) ?? Infinity) <= wave;
  const selectedTests = current ? m.tests.filter((t) => current.depth.has(t)) : [];
  const filesReached = current ? current.depth.size - 1 : 0;
  const focusChain = focus && current ? chain(current, focus) : [];
  const onChain = new Set(focusChain);
  const hubs = new Set(
    [...m.degree.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10).map(([n]) => n),
  );

  const neighbours = useMemo(() => {
    if (!hover) return null;
    return { imports: new Set(edges[hover] ?? []), importers: new Set(m.dependents.get(hover) ?? []) };
  }, [hover, edges, m]);

  return (
    <div className="grid overflow-hidden rounded-2xl border bg-card shadow-sm lg:grid-cols-[1fr_340px]">
      {/* ------------------------------------------------ graph */}
      <div className="min-w-0 border-b lg:border-b-0 lg:border-r">
        <div className="flex flex-wrap items-center gap-2 border-b bg-muted/40 px-4 py-3">
          {Object.entries(samples).map(([key, sample]) => (
            <SourceButton key={key} active={source === key} onClick={() => setSource(key)}>
              {sample.title ?? key}
              {sample.real ? <span className="ml-1 text-[10px] text-pass">real</span> : null}
            </SourceButton>
          ))}
          {custom ? (
            <SourceButton active={source === "custom"} onClick={() => setSource("custom")}>
              Your graph
            </SourceButton>
          ) : null}
          <button
            type="button"
            onClick={() => setPasting(!pasting)}
            className="ml-auto inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <ClipboardPaste className="size-3.5" /> Load your project
          </button>
        </div>
        {pasting ? (
          <PasteGraph
            onLoad={(g) => {
              setCustom(g);
              setSource("custom");
              setPasting(false);
            }}
          />
        ) : null}
        <div className="relative">
          <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full" role="img" aria-label="Dependency graph">
            {m.edges.map(([from, to]) => {
              const a = pos.get(from)!;
              const b = pos.get(to)!;
              const tree = current?.parent.get(from) === to && reached(from);
              const chainEdge = onChain.has(from) && onChain.has(to) && current?.parent.get(from) === to;
              const hovered = hover && (from === hover || to === hover);
              return (
                <line
                  key={`${from}>${to}`}
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  strokeWidth={chainEdge ? 2.5 : tree || hovered ? 1.4 : 0.6}
                  className={cn(
                    "transition-[stroke,opacity] duration-500",
                    chainEdge ? "stroke-pass" : tree ? "stroke-amber" : hovered ? "stroke-teal" : "stroke-muted-foreground",
                  )}
                  opacity={chainEdge ? 1 : tree ? 0.75 : hovered ? 0.9 : current ? 0.08 : 0.18}
                />
              );
            })}
            {m.nodes.map((n) => {
              const p = pos.get(n)!;
              const r = 3.5 + Math.sqrt(m.dependents.get(n)!.length) * 1.6;
              const hit = reached(n);
              const isChanged = n === changed;
              const test = isTest(n);
              const dim = hover && neighbours && n !== hover && !neighbours.imports.has(n) && !neighbours.importers.has(n);
              const showLabel = isChanged || n === hover || onChain.has(n) || (hubs.has(n) && !current) || (hit && !test && r > 6);
              return (
                <g
                  key={n}
                  className="cursor-pointer"
                  opacity={dim ? 0.2 : 1}
                  onMouseEnter={() => setHover(n)}
                  onMouseLeave={() => setHover(null)}
                  onClick={() => {
                    setChanged(n);
                    setFocus(null);
                  }}
                >
                  {isChanged ? (
                    <motion.circle
                      cx={p.x}
                      cy={p.y}
                      r={r + 4}
                      className="fill-none stroke-amber"
                      strokeWidth={2}
                      animate={{ r: [r + 3, r + 14], opacity: [0.9, 0] }}
                      transition={{ duration: 1.6, repeat: Infinity }}
                    />
                  ) : null}
                  <circle
                    cx={p.x}
                    cy={p.y}
                    r={r}
                    strokeWidth={1.5}
                    className={cn(
                      "transition-[fill,stroke] duration-500",
                      isChanged
                        ? "fill-amber stroke-amber"
                        : hit && test
                          ? "fill-pass stroke-pass"
                          : hit
                            ? "fill-amber/60 stroke-amber"
                            : test
                              ? "fill-card stroke-pass/50"
                              : DIRECTORY_SCOPED.has(base(n))
                                ? "fill-card stroke-teal"
                                : "fill-muted stroke-muted-foreground/50",
                    )}
                  />
                  {showLabel ? (
                    <text
                      x={p.x + r + 4}
                      y={p.y + 3.5}
                      className={cn(
                        "pointer-events-none fill-foreground font-mono text-[10.5px]",
                        isChanged && "fill-amber font-semibold",
                      )}
                      style={{ paintOrder: "stroke", stroke: "var(--card)", strokeWidth: 3 }}
                    >
                      {n === hover ? n : base(n)}
                    </text>
                  ) : null}
                </g>
              );
            })}
          </svg>
        </div>
        <Legend />
      </div>

      {/* ------------------------------------------------ panel */}
      <div className="flex min-w-0 flex-col gap-5 p-5">
        <div>
          <p className="flex items-center gap-1.5 text-xs uppercase tracking-wider text-muted-foreground">
            <Crosshair className="size-3.5" /> Changed file
          </p>
          <p className="mt-1 break-all font-mono text-sm font-medium text-amber">{changed ?? "click a file"}</p>
        </div>
        {current ? (
          <>
            <div className="grid grid-cols-3 gap-2 text-center">
              <Metric value={String(filesReached)} label="files reached" />
              <Metric value={`${selectedTests.length}/${m.tests.length}`} label="test files" tone="pass" />
              <Metric value={String(current.waves)} label="waves" />
            </div>
            <div>
              <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Waves className="size-3.5" /> Blast radius, wave by wave
              </p>
              <div className="mt-2 flex h-14 items-end gap-1">
                {Array.from({ length: current.waves }, (_, i) => i + 1).map((w) => {
                  const count = [...current.depth.values()].filter((d) => d === w).length;
                  const max = Math.max(...Array.from({ length: current.waves }, (_, i) => [...current.depth.values()].filter((d) => d === i + 1).length));
                  return (
                    <div key={w} className="flex flex-1 flex-col items-center gap-1">
                      <motion.div
                        className={cn("w-full rounded-t-sm", w <= wave ? "bg-amber" : "bg-muted")}
                        initial={{ height: 0 }}
                        animate={{ height: `${(count / max) * 40 + 4}px` }}
                      />
                      <span className="font-mono text-[10px] text-muted-foreground">{count}</span>
                    </div>
                  );
                })}
              </div>
              <p className="mt-2 text-sm">
                Karma runs <strong>{plural(selectedTests.length, "test file")}</strong> of {m.tests.length}:{" "}
                <span className="text-pass">{percent(1 - selectedTests.length / Math.max(m.tests.length, 1))} skipped</span>.
              </p>
            </div>
            <div className="min-h-0">
              <p className="text-xs text-muted-foreground">Selected tests: click one to see its chain</p>
              <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto pr-1">
                {selectedTests.map((t) => (
                  <li key={t}>
                    <button
                      type="button"
                      onClick={() => setFocus(t === focus ? null : t)}
                      className={cn(
                        "w-full truncate rounded px-2 py-1 text-left font-mono text-[11px] transition-colors",
                        t === focus ? "bg-pass/15 text-pass" : "hover:bg-accent",
                      )}
                    >
                      {t}
                    </button>
                  </li>
                ))}
                {!selectedTests.length ? <li className="text-xs text-muted-foreground">No test depends on this file.</li> : null}
              </ul>
              {focusChain.length ? (
                <p className="mt-2 break-all font-mono text-[11px] leading-5 text-muted-foreground">
                  {focusChain.map((step, i) => (
                    <span key={step}>
                      {i ? <span className="text-pass"> ← </span> : null}
                      <span className={i === focusChain.length - 1 ? "text-amber" : "text-foreground"}>{base(step)}</span>
                    </span>
                  ))}
                </p>
              ) : null}
            </div>
          </>
        ) : null}
        <div className="mt-auto">
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Flame className="size-3.5 text-amber" /> Biggest blast radius
          </p>
          <ul className="mt-2 space-y-1.5">
            {ranking.map((r) => (
              <li key={r.node}>
                <button
                  type="button"
                  onClick={() => {
                    setChanged(r.node);
                    setFocus(null);
                  }}
                  className="group w-full text-left"
                >
                  <div className="flex justify-between gap-2 font-mono text-[11px]">
                    <span className={cn("truncate group-hover:text-amber", r.node === changed && "text-amber")}>{r.node}</span>
                    <span className="text-muted-foreground">{r.tests}</span>
                  </div>
                  <div className="mt-1 h-1 rounded-full bg-muted">
                    <div className="h-full rounded-full bg-amber/70" style={{ width: `${(r.tests / Math.max(m.tests.length, 1)) * 100}%` }} />
                  </div>
                </button>
              </li>
            ))}
          </ul>
          <button
            type="button"
            onClick={() => {
              setChanged(null);
              setFocus(null);
            }}
            className="mt-4 inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
          >
            <RotateCcw className="size-3.5" /> Clear
          </button>
        </div>
      </div>
    </div>
  );
}

function SourceButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-md px-2.5 py-1.5 font-mono text-xs transition-colors",
        active ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function Metric({ value, label, tone }: { value: string; label: string; tone?: "pass" }) {
  return (
    <div className="rounded-lg border bg-muted/40 px-2 py-2">
      <p className={cn("font-mono text-lg font-semibold", tone === "pass" && "text-pass")}>{value}</p>
      <p className="text-[10.5px] leading-tight text-muted-foreground">{label}</p>
    </div>
  );
}

function Legend() {
  const items = [
    ["bg-amber", "changed"],
    ["bg-amber/60", "affected file"],
    ["bg-pass", "selected test"],
    ["border border-teal bg-card", "conftest / __init__"],
    ["border border-pass/50 bg-card", "test file"],
    ["bg-muted border border-muted-foreground/40", "source file"],
  ];
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 border-t px-4 py-2.5 text-[11px] text-muted-foreground">
      {items.map(([swatch, label]) => (
        <span key={label} className="flex items-center gap-1">
          <span className={cn("size-2.5 rounded-full", swatch)} /> {label}
        </span>
      ))}
    </div>
  );
}

function PasteGraph({ onLoad }: { onLoad: (g: Record<string, string[]>) => void }) {
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  return (
    <div className="border-b bg-muted/30 p-4">
      <p className="text-sm">
        Run <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">karma graph --format json</code>{" "}
        in your project and paste the output. It stays in your browser.
      </p>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={4}
        placeholder='{"app/models.py": ["app/db.py"], "tests/test_models.py": ["app/models.py"]}'
        className="mt-3 w-full rounded-md border bg-card p-2 font-mono text-xs"
      />
      {error ? <p className="mt-1 text-xs text-fail">{error}</p> : null}
      <button
        type="button"
        onClick={() => {
          try {
            const parsed: unknown = JSON.parse(text);
            if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("expected an object of file → imports");
            const graph = parsed as Record<string, unknown>;
            if (!Object.values(graph).every((v) => Array.isArray(v) && v.every((x) => typeof x === "string")))
              throw new Error("every value must be a list of file paths");
            if (Object.keys(graph).length > 800) throw new Error("more than 800 files: too many to draw here");
            onLoad(graph as Record<string, string[]>);
          } catch (e) {
            setError(e instanceof Error ? e.message : "not valid JSON");
          }
        }}
        className="mt-2 inline-flex h-8 items-center rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground"
      >
        Load graph
      </button>
    </div>
  );
}
