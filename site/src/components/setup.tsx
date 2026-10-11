import { CodeTabs } from "@/components/code-tabs";
import { Section } from "@/components/section";
import { highlight } from "@/lib/highlight";

const SNIPPETS = [
  {
    id: "cli",
    label: "Command line",
    lang: "bash",
    code: `pip install git+https://github.com/Pradyuman-aviator/Karma.git

karma run                          # tests affected by your branch + uncommitted work
karma run --granularity function   # ...only the test functions that use the change
karma run --explain                # ...and show why each one runs
karma select                       # just print them:  pytest $(karma select)
karma run -- -x -n auto            # everything after -- goes to pytest`,
  },
  {
    id: "github",
    label: "GitHub Actions",
    lang: "yaml",
    code: `jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v6
        with:
          python-version: "3.13"
      - run: pip install -e ".[test]"
      - uses: Pradyuman-aviator/Karma@main
        with:
          granularity: function
          prioritize: "true"
          diagnose: "true"`,
  },
  {
    id: "gitlab",
    label: "GitLab CI",
    lang: "yaml",
    code: `test:
  image: python:3.13
  variables:
    GIT_DEPTH: 0
  script:
    - pip install -e ".[test]" git+https://github.com/Pradyuman-aviator/Karma.git
    - karma run --granularity function --base "origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME"`,
  },
  {
    id: "precommit",
    label: "pre-commit",
    lang: "yaml",
    code: `# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: karma
        name: affected tests
        entry: karma run --staged --granularity function
        language: system
        pass_filenames: false
        stages: [pre-push]`,
  },
  {
    id: "config",
    label: "pyproject.toml",
    lang: "toml",
    code: `[tool.karma]
granularity = "function"   # run only the test functions using a change
prioritize = true          # likeliest failures first
retries = 2                # a pass on retry means flaky, not broken
diagnose = true            # point failures at the change behind them

[tool.karma.mappings]      # dependencies imports cannot express
"tests/fixtures/*.json" = ["tests/test_loader.py"]`,
  },
];

export async function Setup() {
  const tabs = await Promise.all(
    SNIPPETS.map(async (s) => ({ ...s, html: await highlight(s.code, s.lang) })),
  );
  return (
    <Section
      id="setup"
      eyebrow="Get started"
      title="One command locally. One step in CI."
      intro="Karma runs in your own Python environment, so the tests it selects can import your dependencies. On a pull request the Action posts a job summary with why each test ran, and annotates failures on the diff."
    >
      <CodeTabs tabs={tabs} />
    </Section>
  );
}
