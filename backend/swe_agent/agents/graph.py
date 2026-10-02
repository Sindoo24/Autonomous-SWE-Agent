"""The agent graph.

    START -> intake -> prepare_repo -> baseline_tests -> explore -> hypothesize -> plan
          -> reproduce -> implement -> validate_patch
    validate_patch  --accepted--> run_tests
                    --rejected, attempts left--> implement
                    --rejected, none left------> report_failure
    run_tests       --pass--> verify -> human_approval
                    --fail--> analyze_failure --implement--> implement      (patch's own error)
                                              --plan-------> plan           (re-plan in place)
                                              --explore----> explore        (after rollback)
                                              --stop-------> report_failure (abort, repeated,
                                                             oscillation, out of iterations)
    human_approval  --approved--> finalize -> END
                    --rejected + feedback + retry--> plan
                    --rejected--> report_failure -> END
    any node that sets termination_reason ------------> report_failure

With execution disabled (SANDBOX_ENABLED=false) there is no baseline_tests / reproduce /
run_tests / analyze_failure / verify: validate_patch --accepted--> human_approval.
The graph is compiled with a checkpointer when one is given, which `interrupt()` requires.
"""

from __future__ import annotations

from collections.abc import Callable
from itertools import pairwise
from typing import Any

from langgraph.graph import END, START, StateGraph

from swe_agent.agents.nodes.approval import human_approval
from swe_agent.agents.nodes.exploration import explore, hypothesize
from swe_agent.agents.nodes.failure_analysis import analyze_failure
from swe_agent.agents.nodes.intake import intake, prepare_repo
from swe_agent.agents.nodes.patching import implement, validate_patch
from swe_agent.agents.nodes.planning import plan
from swe_agent.agents.nodes.reporting import finalize, report_failure
from swe_agent.agents.nodes.reproduction import reproduce
from swe_agent.agents.nodes.testing import baseline_tests, run_tests
from swe_agent.agents.nodes.verification import verify
from swe_agent.agents.state import AgentState

FAIL = "report_failure"


def _next_or_fail(nxt: str) -> Callable[[AgentState], str]:
    def route(state: AgentState) -> str:
        return FAIL if state.get("termination_reason") else nxt

    route.__name__ = f"route_to_{nxt}"
    return route


def make_after_validate(max_attempts: int, on_accept: str) -> Callable[[AgentState], str]:
    def after_validate(state: AgentState) -> str:
        if state.get("termination_reason"):
            return FAIL
        last = state["patch_history"][-1]
        if last.validation.accepted:
            return on_accept
        if state.get("validation_attempts", 0) >= max_attempts:
            return FAIL
        return "implement"

    return after_validate


def after_run_tests(state: AgentState) -> str:
    if state.get("termination_reason"):
        return FAIL
    return "analyze_failure" if state.get("failure_analysis") else "verify"


def after_analysis(state: AgentState) -> str:
    if state.get("termination_reason"):
        return FAIL
    analysis = state.get("failure_analysis")
    assert analysis is not None
    return analysis.route  # "implement" | "plan" | "explore" ("abort" sets termination_reason)


def after_approval(state: AgentState) -> str:
    if state.get("termination_reason"):
        return FAIL
    decision = state.get("approval")
    return "finalize" if decision is not None and decision.approved else "plan"


def build_graph(
    *, max_validation_attempts: int = 3, execution: bool = True, checkpointer: Any = None
) -> Any:
    g: StateGraph[AgentState] = StateGraph(AgentState)
    nodes: dict[str, Any] = {  # Any: LangGraph's node protocols do not model our async wrapper
        "intake": intake,
        "prepare_repo": prepare_repo,
        "explore": explore,
        "hypothesize": hypothesize,
        "plan": plan,
        "implement": implement,
        "validate_patch": validate_patch,
        "human_approval": human_approval,
        "finalize": finalize,
        FAIL: report_failure,
    }
    if execution:
        nodes |= {
            "baseline_tests": baseline_tests,
            "run_tests": run_tests,
            "analyze_failure": analyze_failure,
            "verify": verify,
            "reproduce": reproduce,
        }
    for name, fn in nodes.items():
        g.add_node(name, fn)

    g.add_edge(START, "intake")
    chain = ["intake", "prepare_repo"]
    chain += ["baseline_tests"] if execution else []
    chain += ["explore", "hypothesize", "plan"]
    chain += ["reproduce"] if execution else []
    chain += ["implement", "validate_patch"]
    for cur, nxt in pairwise(chain):
        g.add_conditional_edges(cur, _next_or_fail(nxt), [nxt, FAIL])

    on_accept = "run_tests" if execution else "human_approval"
    g.add_conditional_edges(
        "validate_patch",
        make_after_validate(max_validation_attempts, on_accept),
        [on_accept, "implement", FAIL],
    )
    if execution:
        g.add_conditional_edges("run_tests", after_run_tests, ["verify", "analyze_failure", FAIL])
        g.add_conditional_edges(
            "analyze_failure", after_analysis, ["plan", "implement", "explore", FAIL]
        )
        g.add_conditional_edges("verify", _next_or_fail("human_approval"), ["human_approval", FAIL])
    g.add_conditional_edges("human_approval", after_approval, ["finalize", "plan", FAIL])
    g.add_edge("finalize", END)
    g.add_edge(FAIL, END)
    return g.compile(checkpointer=checkpointer)
