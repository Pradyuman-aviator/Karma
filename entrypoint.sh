#!/usr/bin/env bash
# Entry point of the Karma GitHub Action (see action.yml).
#
# Runs Karma from the action's checkout with the workflow's own Python, so the
# selected tests run in the environment where the project's dependencies live.
set -euo pipefail

ACTION_PATH="${KARMA_ACTION_PATH:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

# --- interpreter ----------------------------------------------------------------
PYTHON="${INPUT_PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  if command -v python >/dev/null 2>&1; then PYTHON=python; else PYTHON=python3; fi
fi

# --- git ---------------------------------------------------------------------------
# Container jobs run as a different user than the checkout's owner; without this
# every git command fails with "detected dubious ownership".
if [[ -n "${GITHUB_WORKSPACE:-}" ]]; then
  git config --global --add safe.directory "$GITHUB_WORKSPACE" 2>/dev/null || true
fi

# Base ref: explicit input, else the PR's target branch, else the commit before a push.
BASE="${INPUT_BASE_BRANCH:-}"
if [[ -z "$BASE" ]]; then
  if [[ -n "${GITHUB_BASE_REF:-}" ]]; then
    BASE="origin/${GITHUB_BASE_REF}"
  elif [[ -n "${KARMA_EVENT_BEFORE:-}" && ! "${KARMA_EVENT_BEFORE}" =~ ^0+$ ]]; then
    BASE="$KARMA_EVENT_BEFORE"
  elif [[ -n "${KARMA_DEFAULT_BRANCH:-}" ]]; then
    BASE="origin/${KARMA_DEFAULT_BRANCH}"
  fi
fi
# A ref starting with "-" would be parsed by git as an option (e.g. --upload-pack=...).
if [[ "$BASE" == -* ]]; then
  echo "::error title=Karma::invalid base-branch '$BASE'"
  exit 2
fi

has_commit() { git rev-parse --verify --quiet "$1^{commit}" >/dev/null 2>&1; }

# actions/checkout fetches a single commit by default. Fetch what the diff needs
# rather than failing (or, worse, silently selecting nothing).
if [[ -n "$BASE" ]] && ! has_commit "$BASE"; then
  echo "::group::Karma: fetching base ref $BASE"
  branch="${BASE#origin/}"
  if [[ "$BASE" == origin/* ]]; then
    git fetch --no-tags origin "+refs/heads/${branch}:refs/remotes/origin/${branch}" || true
  elif git fetch --no-tags origin "+refs/heads/${branch}:refs/remotes/origin/${branch}"; then
    BASE="origin/${branch}" # a bare branch name such as `main` only exists on the remote
  else
    git fetch --no-tags origin "$BASE" || true # a commit SHA
  fi
  echo "::endgroup::"
fi
if [[ -n "$BASE" ]] && [[ "$(git rev-parse --is-shallow-repository 2>/dev/null)" == "true" ]] \
  && ! git merge-base "$BASE" HEAD >/dev/null 2>&1; then
  echo "::notice title=Karma::Shallow clone: fetching history to find the merge base. Use 'fetch-depth: 0' with actions/checkout to skip this step."
  echo "::group::Karma: fetching history"
  git fetch --no-tags --unshallow origin || true
  echo "::endgroup::"
fi

# --- run -----------------------------------------------------------------------------
# Split inputs like a shell would (quotes respected, multi-line YAML values allowed).
# Python's shlex does it portably; bash 3.2 on macOS has no `readarray -d`.
split_words() {
  "$PYTHON" -c 'import shlex, sys; sys.stdout.write("".join(w + "\0" for w in shlex.split(sys.argv[1])))' "$1"
}
for input_name in INPUT_ARGS INPUT_PYTEST_ARGS; do
  if ! "$PYTHON" -c 'import shlex, sys; shlex.split(sys.argv[1])' "${!input_name:-}" 2>/dev/null; then
    echo "::error title=Karma::cannot parse the ${input_name#INPUT_} input (unbalanced quotes?)"
    exit 2
  fi
done
EXTRA_ARGS=()
while IFS= read -r -d '' word; do EXTRA_ARGS+=("$word"); done < <(split_words "${INPUT_ARGS:-}")
PYTEST_ARGS=()
while IFS= read -r -d '' word; do PYTEST_ARGS+=("$word"); done < <(split_words "${INPUT_PYTEST_ARGS:-}")

cmd=("$PYTHON" "$ACTION_PATH/cli.py" "${INPUT_COMMAND:-run}" --ci --head HEAD
  --on-git-error "${INPUT_ON_GIT_ERROR:-fail}")
if [[ -n "$BASE" ]]; then cmd+=(--base "$BASE"); fi
# ${arr[@]+...} keeps `set -u` happy with empty arrays on old bash versions.
cmd+=(${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"})
if [[ ${#PYTEST_ARGS[@]} -gt 0 ]]; then cmd+=(-- "${PYTEST_ARGS[@]}"); fi

echo "karma: ${cmd[*]}"
exec "${cmd[@]}"
