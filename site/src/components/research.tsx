import { ArrowUpRight, BookOpen } from "lucide-react";

import { Section } from "@/components/section";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const WORKS = [
  {
    name: "Ekstazi",
    venue: "ISSTA 2015",
    authors: "Gligoric, Eloussi, Marinov",
    finding: "File-level dependencies with checksums that ignore non-semantic changes made test selection cheap enough to win end to end.",
    karma: "Compares normalised syntax trees, so comments, formatting and moved code are not changes.",
    url: "https://users.ece.utexas.edu/~gligoric/papers/GligoricETAL15Ekstazi.pdf",
  },
  {
    name: "Static RTS study",
    venue: "FSE 2016",
    authors: "Legunsen, Hariri, Shi, Lu, Zhang, Marinov",
    finding: "Static class-level selection was nearly as good as dynamic; static method-level selection with call graphs was unsafe in 10.6% of revisions.",
    karma: "Matches names instead of building call graphs, and selects at the granularity that stays safe.",
    url: "https://mir.cs.illinois.edu/~marinov/publications/LegunsenETAL16StaticRTS.pdf",
  },
  {
    name: "HyRTS",
    venue: "ICSE 2018",
    authors: "Lingming Zhang",
    finding: "Hybrid selection: method level for method-body changes, file level for anything else. Fewer tests than file level, at lower cost.",
    karma: "Function level where it is sure; whole files for module code, class creation hooks and dynamic code.",
    url: "https://lingming.cs.illinois.edu/publications/icse2018.pdf",
  },
  {
    name: "NameRTS",
    venue: "ISSTA 2026",
    authors: "Wang, Pradel, Liu",
    finding: "For Python, linking code to the names it uses was safe on 99.6% of commits; file-level tools that miss package __init__ imports were safe on 76.6%.",
    karma: "Name-based propagation, plus every package __init__ and conftest.py on the import path.",
    url: "https://arxiv.org/abs/2605.25356",
  },
  {
    name: "Predictive Test Selection",
    venue: "ICSE-SEIP 2019 (Meta)",
    authors: "Machalica, Samylkin, Porth, Chandra",
    finding: "Learning from history halves test cost, but probabilistic selection can skip failing tests.",
    karma: "Learns from history only to order tests (--prioritize); it never skips a test because of a prediction.",
    url: "https://arxiv.org/abs/1810.05286",
  },
  {
    name: "pytest-testmon",
    venue: "open source",
    authors: "Tibor Arpas",
    finding: "Coverage-based selection is precise, but needs a recorded run, tracing overhead, and inherits coverage's blind spots.",
    karma: "Static: correct on the first run, nothing to record, no overhead while tests run.",
    url: "https://testmon.org/blog/determining-affected-tests/",
  },
];

export function Research() {
  return (
    <Section
      id="research"
      eyebrow="Built on research"
      title="Twenty years of regression test selection, applied to pytest"
      intro="Function-level selection is designed from what the research literature found to be safe, and what it found not to be."
    >
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {WORKS.map((work) => (
          <Card key={work.name} className="group flex flex-col">
            <CardHeader>
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <BookOpen className="size-3.5" /> {work.venue}
              </div>
              <CardTitle className="flex items-center justify-between gap-2">
                {work.name}
                <a
                  href={work.url}
                  className="text-muted-foreground transition-colors hover:text-amber"
                  aria-label={`Read ${work.name}`}
                >
                  <ArrowUpRight className="size-4" />
                </a>
              </CardTitle>
              <p className="text-xs text-muted-foreground">{work.authors}</p>
            </CardHeader>
            <CardContent className="flex flex-1 flex-col gap-3 text-sm">
              <p className="leading-relaxed text-muted-foreground">{work.finding}</p>
              <p className="mt-auto border-l-2 border-amber pl-3 leading-relaxed">
                <span className="font-medium text-amber">Karma: </span>
                {work.karma}
              </p>
            </CardContent>
          </Card>
        ))}
      </div>
    </Section>
  );
}
