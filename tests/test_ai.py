"""AI explanations of failures (karma.ai), against a local stand-in for the APIs."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from karma import ai
from karma.diagnose import Diagnosis, Frame, Hunk, Suspect
from karma.errors import KarmaError
from karma.runner import Outcome, TestCase
from tests.helpers import FakeAPI, Reply, anthropic_reply, openai_reply

ANSWER = {
    "summary": "RATE was lowered to 0.19.",
    "cause": "The test expects 21% VAT.",
    "fix": "Restore RATE = 0.21.",
    "kind": "regression",
    "confidence": "high",
    "file": "billing/tax.py",
    "line": 1,
}
HUNK = Hunk("billing/tax.py", 1, 2, "@@ -1 +1,2 @@\n-RATE = 0.21\n+RATE = 0.19\n+X = 1")


def diagnosis(nodeid: str = "tests/test_tax.py::test_vat", details: str = "") -> Diagnosis:
    case = TestCase(
        nodeid,
        Outcome.FAILED,
        message="assert 119.0 == 121.0",
        details=details or "E  assert 119.0 == 121.0\n\ntests/test_tax.py:5: AssertionError",
        file="tests/test_tax.py",
        line=4,
    )
    return Diagnosis(
        case=case,
        chain=("tests/test_tax.py", "billing/tax.py"),
        history="first failure in 12 recorded runs",
        suspects=(Suspect("billing/tax.py", 1, "changed; the test imports it", HUNK),),
        frames=(Frame("tests/test_tax.py", 5),),
        diff=(HUNK,),
    )


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai, "RETRY_DELAY", 0.0)


# --------------------------------------------------------------------------- settings


class TestConfigure:
    def test_anthropic_defaults(self) -> None:
        settings = ai.configure("anthropic", environ={"ANTHROPIC_API_KEY": "sk-ant-x"})
        assert settings.model == "claude-sonnet-5"
        assert settings.url == "https://api.anthropic.com"
        assert settings.api_key == "sk-ant-x"
        assert not settings.local
        assert settings.label == "claude-sonnet-5 (Anthropic)"

    def test_anthropic_needs_a_key(self) -> None:
        with pytest.raises(KarmaError, match="set ANTHROPIC_API_KEY"):
            ai.configure("anthropic", environ={})

    def test_local_models_need_a_name_but_no_key(self) -> None:
        with pytest.raises(KarmaError, match="ollama list"):
            ai.configure("ollama", environ={})
        settings = ai.configure("ollama", "qwen2.5-coder", environ={})
        assert settings.url == "http://localhost:11434/v1"
        assert settings.api_key is None
        assert settings.local
        assert settings.label == "qwen2.5-coder (on this machine)"

    def test_ollama_host(self) -> None:
        settings = ai.configure("ollama", "m", environ={"OLLAMA_HOST": "10.0.0.5:11434"})
        assert settings.url == "http://10.0.0.5:11434/v1"
        assert settings.label == "m (10.0.0.5)"

    def test_openai_compatible_servers(self) -> None:
        settings = ai.configure("openai", "gpt-x", "https://llm.example.com/v1/", environ={})
        assert settings.url == "https://llm.example.com/v1"
        assert settings.api_key is None  # e.g. a self-hosted server
        keyed = ai.configure("openai", "gpt-x", environ={"OPENAI_API_KEY": "sk-o"})
        assert keyed.api_key == "sk-o"

    @pytest.mark.parametrize(
        ("provider", "url", "message"),
        [("gemini", None, "unknown AI provider"), ("ollama", "ftp://x", "http\\(s\\) URL")],
    )
    def test_invalid(self, provider: str, url: str | None, message: str) -> None:
        with pytest.raises(KarmaError, match=message):
            ai.configure(provider, "m", url, environ={})


# --------------------------------------------------------------------------- prompt


class TestPrompt:
    def test_contains_the_evidence(self, tmp_path: Path) -> None:
        test = tmp_path / "tests" / "test_tax.py"
        test.parent.mkdir()
        test.write_text(
            "import x\n\n\n"
            "def test_vat():\n    assert total(100) == 121.0\n\n\n"
            "def test_other():\n    pass\n",
            encoding="utf-8",
        )
        prompt = ai.build_prompt(diagnosis(), tmp_path)
        for expected in (
            "Failing test: tests/test_tax.py::test_vat (failed)",
            "Error: assert 119.0 == 121.0",
            "tests/test_tax.py <- billing/tax.py",
            "History of this test file: first failure in 12 recorded runs",
            "- billing/tax.py:1: changed; the test imports it",
            "tests/test_tax.py:5: AssertionError",
            "+RATE = 0.19",
            "    4 | def test_vat():",
            "    5 |     assert total(100) == 121.0",
        ):
            assert expected in prompt
        assert "test_other" not in prompt  # the excerpt stops at the end of the test

    def test_missing_evidence_is_said_so(self, tmp_path: Path) -> None:
        bare = Diagnosis(case=TestCase("t.py::t", Outcome.ERROR, message="boom"))
        prompt = ai.build_prompt(bare, tmp_path)
        assert "Why Karma ran it: not recorded" in prompt
        assert "Diff: not available" in prompt

    def test_long_tracebacks_are_trimmed(self, tmp_path: Path) -> None:
        details = "".join(f"frame {i}\n" for i in range(5000)) + "E   ValueError: the end"
        prompt = ai.build_prompt(diagnosis(details=details), tmp_path)
        assert "characters omitted" in prompt
        assert "E   ValueError: the end" in prompt
        assert len(prompt) < ai.MAX_PROMPT

    def test_secrets_never_reach_the_prompt(self, tmp_path: Path) -> None:
        details = (
            "E   AssertionError: token ghp_" + "a" * 36 + " rejected\n"
            'password = "hunter2hunter2"\n'
            "API_KEY=abcdef123456\n"
            "the key sk-ant-api03-" + "x" * 30 + "\n"
            "my-own-key-1234567"
        )
        prompt = ai.build_prompt(
            diagnosis(details=details), tmp_path, secrets=["my-own-key-1234567"]
        )
        assert "ghp_" not in prompt
        assert "hunter2" not in prompt
        assert "abcdef123456" not in prompt
        assert "sk-ant-api03" not in prompt
        assert "my-own-key-1234567" not in prompt
        assert 'password = "[redacted]"' in prompt


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("AKIAABCDEFGHIJKLMNOP", "[redacted]"),
        ("-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----", "[redacted]"),
        ("Bearer eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0NTY3.SflKxwRJSMeKKF2QT4", "Bearer [redacted]"),
        ("xoxb-1234567890-abcdefghij", "[redacted]"),
        ("AIza" + "b" * 35, "[redacted]"),
        ("{'api_key': 'abcdef1234'}", "{'api_key': '[redacted]'}"),
        ("token = get_token(user)", "token = get_token(user)"),  # code stays readable
    ],
)
def test_redact(text: str, expected: str) -> None:
    assert ai.redact(text) == expected


# --------------------------------------------------------------------------- answers


class TestParseExplanation:
    def test_json_in_a_code_fence(self) -> None:
        explanation = ai.parse_explanation("```json\n" + json.dumps(ANSWER) + "\n```", "m")
        assert explanation.summary == "RATE was lowered to 0.19."
        assert (explanation.kind, explanation.confidence) == ("regression", "high")
        assert (explanation.path, explanation.line, explanation.model) == ("billing/tax.py", 1, "m")

    def test_json_with_prose_and_reasoning_around_it(self) -> None:
        text = (
            "<think>hmm {not this}</think>Here you go:\n" + json.dumps(ANSWER) + "\nHope it helps"
        )
        assert ai.parse_explanation(text, "m").fix == "Restore RATE = 0.21."

    def test_unknown_values_are_normalised(self) -> None:
        answer = {**ANSWER, "kind": "Bug!", "confidence": "certain", "file": "null", "line": "12"}
        explanation = ai.parse_explanation(json.dumps(answer), "m")
        assert (explanation.kind, explanation.confidence) == ("unclear", "low")
        assert (explanation.path, explanation.line) == (None, 12)
        for line in (0, -3, True, "x", None):
            assert ai.parse_explanation(json.dumps({**ANSWER, "line": line}), "m").line is None

    def test_free_text_is_kept(self) -> None:
        explanation = ai.parse_explanation("The rate changed.\nRestore it.", "m")
        assert (explanation.summary, explanation.cause) == ("The rate changed.", "Restore it.")
        assert explanation.confidence == "low"
        assert ai.parse_explanation("", "m").summary == "(no answer)"
        assert ai.parse_explanation("[1, 2]", "m").summary == "[1, 2]"


# --------------------------------------------------------------------------- requests


class TestRequests:
    def test_anthropic(self, api: FakeAPI, tmp_path: Path) -> None:
        api.answer(anthropic_reply(json.dumps(ANSWER)))
        settings = ai.configure("anthropic", url=api.url, environ={"ANTHROPIC_API_KEY": "sk-ant-k"})
        (result,), errors = ai.explain([diagnosis()], settings, tmp_path)
        assert errors == []
        assert result.explanation is not None
        assert result.explanation.summary == "RATE was lowered to 0.19."
        (request,) = api.requests
        assert request["path"] == "/v1/messages"
        assert request["headers"]["x-api-key"] == "sk-ant-k"
        assert request["headers"]["anthropic-version"] == "2023-06-01"
        assert request["body"]["model"] == "claude-sonnet-5"
        assert request["body"]["system"] == ai.SYSTEM
        assert (
            "Failing test: tests/test_tax.py::test_vat" in request["body"]["messages"][0]["content"]
        )

    def test_anthropic_url_with_version(self, api: FakeAPI, tmp_path: Path) -> None:
        api.answer(anthropic_reply(json.dumps(ANSWER)))
        settings = ai.configure(
            "anthropic", url=api.url + "/v1", environ={"ANTHROPIC_API_KEY": "k"}
        )
        ai.explain([diagnosis()], settings, tmp_path)
        assert api.requests[0]["path"] == "/v1/messages"

    def test_openai_compatible(self, api: FakeAPI, tmp_path: Path) -> None:
        api.answer(openai_reply(json.dumps(ANSWER)))
        settings = ai.configure("openai", "local-model", api.url + "/v1", environ={})
        (result,), errors = ai.explain([diagnosis()], settings, tmp_path)
        assert not errors
        assert result.explanation is not None
        assert result.explanation.model == "local-model"
        (request,) = api.requests
        assert request["path"] == "/v1/chat/completions"
        assert "authorization" not in request["headers"]  # no key, no header
        assert [m["role"] for m in request["body"]["messages"]] == ["system", "user"]

        keyed = ai.configure("openai", "m", api.url, environ={"OPENAI_API_KEY": "sk-o"})
        ai.explain([diagnosis()], keyed, tmp_path)
        assert api.requests[1]["headers"]["authorization"] == "Bearer sk-o"

    def test_several_failures_are_asked_about_concurrently(
        self, api: FakeAPI, tmp_path: Path
    ) -> None:
        api.answer(
            Reply(body={"choices": [{"message": {"content": json.dumps(ANSWER)}}]}, delay=0.3)
        )
        settings = ai.configure("ollama", "m", api.url, environ={})
        started = time.monotonic()
        results, errors = ai.explain(
            [diagnosis(f"t.py::t{i}") for i in range(3)], settings, tmp_path
        )
        assert time.monotonic() - started < 0.85  # three 0.3 s answers, in parallel
        assert not errors
        assert all(r.explanation is not None for r in results)
        assert [r.case.nodeid for r in results] == ["t.py::t0", "t.py::t1", "t.py::t2"]

    def test_rate_limits_are_retried_once(self, api: FakeAPI, tmp_path: Path) -> None:
        api.answer(
            Reply(
                status=429, body={"error": {"message": "slow down"}}, headers={"retry-after": "0"}
            ),
            anthropic_reply(json.dumps(ANSWER)),
        )
        settings = ai.configure("anthropic", url=api.url, environ={"ANTHROPIC_API_KEY": "k"})
        (result,), errors = ai.explain([diagnosis()], settings, tmp_path)
        assert not errors
        assert result.explanation is not None
        assert len(api.requests) == 2

    @pytest.mark.parametrize(
        ("provider", "status", "hint"),
        [
            ("anthropic", 401, "check ANTHROPIC_API_KEY"),
            ("ollama", 404, "ollama pull m"),
            ("openai", 404, "a model this server offers"),
            ("anthropic", 529, "HTTP 529: overloaded"),
        ],
    )
    def test_errors_explain_what_to_do(
        self, api: FakeAPI, tmp_path: Path, provider: str, status: int, hint: str
    ) -> None:
        api.answer(Reply(status=status, body={"type": "error", "error": {"message": "overloaded"}}))
        settings = ai.configure(provider, "m", api.url, environ={"ANTHROPIC_API_KEY": "k"})
        (result,), errors = ai.explain([diagnosis()], settings, tmp_path)
        assert result.explanation is None  # the local evidence is kept
        (error,) = errors
        assert hint in error

    def test_unreachable_local_server(self, tmp_path: Path) -> None:
        settings = ai.configure("ollama", "m", "http://127.0.0.1:9", environ={})
        _, (error,) = ai.explain([diagnosis()], settings, tmp_path)
        assert "cannot reach http://127.0.0.1:9" in error
        assert "is Ollama running?" in error

    def test_timeout(self, api: FakeAPI, tmp_path: Path) -> None:
        api.answer(Reply(body={}, delay=1.0))
        settings = ai.Settings("ollama", "m", api.url, timeout=0.2)
        _, (error,) = ai.explain([diagnosis()], settings, tmp_path)
        assert error == "no answer from m (on this machine) within 0s"

    def test_malformed_answers(self, api: FakeAPI, tmp_path: Path) -> None:
        settings = ai.configure("ollama", "m", api.url, environ={})
        api.answer(Reply(body=b"<html>not json</html>"))
        assert ai.explain([diagnosis()], settings, tmp_path)[1] == [
            "m (on this machine) did not answer with JSON"
        ]
        api.answer(Reply(body=[1, 2]))
        assert ai.explain([diagnosis()], settings, tmp_path)[1] == [
            "m (on this machine) sent an unexpected response"
        ]
        api.answer(Reply(body={"choices": []}))
        (result,), errors = ai.explain([diagnosis()], settings, tmp_path)
        assert not errors
        assert result.explanation is not None
        assert result.explanation.summary == "(no answer)"

    def test_nothing_to_explain(self, tmp_path: Path) -> None:
        settings = ai.configure("ollama", "m", environ={})
        assert ai.explain([], settings, tmp_path) == ([], [])
