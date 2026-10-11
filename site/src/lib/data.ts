// Everything on the site that describes Karma comes from here. The file is generated
// from the source by `python scripts/build_site.py` (and checked by Karma's tests), so
// the site cannot drift from what the tool does.
import raw from "@/data/karma.json";

export type Option = {
  flags: string[];
  positional: boolean;
  metavar: string | null;
  kind: string;
  choices: string[] | null;
  default: string | null;
  help: string;
  group: string;
};

export type Command = {
  name: string;
  description: string;
  help: string;
  options: Option[];
  actions: Command[];
  examples: { command: string; what: string }[];
  pytest_args?: boolean;
};

export type Selection = {
  files: string[];
  targets: string[];
  file_reasons: Record<string, string[]>;
  item_reasons: Record<string, string[]>;
  narrowed_out: string[];
};

export type DemoChange = {
  file: string;
  symbol: string;
  kind: "function" | "method" | "constant";
  start: number;
  end: number;
  /** the symbol's lines before and after the change */
  before: string[];
  after: string[];
  file_level: Selection;
  function_level: Selection;
};

export type Demo = {
  files: Record<string, string>;
  symbols: Record<string, { name: string; kind: string; start: number; end: number }[]>;
  tests: Record<string, string[]>;
  total_tests: number;
  changes: DemoChange[];
};

export type Mutant = {
  mutant: string;
  failing: number;
  selected: number;
  total: number;
  missed: number;
};

export type FunctionLevelResult = {
  repo: string;
  tests: number;
  file_level_skipped: number;
  function_level_skipped: number;
  mutants: Mutant[];
};

export type Graph = {
  title?: string;
  commit?: string;
  real: boolean;
  suggest: string;
  edges: Record<string, string[]>;
};

type Data = {
  version: string;
  commands: Command[];
  action: {
    inputs: { name: string; description: string; default: string }[];
    outputs: { name: string; description: string }[];
  };
  config: { key: string; type: string; default: string; description: string }[];
  benchmarks: {
    function_level: Record<string, FunctionLevelResult>;
    prioritization: {
      repo: string;
      faults: number;
      replays: number;
      orderings: { name: string; first_failure: number; apfd: number }[];
    };
    performance: {
      repo: string;
      files: number;
      edges: number;
      machine: string;
      runs: { name: string; seconds: number }[];
    };
    flaky: {
      repo: string;
      tests: number;
      deterministic_failures: number;
      retries: number;
      misclassified: number;
    };
    quality: { tests: number; coverage: number; pythons: string[]; os: string[]; ci_jobs: number };
  };
  samples: Record<string, Graph>;
  demo: Demo;
};

export const data = raw as unknown as Data;
