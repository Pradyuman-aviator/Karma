import { ArrowRight, ShieldCheck } from "lucide-react";

import { CopyButton } from "@/components/copy-button";
import { Terminal, type TerminalLine } from "@/components/terminal";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { data } from "@/lib/data";
import { percent, plural } from "@/lib/utils";

const INSTALL = "pip install git+https://github.com/Pradyuman-aviator/Karma.git";

/** The hero terminal replays what Karma really selected for one change in the demo. */
function sessionFor(symbol: string): TerminalLine[] {
  const change = data.demo.changes.find((c) => `${c.file}::${c.symbol}` === symbol)!;
  const fn = change.function_level;
  const files = new Set(fn.targets.map((t) => t.split("::")[0]));
  const lines: TerminalLine[] = [
    { kind: "command", text: "karma run --granularity function --explain" },
    { kind: "info", text: "karma: comparing the working tree against origin/main" },
    {
      kind: "info",
      text: `karma: ${plural(fn.targets.length, "test function")} of ${data.demo.total_tests} selected, in ${plural(files.size, "file")}`,
    },
  ];
  let current = "";
  for (const target of fn.targets) {
    const [file, item] = target.split("::");
    if (file !== current) {
      lines.push({ kind: "file", text: file });
      current = file;
    }
    lines.push({ kind: "item", text: `  ::${item}` });
    const chain = fn.item_reasons[target] ?? [];
    chain.slice(1).forEach((step) => {
      lines.push({ kind: step.includes("(changed") ? "changed" : "chain", text: `      ← ${step}` });
    });
  }
  lines.push({ kind: "blank", text: "" });
  lines.push({
    kind: "result",
    text: `${"•".repeat(fn.targets.length)}  ${fn.targets.length} passed in 0.31s`,
  });
  return lines;
}

export function Hero() {
  const results = Object.values(data.benchmarks.function_level);
  const mutants = results.reduce((n, r) => n + r.mutants.length, 0);
  const missed = results.reduce((n, r) => n + r.mutants.filter((m) => m.missed).length, 0);
  const gain = Math.min(...results.map((r) => r.function_level_skipped / r.file_level_skipped));

  return (
    <section id="top" className="relative overflow-hidden">
      <div className="bg-grid pointer-events-none absolute inset-0 -z-10" />
      <div className="pointer-events-none absolute -top-40 left-1/2 -z-10 h-[480px] w-[900px] -translate-x-1/2 rounded-full bg-amber/10 blur-3xl" />
      <div className="mx-auto grid max-w-6xl items-center gap-12 px-4 pb-20 pt-14 sm:px-6 lg:grid-cols-[1.05fr_1fr] lg:pt-20">
        <div className="min-w-0">
          <Badge className="gap-1.5">
            <span className="size-1.5 rounded-full bg-amber" /> New in {data.version}: function-level
            selection
          </Badge>
          <h1 className="mt-6 text-balance text-4xl font-semibold leading-[1.05] tracking-tight sm:text-5xl lg:text-6xl">
            Run only the tests your change can break.{" "}
            <span className="text-amber">Down to the function.</span>
          </h1>
          <p className="mt-6 max-w-xl text-pretty text-lg leading-relaxed text-muted-foreground">
            Karma reads your git diff, follows every import and every name that uses what you
            changed, and runs only the affected pytest tests, each with the chain that selected
            it. Static, local and explainable: no prior runs, no tracing, no SaaS.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <Button size="lg" asChild>
              <a href="#setup">
                Get started <ArrowRight />
              </a>
            </Button>
            <Button size="lg" variant="outline" asChild>
              <a href="#demo">Try the function-level demo</a>
            </Button>
          </div>
          <div className="mt-8 flex max-w-xl items-center gap-2 rounded-lg border bg-card/70 py-1.5 pl-4 pr-1.5 font-mono text-[13px] backdrop-blur">
            <span className="select-none text-pass">$</span>
            <span className="min-w-0 flex-1 truncate">{INSTALL}</span>
            <CopyButton text={INSTALL} />
          </div>
          <p className="mt-6 flex items-start gap-2 text-sm text-muted-foreground">
            <ShieldCheck className="mt-0.5 size-4 shrink-0 text-pass" />
            <span>
              <strong className="font-medium text-foreground">
                {missed} missed failures in {mutants} broken functions
              </strong>{" "}
              across starlette and rich, while skipping {gain.toFixed(1)}× more tests than
              file-level selection.
            </span>
          </p>
        </div>
        <Terminal className="min-w-0" lines={sessionFor("billing/tax.py::tax_for")} />
      </div>
      <Stats />
    </section>
  );
}

function Stats() {
  const fl = data.benchmarks.function_level;
  const warm = data.benchmarks.performance.runs.find((r) => r.name.startsWith("Warm"));
  const stats = [
    { value: "0", label: "failing tests missed", detail: "80 functions broken in 2 real projects" },
    {
      value: percent(fl.starlette.function_level_skipped),
      label: "of starlette's tests skipped",
      detail: `vs ${percent(fl.starlette.file_level_skipped)} at file level`,
    },
    {
      value: `${warm?.seconds ?? 0.34}s`,
      label: "to rebuild the graph",
      detail: `${data.benchmarks.performance.files.toLocaleString()} files, warm cache`,
    },
    { value: "0", label: "setup or prior runs", detail: "works on the first run" },
  ];
  return (
    <div className="border-y bg-card/40 backdrop-blur">
      <dl className="mx-auto grid max-w-6xl grid-cols-2 px-4 sm:px-6 lg:grid-cols-4">
        {stats.map((s, i) => (
          <div
            key={s.label}
            className={`py-6 ${i % 2 ? "pl-6" : "pr-6"} ${i > 1 ? "border-t lg:border-t-0" : ""} lg:px-6 ${i ? "lg:border-l" : "lg:pl-0"}`}
          >
            <dt className="text-sm text-muted-foreground">{s.label}</dt>
            <dd className="mt-1 font-mono text-3xl font-semibold tracking-tight">{s.value}</dd>
            <dd className="mt-1 text-xs text-muted-foreground">{s.detail}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
