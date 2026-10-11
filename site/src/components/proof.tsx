import { Section } from "@/components/section";
import { MutantStrip } from "@/components/mutant-strip";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { data } from "@/lib/data";
import { percent } from "@/lib/utils";

export function Proof() {
  const { function_level, prioritization, performance, flaky, quality } = data.benchmarks;
  const projects = Object.entries(function_level);
  const slowest = Math.max(...performance.runs.map((r) => r.seconds));
  const random = prioritization.orderings[0];

  return (
    <Section
      id="proof"
      eyebrow="Proof"
      title="Measured by breaking real code"
      intro={
        <>
          A test selector is only worth using if it never skips a test that would fail. So we
          broke {projects.reduce((n, [, r]) => n + r.mutants.length, 0)} randomly chosen functions
          in two well-known projects, one at a time, ran the full suite, and checked every failing
          test against Karma&apos;s selection. Run the same check on your code with{" "}
          <code className="font-mono text-sm">scripts/safety_check.py</code>.
        </>
      }
    >
      <div className="grid gap-4 lg:grid-cols-2">
        {projects.map(([name, result]) => (
          <Card key={name}>
            <CardHeader>
              <div className="flex items-start justify-between gap-4">
                <div>
                  <CardTitle className="font-mono text-base">{result.repo}</CardTitle>
                  <CardDescription>
                    {result.tests.toLocaleString()} tests · {result.mutants.length} functions broken
                  </CardDescription>
                </div>
                <span className="rounded-full bg-pass/12 px-2.5 py-1 text-xs font-semibold text-pass">
                  {result.mutants.filter((m) => m.missed).length} missed
                </span>
              </div>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">Average share of tests skipped</p>
              <Bar label="File level" value={result.file_level_skipped} tone="muted" />
              <Bar label="Function level" value={result.function_level_skipped} tone="amber" />
              <p className="mt-6 text-sm text-muted-foreground">
                Each bar is one broken function: the share of the suite Karma ran for it
              </p>
              <MutantStrip mutants={result.mutants} />
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="mt-4 grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Failures first</CardTitle>
            <CardDescription>
              {prioritization.faults} real faults in {prioritization.repo}, replayed{" "}
              {prioritization.replays}×. Average position of the first failing test.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2.5">
            {prioritization.orderings.map((o) => (
              <Row
                key={o.name}
                label={o.name}
                value={o.first_failure / random.first_failure}
                text={`#${o.first_failure.toFixed(1)}`}
                highlight={o.name.startsWith("Karma")}
              />
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Fast on big repositories</CardTitle>
            <CardDescription>
              {performance.files.toLocaleString()} files and {performance.edges.toLocaleString()} import edges ({performance.repo}).
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2.5">
            {performance.runs.map((r) => (
              <Row
                key={r.name}
                label={r.name}
                value={r.seconds / slowest}
                text={`${r.seconds}s`}
                highlight={r.name.startsWith("Warm")}
              />
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Built to be trusted</CardTitle>
            <CardDescription>Karma tests itself, on every platform it supports.</CardDescription>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-2 gap-4">
              <Fact value={`${quality.tests}+`} label="tests" />
              <Fact value={`${quality.coverage}%`} label="coverage" />
              <Fact value={`${quality.pythons[0]}–${quality.pythons.at(-1)}`} label="Python" />
              <Fact value={`${flaky.misclassified}`} label={`flaky misclassified of ${flaky.deterministic_failures}`} />
            </dl>
          </CardContent>
        </Card>
      </div>
    </Section>
  );
}

function Bar({ label, value, tone }: { label: string; value: number; tone: "muted" | "amber" }) {
  return (
    <div className="mt-3">
      <div className="flex justify-between text-sm">
        <span className={tone === "amber" ? "font-medium" : "text-muted-foreground"}>{label}</span>
        <span className="font-mono font-semibold">{percent(value, 1)}</span>
      </div>
      <div className="mt-1.5 h-2.5 overflow-hidden rounded-full bg-muted">
        <div
          className={tone === "amber" ? "h-full rounded-full bg-amber" : "h-full rounded-full bg-muted-foreground/40"}
          style={{ width: percent(value, 1) }}
        />
      </div>
    </div>
  );
}

function Row({
  label,
  value,
  text,
  highlight,
}: {
  label: string;
  value: number;
  text: string;
  highlight?: boolean;
}) {
  return (
    <div>
      <div className="flex justify-between gap-3 text-xs">
        <span className={highlight ? "font-medium" : "text-muted-foreground"}>{label}</span>
        <span className="font-mono">{text}</span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className={highlight ? "h-full rounded-full bg-teal" : "h-full rounded-full bg-muted-foreground/35"}
          style={{ width: `${Math.max(value * 100, 2)}%` }}
        />
      </div>
    </div>
  );
}

function Fact({ value, label }: { value: string; label: string }) {
  return (
    <div>
      <dd className="font-mono text-2xl font-semibold">{value}</dd>
      <dt className="text-xs text-muted-foreground">{label}</dt>
    </div>
  );
}
