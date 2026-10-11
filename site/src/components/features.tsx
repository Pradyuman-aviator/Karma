import { Check, Gauge, Minus, MessageSquareWarning, Sparkles, Waves, X } from "lucide-react";

import { Section } from "@/components/section";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const FEATURES = [
  {
    icon: Gauge,
    title: "Likeliest failures first",
    body: "--prioritize orders the selection with a small logistic model trained on your own test history, so a broken change fails in the first seconds. Every selected test still runs.",
    code: "karma run --prioritize -- -x",
  },
  {
    icon: Waves,
    title: "Flaky-test registry",
    body: "--retries re-runs only what failed. A pass on retry is confirmed flaky and reported as a warning; karma flaky sync quarantines repeat offenders in a reviewed TOML file.",
    code: "karma run --retries 2",
  },
  {
    icon: MessageSquareWarning,
    title: "Failure diagnosis",
    body: "--diagnose points each failure at the changed lines in its traceback, with why the test ran and its history, as annotations on the pull request diff.",
    code: "karma run --diagnose",
  },
  {
    icon: Sparkles,
    title: "Optional AI explanations",
    body: "Ask Claude, a model on your machine through Ollama, or any OpenAI-compatible server for a cause and a fix. Off unless asked; --show-prompt shows exactly what would be sent.",
    code: "karma diagnose --ai anthropic",
  },
];

type Cell = boolean | "partly" | string;
const COLUMNS = ["Karma", "Changed-files plugins", "Coverage-based", "Selection SaaS", "Build systems"];
const ROWS: [string, Cell[]][] = [
  ["Follows dependencies", [true, false, true, true, true]],
  ["Selects individual test functions", [true, false, true, "varies", false]],
  ["Works on the first run", [true, true, false, false, true]],
  ["No overhead while tests run", [true, true, false, "varies", true]],
  ["Data stays on your machine", [true, true, true, false, true]],
  ["Explains why each test runs", [true, false, false, "partly", "partly"]],
  ["Adopt without changing your build", [true, true, true, true, false]],
];

function Mark({ value }: { value: Cell }) {
  if (value === true) return <Check className="mx-auto size-4 text-pass" aria-label="yes" />;
  if (value === false) return <X className="mx-auto size-4 text-muted-foreground/50" aria-label="no" />;
  return (
    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
      <Minus className="size-3" /> {value}
    </span>
  );
}

export function Features() {
  return (
    <Section
      id="features"
      eyebrow="Beyond selection"
      title="Everything after the diff, too"
      intro="Selection decides which tests run. Karma also decides in what order, recognises flaky tests, and explains failures."
    >
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {FEATURES.map((f) => (
          <Card key={f.title} className="flex flex-col">
            <CardHeader>
              <f.icon className="size-5 text-teal" />
              <CardTitle className="mt-2 text-base">{f.title}</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-1 flex-col">
              <p className="text-sm leading-relaxed text-muted-foreground">{f.body}</p>
              <code className="mt-4 rounded-md bg-terminal px-3 py-2 font-mono text-xs text-terminal-foreground">
                <span className="text-[#52c79b]">$ </span>
                {f.code}
              </code>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="mt-12 overflow-x-auto rounded-xl border bg-card">
        <table className="w-full min-w-[680px] text-sm">
          <thead>
            <tr className="border-b">
              <th className="p-4 text-left font-medium text-muted-foreground">How tools compare</th>
              {COLUMNS.map((c, i) => (
                <th
                  key={c}
                  className={i === 0 ? "bg-amber-soft p-4 font-semibold text-amber" : "p-4 font-medium text-muted-foreground"}
                >
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ROWS.map(([label, cells]) => (
              <tr key={label} className="border-b last:border-0">
                <td className="p-4">{label}</td>
                {cells.map((cell, i) => (
                  <td key={i} className={i === 0 ? "bg-amber-soft/50 p-4 text-center" : "p-4 text-center"}>
                    <Mark value={cell} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-xs text-muted-foreground">
        Changed-files plugins: e.g. pytest-picked. Coverage-based: e.g. pytest-testmon. Build
        systems: Bazel, Pants. The honest trade-off: static analysis cannot see a data file a test
        reads; mappings and a periodic full run cover that.
      </p>
    </Section>
  );
}
