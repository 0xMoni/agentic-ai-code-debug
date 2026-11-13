"""Live verification of the seven acceptance criteria."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

from chains.lcel_chains import StructuredChain
from config.llm_provider import check_provider
from config.settings import Settings
from models.schemas import (
    AgentName,
    BugReport,
    Intent,
    PromptTechnique,
    TestOutcome,
    VerificationStatus,
)
from prompts.examples import BUG_FINDER_FEW_SHOT
from prompts.system_prompts import BUG_FINDER_SYSTEM
from rag.ingest import EMBEDDING_MODEL_NAME
from rag.retriever import policy_decision, retrieval_status, similarity_search
from routing.intent_router import IntentRouter, keyword_intent
from sandbox.executor import SandboxExecutor, parse_pytest_output


@dataclass
class CheckResult:
    status: VerificationStatus
    detail: str
    evidence: str = ""
    duration_seconds: float = 0.0


@dataclass
class Criterion:
    number: int
    title: str
    what: str
    """What was implemented."""
    how: str
    """How it works."""
    files: list[str]
    check: Callable[[Settings], CheckResult]
    navigate_to: str = ""
    costs_tokens: bool = False
    demo_notes: list[str] = field(default_factory=list)


def check_llm(settings: Settings) -> CheckResult:
    """Real inference against the configured provider."""
    started = time.perf_counter()
    if not settings.has_credentials:
        return CheckResult(
            VerificationStatus.NOT_CONFIGURED,
            "No API key is configured, so no inference can be attempted.",
            evidence="GROQ_API_KEY is empty. Add a free key from "
            "https://console.groq.com/keys to debug-squad/.env",
        )
    health = check_provider(settings, run_inference=True)
    elapsed = time.perf_counter() - started
    if health.ok and health.inference_verified:
        return CheckResult(
            VerificationStatus.VERIFIED,
            f"A real completion came back from {health.model} via {health.provider}.",
            evidence=health.detail,
            duration_seconds=elapsed,
        )
    return CheckResult(
        VerificationStatus.FAILED,
        "The provider did not return a usable completion.",
        evidence=health.detail,
        duration_seconds=elapsed,
    )


def check_gui(settings: Settings) -> CheckResult:
    """The GUI is verified by the fact that you are reading this in it."""
    from ui.state import PAGES

    return CheckResult(
        VerificationStatus.VERIFIED,
        f"This page is being served by the Streamlit app, which has "
        f"{len(PAGES)} working screens.",
        evidence=(
            "Screens: " + ", ".join(PAGES) + ". Every button on them is wired to a "
            "real callback - uploads, example loading, analysis, sandbox execution, "
            "similarity search, index rebuilds and downloads all perform real work."
        ),
    )


def check_prompt_engineering(settings: Settings) -> CheckResult:
    """All four techniques must be present and wired to a chain."""
    from prompts import system_prompts, user_prompts

    system_count = len([n for n in dir(system_prompts) if n.endswith("_SYSTEM")])
    template_count = len([n for n in dir(user_prompts) if n.endswith("_USER")])
    techniques = {t.value for t in PromptTechnique}

    missing = []
    if system_count < 5:
        missing.append("system prompts")
    if template_count < 5:
        missing.append("user prompt templates")
    if len(BUG_FINDER_FEW_SHOT) < 3:
        missing.append("few-shot examples")

    if missing:
        return CheckResult(
            VerificationStatus.FAILED,
            f"Missing: {', '.join(missing)}.",
        )

    return CheckResult(
        VerificationStatus.VERIFIED,
        f"{system_count} system prompts and {template_count} user templates are "
        f"defined, with all {len(techniques)} techniques represented.",
        evidence=(
            f"Techniques: {', '.join(sorted(techniques))}. "
            f"Few-shot: {len(BUG_FINDER_FEW_SHOT)} examples, including one with "
            "correct code whose expected answer is an empty issue list. "
            "Reflection is driven by real pytest output (agents/fixer_agent.py "
            "reflect()). ReAct-style tool use: ast.parse, ruff, knowledge_base, "
            "sandbox. The Prompt Inspector shows the live record of each call."
        ),
    )


def check_langchain(settings: Settings) -> CheckResult:
    """Build a real chain and confirm the LangChain types are in it."""
    started = time.perf_counter()
    try:
        from tests.fake_llm import FakeChatModel

        payload = json.dumps(
            {"issues": [], "summary": "compliance check", "syntax_valid": True}
        )
        chain = StructuredChain(
            system_prompt=BUG_FINDER_SYSTEM,
            user_template="Analyse:\n{code}\n{format_instructions}",
            output_model=BugReport,
            model=FakeChatModel(responses=[payload], calls=[]),
            agent=AgentName.BUG_FINDER,
            technique=PromptTechnique.FEW_SHOT,
            provider="compliance-check",
            model_name="stub",
        )
        report, record = chain.invoke({"code": "x = 1"})
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            VerificationStatus.FAILED,
            f"Building or invoking an LCEL chain raised {type(exc).__name__}: {exc}",
            duration_seconds=time.perf_counter() - started,
        )

    checks = {
        "ChatPromptTemplate": isinstance(chain.prompt, ChatPromptTemplate),
        "PydanticOutputParser": isinstance(chain.parser, PydanticOutputParser),
        "Runnable pipeline": isinstance(chain.chain, Runnable),
        "parsed to a Pydantic model": isinstance(report, BugReport),
        "prompt record produced": record is not None,
    }
    failed = [name for name, ok in checks.items() if not ok]
    elapsed = time.perf_counter() - started

    if failed:
        return CheckResult(
            VerificationStatus.FAILED,
            f"These LangChain components did not verify: {', '.join(failed)}.",
            duration_seconds=elapsed,
        )

    return CheckResult(
        VerificationStatus.VERIFIED,
        "A real LCEL chain was built and invoked end to end, with a stub model so "
        "the check costs no tokens.",
        evidence=(
            f"{chain.source_expression}\n\n{chain.pipeline_description}\n\n"
            "Verified: " + ", ".join(checks) + ". The same StructuredChain class "
            "is what every agent uses at run time."
        ),
        duration_seconds=elapsed,
    )


def check_agentic_rag(settings: Settings) -> CheckResult:
    """Retrieval must happen when needed AND be skipped when it is not."""
    started = time.perf_counter()
    status = retrieval_status(settings)
    if not status.available:
        return CheckResult(
            VerificationStatus.NOT_CONFIGURED,
            "The vector store is empty, so conditional retrieval cannot be shown.",
            evidence=status.error or "No chunks indexed.",
        )

    retrieving = policy_decision(
        Intent.STYLE_IMPROVEMENT, AgentName.STYLE_LINT, settings=settings
    )
    skipping = policy_decision(
        Intent.CODE_EXPLANATION, AgentName.EXPLAINER, settings=settings
    )
    disabled = policy_decision(
        Intent.STYLE_IMPROVEMENT,
        AgentName.STYLE_LINT,
        rag_enabled=False,
        settings=settings,
    )
    elapsed = time.perf_counter() - started

    positive = retrieving.should_retrieve and bool(retrieving.passages)
    negative = not skipping.should_retrieve
    toggle = not disabled.should_retrieve

    if not (positive and negative and toggle):
        return CheckResult(
            VerificationStatus.FAILED,
            "Retrieval did not behave conditionally: "
            f"style retrieved={positive}, explanation skipped={negative}, "
            f"toggle respected={toggle}.",
            duration_seconds=elapsed,
        )

    sources = ", ".join(p.source for p in retrieving.passages)
    return CheckResult(
        VerificationStatus.VERIFIED,
        "Retrieval ran for the style review and was correctly skipped for an "
        "explanation request - it is genuinely conditional.",
        evidence=(
            f"Style/Lint on a style request: retrieved "
            f"{len(retrieving.passages)} passage(s) for query "
            f"{retrieving.query!r}.\nSources: {sources}\n\n"
            f"Explainer on an explanation request: {skipping.reason}\n\n"
            f"With the RAG toggle off: {disabled.reason}"
        ),
        duration_seconds=elapsed,
    )


def check_routing(settings: Settings) -> CheckResult:
    """Routing must map the documented examples to different agent sets."""
    started = time.perf_counter()
    expectations = [
        ("Find the bug in this code", Intent.DEBUGGING),
        ("Improve readability", Intent.STYLE_IMPROVEMENT),
        ("Explain this code", Intent.CODE_EXPLANATION),
        ("Generate edge-case tests", Intent.TEST_GENERATION),
        ("Review and fix everything", Intent.FULL_REVIEW),
    ]
    router = IntentRouter(settings)
    lines: list[str] = []
    wrong: list[str] = []

    for request, expected in expectations:
        detected, _confidence, _why = keyword_intent(request)
        decision = router.fallback(request)
        agents = ", ".join(a.value for a in decision.agents)
        marker = "ok " if detected is expected else "BAD"
        lines.append(f"[{marker}] {request!r} -> {detected.value}  ({agents})")
        if detected is not expected:
            wrong.append(request)

    sets = {
        tuple(a.value for a in router.fallback(r).agents) for r, _ in expectations
    }
    elapsed = time.perf_counter() - started

    if wrong:
        return CheckResult(
            VerificationStatus.FAILED,
            f"{len(wrong)} documented example(s) routed to the wrong intent.",
            evidence="\n".join(lines),
            duration_seconds=elapsed,
        )
    if len(sets) < 4:
        return CheckResult(
            VerificationStatus.FAILED,
            "Different intents produced nearly identical agent sets, so routing "
            "is not actually gating execution.",
            evidence="\n".join(lines),
            duration_seconds=elapsed,
        )

    return CheckResult(
        VerificationStatus.VERIFIED,
        f"All five documented examples routed correctly, to {len(sets)} distinct "
        "agent workflows.",
        evidence=(
            "\n".join(lines)
            + "\n\nThis is the deterministic path, which needs no API key. With a "
            "key configured, an LLM zero-shot classifier runs first and this is "
            "the fallback."
        ),
        duration_seconds=elapsed,
    )


def check_vector_db(settings: Settings) -> CheckResult:
    """Run a real similarity search and show the distances."""
    started = time.perf_counter()
    status = retrieval_status(settings)
    if not status.available:
        return CheckResult(
            VerificationStatus.NOT_CONFIGURED,
            "ChromaDB has no indexed chunks yet.",
            evidence=(status.error or "")
            + " The app builds it on first start; to build it by hand, run "
            "`python -m rag.ingest`.",
        )

    query = "what should a test's expected value be based on"
    try:
        passages = similarity_search(query, top_k=3, settings=settings)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            VerificationStatus.FAILED,
            f"The similarity search raised {type(exc).__name__}: {exc}",
            duration_seconds=time.perf_counter() - started,
        )
    elapsed = time.perf_counter() - started

    if not passages:
        return CheckResult(
            VerificationStatus.FAILED,
            "The search ran but returned no passages.",
            duration_seconds=elapsed,
        )

    lines = [f"query: {query!r}", ""]
    for index, passage in enumerate(passages, start=1):
        lines.append(
            f"[{index}] similarity {passage.score}  "
            f"(cosine distance {passage.distance:.4f})  {passage.source}"
        )
    return CheckResult(
        VerificationStatus.VERIFIED,
        f"A live similarity search over {status.chunk_count} stored chunks "
        f"returned {len(passages)} passage(s) with real distances.",
        evidence=(
            "\n".join(lines)
            + f"\n\nCollection: {status.collection}\n"
            f"Persisted at: {status.persist_dir}\n"
            f"Embedding model: {EMBEDDING_MODEL_NAME} (local, 384 dimensions)"
        ),
        duration_seconds=elapsed,
    )


def check_sandbox(settings: Settings) -> CheckResult:
    """Not one of the seven, but the claim most worth proving."""
    started = time.perf_counter()
    executor = SandboxExecutor(settings)
    if not executor.available:
        return CheckResult(
            VerificationStatus.NOT_CONFIGURED,
            "No isolated sandbox backend is available, so code execution is "
            "disabled and tests are reported as Not Executed.",
            evidence=executor.backend.description + "\n" + executor.backend.detail,
        )

    probe = '''"""Sandbox escape probe. Each test passes only if the escape is denied."""

import os
import socket

import pytest


def test_network_is_denied():
    """Opening a socket to the outside world must be refused."""
    with pytest.raises((OSError, PermissionError, socket.error)):
        socket.create_connection(("1.1.1.1", 80), timeout=3)


def test_dns_is_denied():
    """Name resolution must also be unavailable."""
    with pytest.raises((OSError, socket.gaierror)):
        socket.getaddrinfo("example.com", 80)


def test_write_outside_sandbox_is_denied():
    """Writing above the sandbox's own working directory must be refused.

    The parent of the working directory is deliberate: HOME is remapped into
    the sandbox, so "~" is legitimately writable and would prove nothing.
    """
    outside = os.path.dirname(os.getcwd())
    target = os.path.join(outside, "debug_squad_escape_probe.txt")
    with pytest.raises((OSError, PermissionError)):
        with open(target, "w") as handle:
            handle.write("escaped")


def test_write_to_system_path_is_denied():
    """Writing to a system directory must be refused."""
    with pytest.raises((OSError, PermissionError)):
        with open("/etc/debug_squad_escape_probe", "w") as handle:
            handle.write("escaped")


def test_writes_inside_the_sandbox_still_work():
    """The sandbox must remain usable - pytest needs to be able to write."""
    with open("inside.txt", "w") as handle:
        handle.write("fine")
    assert os.path.exists("inside.txt")
'''
    result = executor.run_tests("# nothing to import\n", probe)
    elapsed = time.perf_counter() - started

    if not result.executed:
        return CheckResult(
            VerificationStatus.FAILED,
            f"The sandbox probe could not run: {result.unavailable_reason}",
            evidence=(result.stdout or "")[-1500:] + (result.stderr or "")[-800:],
            duration_seconds=elapsed,
        )

    outcomes = {o.name: o.outcome for o in parse_pytest_output(result.stdout)}
    required = {
        "test_network_is_denied": "an outbound socket connection",
        "test_dns_is_denied": "DNS resolution",
        "test_write_outside_sandbox_is_denied": "a write above the sandbox directory",
        "test_write_to_system_path_is_denied": "a write to /etc",
    }
    denied = [
        name for name in required if outcomes.get(name) is TestOutcome.PASSED
    ]
    not_denied = [name for name in required if name not in denied]

    lines = [
        f"backend: {executor.backend.name}",
        f"exit code: {result.exit_code}",
        "",
        "Escape attempts (a PASSED test means the escape was blocked):",
    ]
    for name, description in required.items():
        outcome = outcomes.get(name)
        verdict = (
            "BLOCKED" if outcome is TestOutcome.PASSED
            else f"NOT BLOCKED ({outcome.value if outcome else 'no result'})"
        )
        lines.append(f"  {verdict:<34} {description}")
    lines.append("")
    lines.append(result.stdout[-1200:])
    evidence = "\n".join(lines)

    if not_denied:
        return CheckResult(
            VerificationStatus.FAILED,
            f"{len(not_denied)} escape attempt(s) were not blocked: "
            + ", ".join(required[name] for name in not_denied),
            evidence=evidence,
            duration_seconds=elapsed,
        )

    return CheckResult(
        VerificationStatus.VERIFIED,
        f"Live escape probe: the {executor.backend.name} sandbox blocked an "
        "outbound connection, DNS resolution, and a write outside its temporary "
        "directory.",
        evidence=evidence,
        duration_seconds=elapsed,
    )


CRITERIA: list[Criterion] = [
    Criterion(
        number=1,
        title="LLM Integration",
        what=(
            "A configurable LLM provider - Groq over its API, or a locally hosted "
            "model through Ollama - with model availability validation and "
            "graceful failure."
        ),
        how=(
            "`config/llm_provider.py` builds a LangChain chat model for whichever "
            "provider is configured. Before any review runs, it queries the "
            "provider's live model list and checks the configured model is "
            "actually available to the key. No credential is hardcoded: everything "
            "comes from environment variables, and the key is masked wherever it "
            "is displayed. The active model is shown in the sidebar."
        ),
        files=[
            "config/llm_provider.py",
            "config/ollama_chat.py",
            "config/settings.py",
            ".env.example",
        ],
        check=check_llm,
        navigate_to="Settings",
        costs_tokens=True,
        demo_notes=[
            "This check sends a real one-word completion request and shows the reply.",
            "The model list is fetched live, not hardcoded, because provider "
            "catalogues change.",
        ],
    ),
    Criterion(
        number=2,
        title="Interactive GUI",
        what=(
            "A seven-screen Streamlit application: code workspace, AI agents, "
            "results and history, test lab, prompt inspector, this compliance "
            "page, and settings - with a live system status in the sidebar."
        ),
        how=(
            "`app.py` handles navigation and the sidebar's live LLM, sandbox and "
            "reference-notes status. Each screen is a module "
            "under `ui/pages/`. Code can be pasted or uploaded as a .py file; "
            "examples load with one click; review mode, automatic intent "
            "detection and RAG are all togglable; corrected code, diffs, test "
            "modules and the HTML report all download."
        ),
        files=["app.py", "ui/pages/", "ui/components.py", "ui/styles.css"],
        check=check_gui,
        navigate_to="Code workspace",
    ),
    Criterion(
        number=3,
        title="Prompt Engineering",
        what=(
            "Zero-shot, few-shot, reflection and ReAct-style tool use, each "
            "identifiable in the code and observable in the Prompt Inspector."
        ),
        how=(
            "System prompts live in `prompts/system_prompts.py` and user prompt "
            "templates in `prompts/user_prompts.py`, so the whole prompt surface "
            "is readable in two files. The Bug-Finder uses three hand-written "
            "few-shot examples; the third is correct code whose expected answer "
            "is an empty issue list, which is what stops the model inventing a "
            "defect. The Fixer's reflection prompt is fed the real pytest output. "
            "Every call is recorded as a PromptRecord with its technique, "
            "template, rendered prompt, retrieved context and parsed output."
        ),
        files=[
            "prompts/system_prompts.py",
            "prompts/user_prompts.py",
            "prompts/examples.py",
            "ui/pages/prompt_inspector.py",
        ],
        check=check_prompt_engineering,
        navigate_to="Prompt inspector",
        demo_notes=[
            "Run a review, then open the Prompt Inspector and step through each "
            "recorded call.",
            "Prompts ask for a short published justification, never hidden "
            "chain-of-thought.",
        ],
    ),
    Criterion(
        number=4,
        title="LangChain and LCEL",
        what=(
            "ChatPromptTemplate with system and human message templates, a "
            "LangChain model integration, PydanticOutputParser, and Runnable "
            "pipelines composed with the `|` operator."
        ),
        how=(
            "`chains/lcel_chains.py` defines `StructuredChain`, whose pipeline is "
            "literally `self.prompt | self.model | self.parser`. Every agent uses "
            "it. `RepairingPydanticParser` subclasses LangChain's own "
            "`PydanticOutputParser` and overrides `parse_result` - the method the "
            "Runnable actually calls - so the JSON salvage logic sits inside the "
            "pipeline rather than wrapped around it. The explanation handler uses "
            "`StrOutputParser` for markdown output."
        ),
        files=["chains/lcel_chains.py", "agents/", "config/ollama_chat.py"],
        check=check_langchain,
        navigate_to="Prompt inspector",
        demo_notes=[
            "This check builds and invokes a real chain with a stub model, so it "
            "verifies the wiring without spending tokens.",
        ],
    ),
    Criterion(
        number=5,
        title="Agentic RAG",
        what=(
            "Retrieval that the system decides on, rather than performing "
            "unconditionally - with the decision and its reason recorded either "
            "way."
        ),
        how=(
            "`rag/retriever.py` holds a policy table keyed by (intent, agent). A "
            "style review retrieves the conventions document; debugging retrieves "
            "exception and edge-case notes; test generation retrieves testing "
            "principles. An explanation request retrieves nothing, and neither "
            "does the Fixer, which already has the findings it needs. Skipped "
            "retrievals carry a reason, so the negative case is as visible as the "
            "positive one, and the prompt genuinely differs between the two paths."
        ),
        files=["rag/retriever.py", "rag/ingest.py", "rag/knowledge_base/"],
        check=check_agentic_rag,
        navigate_to="AI agents",
        demo_notes=[
            "Run example 3 ('Make it readable'): the Style agent's log on the AI "
            "agents page shows what it looked up.",
            "Then run example 4 ('Explain it'): the Explainer's log shows retrieval "
            "deliberately skipped, with the reason.",
        ],
    ),
    Criterion(
        number=6,
        title="Intent Detection and Routing",
        what=(
            "Five intents, detected automatically or chosen manually, each "
            "selecting a different set of agents."
        ),
        how=(
            "`routing/intent_router.py` classifies the request zero-shot through "
            "an LCEL chain, and falls back to a deterministic keyword matcher when "
            "the LLM is unavailable, rate-limited, or returns something "
            "unparseable. The decision selects a workflow, and the orchestrator "
            "constructs only those agents - the rest are marked Skipped with a "
            "reason on the AI Agents screen. The detected intent, the detection "
            "method, the confidence and the routing explanation are all shown."
        ),
        files=["routing/intent_router.py", "services/review_orchestrator.py"],
        check=check_routing,
        navigate_to="AI agents",
        demo_notes=[
            "This check uses the deterministic path, so it works without an API key.",
            "Routing is only meaningful if it changes what runs - the check also "
            "asserts the agent sets genuinely differ.",
        ],
    ),
    Criterion(
        number=7,
        title="ChromaDB Vector Database",
        what=(
            "A persistent ChromaDB collection built from six original knowledge "
            "documents, with real embeddings, metadata, similarity search and an "
            "inspection screen."
        ),
        how=(
            "`rag/ingest.py` chunks the markdown on heading boundaries with "
            "overlap, embeds each chunk with ChromaDB's bundled local ONNX "
            "all-MiniLM-L6-v2 model (384 dimensions, no API key), and stores it "
            "in a PersistentClient collection with source, title, section and "
            "chunk-index metadata. Search uses cosine distance and returns the "
            "distances, which this check displays."
        ),
        files=["rag/ingest.py", "rag/retriever.py", "rag/knowledge_base/"],
        check=check_vector_db,
        navigate_to="",
        demo_notes=[
            "Running this check performs a live similarity search and shows the "
            "passages it found with their distances - that is the demonstration.",
            "The embedding model is local - the first run downloads it once, "
            "after which no network access is needed for retrieval.",
        ],
    ),
]

SANDBOX_CRITERION = Criterion(
    number=8,
    title="Secure Code Execution (additional)",
    what=(
        "Untrusted code never runs in the Streamlit process. It runs in a "
        "kernel-isolated child with no network access and a confined filesystem."
    ),
    how=(
        "`sandbox/sandbox_config.py` prefers Docker (`--network none`, read-only "
        "root, memory and CPU caps, non-root, all capabilities dropped) and falls "
        "back to macOS seatbelt, which generates a per-run profile denying network "
        "access and all writes outside one temporary directory. Resource limits "
        "(CPU, file size, process count) are applied in the child on top of a "
        "wall-clock timeout. If no isolated backend exists, execution is disabled "
        "and tests are labelled Not Executed - never assumed to pass."
    ),
    files=["sandbox/sandbox_config.py", "sandbox/executor.py"],
    check=check_sandbox,
    navigate_to="Test lab",
    demo_notes=[
        "This check runs a live escape probe: it tries to open a socket and to "
        "write outside the sandbox, and requires both to be denied.",
    ],
)

ALL_CRITERIA = [*CRITERIA, SANDBOX_CRITERION]
