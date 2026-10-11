import { GitCompareArrows, Network, ShieldAlert, TestTubeDiagonal } from "lucide-react";

import { Section } from "@/components/section";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const STEPS = [
  {
    icon: GitCompareArrows,
    title: "What changed",
    body: "The diff from the merge base: exactly what your pull request contains, plus uncommitted work locally. With function granularity, both versions of each file are parsed and compared unit by unit, so comments, formatting and moved code are not changes.",
    detail: "billing/tax.py::rate (changed, line 4)",
  },
  {
    icon: Network,
    title: "Who uses it",
    body: "An import graph of the whole repository, cached per file. Then names: imports and re-exports, module.attr chains, star imports, mock.patch strings, fixtures by parameter name through every conftest.py, registries such as @app.route, and module state a changed function writes.",
    detail: "billing/invoice.py::total ← rate",
  },
  {
    icon: TestTubeDiagonal,
    title: "Which tests",
    body: "The test functions and classes that reach the change, handed to pytest as node ids, each with the chain that selected it. pytest's own rules apply: conftest scopes, plugins, doctests, testpaths, python_functions.",
    detail: "tests/test_invoice.py::test_total",
  },
  {
    icon: ShieldAlert,
    title: "Whole file when unsure",
    body: "Anything static analysis cannot decide safely falls back to the whole file, and a whole file affects every file importing it: exactly file-level selection. Function level can only remove tests, never add risk.",
    detail: "exec · metaclasses · autouse fixtures · importlib",
  },
];

const FALLBACKS = [
  "Module-level code changed",
  "exec(), globals(), writes to sys.modules",
  "Metaclasses and __init_subclass__",
  "Autouse fixtures, pytest_* hooks, pytestmark",
  "unittest.TestCase subclasses it cannot name",
  "import_module() with a computed name",
  "pytest_plugins and -p plugins",
  "Files that do not parse",
];

export function How() {
  return (
    <Section
      id="how"
      eyebrow="How it works"
      title="Static analysis that knows when to be careful"
      intro="Four steps, all on your machine, in about a second. Karma never runs your code to decide, so it works on the first run, with no tracing overhead and nothing to upload."
    >
      <ol className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {STEPS.map((step, i) => (
          <li key={step.title} className="min-w-0">
            <Card className="h-full transition-colors hover:border-amber/40">
              <CardHeader>
                <div className="flex items-center justify-between">
                  <span className="flex size-9 items-center justify-center rounded-lg bg-amber-soft text-amber">
                    <step.icon className="size-[18px]" />
                  </span>
                  <span className="font-mono text-xs text-muted-foreground">0{i + 1}</span>
                </div>
                <CardTitle className="mt-3">{step.title}</CardTitle>
              </CardHeader>
              <CardContent className="flex h-full flex-col">
                <p className="text-sm leading-relaxed text-muted-foreground">{step.body}</p>
                <code className="mt-4 block truncate rounded-md bg-muted px-2.5 py-1.5 font-mono text-[11px] text-foreground/80">
                  {step.detail}
                </code>
              </CardContent>
            </Card>
          </li>
        ))}
      </ol>
      <div className="mt-6 rounded-xl border border-dashed p-5">
        <p className="text-sm font-medium">Falls back to the whole file for</p>
        <ul className="mt-3 flex flex-wrap gap-2">
          {FALLBACKS.map((f) => (
            <li key={f} className="rounded-full border bg-card px-3 py-1 font-mono text-xs text-muted-foreground">
              {f}
            </li>
          ))}
        </ul>
      </div>
    </Section>
  );
}
