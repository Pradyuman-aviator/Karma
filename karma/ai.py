"""Ask a language model to explain failures, from the evidence Karma gathered.

Nothing is sent unless asked for (``--ai`` or ``[tool.karma] ai``). What is sent is
exactly what :func:`build_prompt` returns, which ``karma diagnose --show-prompt``
prints: each failure's error and traceback, why the test ran, its history, the diff of
the changed files involved and the failing test's source, with anything that looks
like a secret redacted. With ``ollama`` (or any server on this machine), even that
stays local.

Providers speak one of two protocols: Anthropic's Messages API, or the OpenAI-style
``/chat/completions`` that Ollama, LM Studio, llama.cpp and vLLM also serve.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from karma import __version__
from karma.config import AI_PROVIDERS as PROVIDERS
from karma.diagnose import Diagnosis, Explanation, Frame
from karma.errors import KarmaError

log = logging.getLogger(__name__)

DEFAULT_MODEL = {"anthropic": "claude-sonnet-5"}
DEFAULT_URL = {
    "anthropic": "https://api.anthropic.com",
    "ollama": "http://localhost:11434/v1",
    "openai": "https://api.openai.com/v1",
}
KEY_VARIABLE = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
ANTHROPIC_VERSION = "2023-06-01"
KINDS = ("regression", "test-needs-update", "flaky", "environment", "unclear")
CONFIDENCE = ("high", "medium", "low")

TIMEOUT = 120.0  # seconds per request; local models on a laptop can be slow
MAX_TOKENS = 1500
MAX_PROMPT = 24_000  # characters of evidence per failure
MAX_TRACEBACK = 8_000
MAX_DIFF = 10_000
MAX_SOURCE_LINES = 40
MAX_CONCURRENT = 4
RETRY_DELAY = 3.0  # seconds before retrying a rate-limited or overloaded request
MAX_RETRY_DELAY = 20.0

SYSTEM = """\
You are the failure analyst of Karma, a test-impact tool for pytest. Karma ran only \
the tests affected by a code change, and a test failed. You get the evidence Karma \
collected. Find the most likely cause of the failure in the change.

Rules:
- Use only the evidence given. Cite file paths and line numbers exactly as they appear \
in it, and never invent code.
- If the evidence is not enough to be sure, say what is missing and use low confidence.
- kind is "regression" (the change broke working code), "test-needs-update" (the \
change looks intended and the test still expects the old behaviour), "flaky" (timing, \
ordering or randomness, not the change), "environment" (dependencies, configuration, \
external services) or "unclear".

Reply with one JSON object and nothing else:
{"summary": "<one sentence: what broke and why>", "cause": "<two to four sentences>", \
"fix": "<the smallest concrete fix>", "kind": "<kind>", "confidence": "high|medium|low", \
"file": "<path of the line to change, or null>", "line": <line number or null>}"""


@dataclass(frozen=True)
class Settings:
    provider: str
    model: str
    url: str
    api_key: str | None = None
    timeout: float = TIMEOUT

    @property
    def local(self) -> bool:
        host = urllib.parse.urlsplit(self.url).hostname or ""
        return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0") or host.endswith(".localhost")

    @property
    def label(self) -> str:
        if self.local:
            where = "on this machine"
        elif self.provider == "anthropic":
            where = "Anthropic"
        else:
            where = urllib.parse.urlsplit(self.url).hostname or self.url
        return f"{self.model} ({where})"


def configure(
    provider: str,
    model: str | None = None,
    url: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Validate the AI options, with actionable errors for everything that is missing."""
    env = os.environ if environ is None else environ
    if provider not in PROVIDERS:
        raise KarmaError(f"unknown AI provider {provider!r} (choose from {', '.join(PROVIDERS)})")
    model = model or DEFAULT_MODEL.get(provider)
    if not model:
        hint = " (a name from `ollama list`)" if provider == "ollama" else ""
        raise KarmaError(
            f"--ai {provider} needs a model: pass --ai-model NAME{hint} "
            "or set [tool.karma] ai-model"
        )
    if not url and provider == "ollama" and env.get("OLLAMA_HOST"):
        host = env["OLLAMA_HOST"]
        url = (host if "://" in host else f"http://{host}").rstrip("/") + "/v1"
    url = (url or DEFAULT_URL[provider]).rstrip("/")
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise KarmaError(f"--ai-url must be an http(s) URL, not {url!r}")
    variable = KEY_VARIABLE.get(provider)
    api_key = env.get(variable) if variable else None
    if provider == "anthropic" and not api_key:
        raise KarmaError(
            "--ai anthropic needs an API key: set ANTHROPIC_API_KEY "
            "(in GitHub Actions, from a repository secret)"
        )
    return Settings(provider, model, url, api_key or None)


# --------------------------------------------------------------------------- prompt


def build_prompt(diagnosis: Diagnosis, root: Path, secrets: Iterable[str] = ()) -> str:
    """Everything the model is told about one failure: exactly what is sent."""
    case = diagnosis.case
    parts = [f"Failing test: {case.nodeid} ({case.outcome.value})", f"Error: {case.message}"]
    if diagnosis.chain:
        chain = " <- ".join(diagnosis.chain)
        parts.append(f"Why Karma ran it (imports, from the test to a changed file): {chain}")
    else:
        parts.append("Why Karma ran it: not recorded (for example, a full run).")
    if diagnosis.history:
        parts.append(f"History of this test file: {diagnosis.history}")
    if diagnosis.same_failure:
        parts.append("Failing the same way: " + ", ".join(diagnosis.same_failure[:10]))
    if diagnosis.suspects:
        lines = [
            f"- {s.path}{f':{s.line}' if s.line else ''}: {s.reason}" for s in diagnosis.suspects
        ]
        parts.append(
            "Suspects Karma found (changed code linked to the failure):\n" + "\n".join(lines)
        )
    parts.append("Traceback (pytest):\n" + _trim(case.details or case.message, MAX_TRACEBACK))
    if diagnosis.diff:
        parts.append("Diff of the changed files involved:\n" + _diff(diagnosis))
    else:
        parts.append("Diff: not available (no git comparison, or no changed file involved).")
    budget = MAX_PROMPT - sum(len(p) for p in parts)
    for title, path, line, before in _sources(diagnosis):
        excerpt = _excerpt(root, path, line, before)
        if excerpt and len(excerpt) < budget:
            parts.append(f"{title} ({path}, from line {max(line - before, 1)}):\n{excerpt}")
            budget -= len(excerpt)
    return redact("\n\n".join(parts), secrets)


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = limit // 4
    omitted = f"[... {len(text) - limit} characters omitted ...]"
    return f"{text[:head]}\n{omitted}\n{text[-(limit - head) :]}"


def _diff(diagnosis: Diagnosis) -> str:
    # The suspects' hunks first: they matter most if the diff has to be cut.
    first = [s.hunk for s in diagnosis.suspects if s.hunk is not None]
    ordered = list(dict.fromkeys([*first, *diagnosis.diff]))
    chunks: list[str] = []
    size = 0
    for hunk in ordered:
        chunk = f"--- {hunk.path}\n{hunk.text}"
        if size + len(chunk) > MAX_DIFF:
            chunks.append(f"[... {len(ordered) - len(chunks)} more hunks omitted ...]")
            break
        chunks.append(chunk)
        size += len(chunk)
    return "\n".join(chunks)


def _sources(diagnosis: Diagnosis) -> list[tuple[str, str, int, int]]:
    """Source worth showing, as (title, path, line, lines before it): the failing test
    (from its ``def``), and the code around where the error was raised."""
    sources: list[tuple[str, str, int, int]] = []
    case = diagnosis.case
    if case.file and case.line:
        sources.append(("Source of the failing test", case.file, case.line, 0))
    raised: Frame | None = next((f for f in reversed(diagnosis.frames) if f.inside), None)
    if raised is not None and raised.path != case.file:
        sources.append(("Source where the error was raised", raised.path, raised.line, 10))
    return sources


def _excerpt(root: Path, path: str, line: int, before: int) -> str:
    """Numbered source lines from ``before`` lines above ``line``. With ``before=0``, the
    function whose ``def`` is at ``line``, up to the end of its body."""
    try:
        text = (root / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()
    if not 1 <= line <= len(lines):
        return ""
    start = max(line - before, 1)
    end = min(start + MAX_SOURCE_LINES - 1, len(lines))
    if before == 0:
        indent = len(lines[line - 1]) - len(lines[line - 1].lstrip())
        for number in range(line + 1, end + 1):
            code = lines[number - 1]
            closes_signature = code.lstrip().startswith((")", "]", "}"))
            if code.strip() and len(code) - len(code.lstrip()) <= indent and not closes_signature:
                end = number - 1
                break
        while end > start and not lines[end - 1].strip():
            end -= 1
    return "\n".join(f"{n:>5} | {lines[n - 1]}" for n in range(start, end + 1))


_SECRETS = [
    re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.S
    ),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),  # GitHub tokens
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(r"\bxox[abeprs]-[A-Za-z0-9-]{10,}"),  # Slack
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"),  # Anthropic, OpenAI
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),  # Google
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),  # JWT
]
# password = "hunter2hunter2", "api_key": 'abc...'  (quoted values only: code stays readable)
_QUOTED_SECRET = re.compile(
    r"(?i)((?:password|passwd|secret|token|api[_-]?key|access[_-]?key|credential)\w*"
    r"[\"']?\s*[:=]\s*)([\"'])([^\"'\n]{6,})\2"
)
# PASSWORD=..., in environment dumps and .env files (upper case: `token = f()` is code)
_ENV_SECRET = re.compile(
    r"(?m)^(\s*(?:export\s+)?\w*(?:PASSWORD|SECRET|TOKEN|API_KEY|ACCESS_KEY)\w*\s*=\s*)(\S{6,})$"
)


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """Hide what looks like a credential. Best effort: it catches common formats only."""
    for secret in secrets:
        if secret and len(secret) >= 8:
            text = text.replace(secret, "[redacted]")
    for pattern in _SECRETS:
        text = pattern.sub("[redacted]", text)
    text = _QUOTED_SECRET.sub(lambda m: f"{m[1]}{m[2]}[redacted]{m[2]}", text)
    return _ENV_SECRET.sub(lambda m: f"{m[1]}[redacted]", text)


# --------------------------------------------------------------------------- requests


def explain(
    diagnoses: Sequence[Diagnosis], settings: Settings, root: Path
) -> tuple[list[Diagnosis], list[str]]:
    """Ask the model about each diagnosis, concurrently.

    Returns the diagnoses (with an :class:`Explanation` where the model answered) and
    the distinct errors; a failed request never loses the local evidence.
    """
    if not diagnoses:
        return [], []

    def ask_one(diagnosis: Diagnosis) -> Diagnosis:
        prompt = build_prompt(diagnosis, root, secrets=[settings.api_key or ""])
        answer = ask(settings, prompt)
        return replace(diagnosis, explanation=parse_explanation(answer, settings.model))

    results: list[Diagnosis] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(MAX_CONCURRENT, len(diagnoses))) as pool:
        futures = [pool.submit(ask_one, diagnosis) for diagnosis in diagnoses]
        for diagnosis, future in zip(diagnoses, futures):
            try:
                results.append(future.result())
            except KarmaError as exc:
                errors.append(str(exc))
                results.append(diagnosis)
    return results, list(dict.fromkeys(errors))


def ask(settings: Settings, prompt: str) -> str:
    """Send one prompt and return the model's text."""
    if settings.provider == "anthropic":
        endpoint = settings.url + ("/messages" if settings.url.endswith("/v1") else "/v1/messages")
        headers = {"x-api-key": settings.api_key or "", "anthropic-version": ANTHROPIC_VERSION}
        body: dict[str, Any] = {
            "model": settings.model,
            "max_tokens": MAX_TOKENS,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": prompt}],
        }
        data = _post(endpoint, headers, body, settings)
        blocks = data.get("content") or []
        return "".join(
            str(b.get("text", ""))
            for b in blocks
            if isinstance(b, dict) and b.get("type") == "text"
        )
    headers = {"authorization": f"Bearer {settings.api_key}"} if settings.api_key else {}
    body = {
        "model": settings.model,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
    }
    data = _post(settings.url + "/chat/completions", headers, body, settings)
    choices: list[Any] = data.get("choices") or [None]
    first: dict[str, Any] = choices[0] if isinstance(choices[0], dict) else {}
    message: dict[str, Any] = first["message"] if isinstance(first.get("message"), dict) else {}
    return str(message.get("content") or "")


def _post(
    url: str, headers: Mapping[str, str], body: Mapping[str, Any], settings: Settings
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "user-agent": f"karma/{__version__}",
            **headers,
        },
        method="POST",
    )
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(request, timeout=settings.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = _error_detail(exc)
            if exc.code in (429, 500, 502, 503, 504, 529) and attempt == 1:
                delay = _retry_after(exc.headers.get("retry-after"))
                log.info("%s answered %d; retrying in %.0fs", settings.label, exc.code, delay)
                time.sleep(delay)
                continue
            raise KarmaError(_http_error(settings, exc.code, detail)) from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise KarmaError(_timeout(settings)) from None
            raise KarmaError(_unreachable(settings, exc.reason)) from None
        except (TimeoutError, socket.timeout):
            raise KarmaError(_timeout(settings)) from None
        except (ValueError, UnicodeDecodeError):
            raise KarmaError(f"{settings.label} did not answer with JSON") from None
        if not isinstance(data, dict):
            raise KarmaError(f"{settings.label} sent an unexpected response")
        return data
    raise KarmaError(f"{settings.label} is busy; try again later")  # pragma: no cover


def _error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError, OSError):
        return exc.reason if isinstance(exc.reason, str) else ""
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or "")
    return str(error or "")


def _retry_after(value: str | None) -> float:
    try:
        return min(max(float(value), 0.0), MAX_RETRY_DELAY) if value else RETRY_DELAY
    except ValueError:
        return RETRY_DELAY


def _http_error(settings: Settings, code: int, detail: str) -> str:
    message = f"{settings.label} answered HTTP {code}" + (f": {detail}" if detail else "")
    variable = KEY_VARIABLE.get(settings.provider)
    if code in (401, 403) and variable:
        message += f" (check {variable})"
    elif code == 404 and settings.provider == "ollama":
        message += f" (download the model with `ollama pull {settings.model}`)"
    elif code == 404:
        message += f" (is {settings.model!r} a model this server offers?)"
    return message


def _unreachable(settings: Settings, reason: object) -> str:
    message = f"cannot reach {settings.url}: {reason}"
    if settings.provider == "ollama":
        message += " (is Ollama running? start it with `ollama serve`)"
    return message


def _timeout(settings: Settings) -> str:
    return f"no answer from {settings.label} within {settings.timeout:.0f}s"


# --------------------------------------------------------------------------- answers


def parse_explanation(text: str, model: str) -> Explanation:
    """Read the model's JSON answer; a free-text answer is kept as the explanation."""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()  # reasoning models
    data = _json_object(cleaned)
    if data is None:
        summary, _, rest = cleaned.partition("\n")
        return Explanation(
            summary=summary.strip() or "(no answer)", cause=rest.strip(), model=model
        )
    kind = str(data.get("kind") or "").strip().lower()
    confidence = str(data.get("confidence") or "").strip().lower()
    path = data.get("file")
    return Explanation(
        summary=str(data.get("summary") or "").strip() or "(no summary)",
        cause=str(data.get("cause") or "").strip(),
        fix=str(data.get("fix") or "").strip(),
        kind=kind if kind in KINDS else "unclear",
        confidence=confidence if confidence in CONFIDENCE else "low",
        path=path.strip() if isinstance(path, str) and path.strip() not in ("", "null") else None,
        line=_positive_int(data.get("line")),
        model=model,
    )


def _json_object(text: str) -> dict[str, Any] | None:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip()) or None
    return None
