from __future__ import annotations

import pytest
from pydantic import BaseModel

from swe_agent.config import ToolMode
from swe_agent.core.budget import BudgetMeter
from swe_agent.core.errors import BudgetExceeded, ModelError
from swe_agent.llm.gateway import LLMGateway, ModelRole, RoleBinding, extract_json
from swe_agent.llm.schema_utils import inline_refs
from swe_agent.llm.scripted import ScriptedProvider, action, native_call
from swe_agent.llm.types import GenParams, LLMResponse, Message, ToolSchema, Usage
from swe_agent.schemas.state import Findings

TOOLS = [
    ToolSchema(
        name="read_file",
        description="r",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
    ),
    ToolSchema(name="finish", description="f", parameters={"type": "object"}),
]
MSGS = [Message(role="system", content="s"), Message(role="user", content="u")]


class Point(BaseModel):
    x: int
    y: int


def make_gw(provider, meter, recorder, artifacts, mode=ToolMode.JSON_ACTION) -> LLMGateway:  # type: ignore[no-untyped-def]
    b = RoleBinding(provider, "m", mode)
    return LLMGateway(
        {ModelRole.REASONING: b, ModelRole.CODER: b}, GenParams(), meter, recorder, artifacts
    )


@pytest.mark.parametrize(
    "text",
    ['{"x": 1}', '```json\n{"x": 1}\n```', 'Sure! Here it is: {"x": 1} hope that helps'],
)
def test_extract_json(text: str) -> None:
    assert extract_json(text) == {"x": 1}


async def test_structured_valid_first_try(meter, recorder, artifacts) -> None:  # type: ignore[no-untyped-def]
    prov = ScriptedProvider([{"x": 1, "y": 2}])
    gw = make_gw(prov, meter, recorder, artifacts)
    assert await gw.structured(ModelRole.REASONING, MSGS, Point, purpose="t") == Point(x=1, y=2)
    assert prov.calls[0].json_schema is not None
    assert prov.calls[0].json_schema["required"] == ["x", "y"]


async def test_structured_repairs_once(meter, recorder, artifacts) -> None:  # type: ignore[no-untyped-def]
    prov = ScriptedProvider(["not json at all", {"x": 3, "y": 4}])
    gw = make_gw(prov, meter, recorder, artifacts)
    assert (await gw.structured(ModelRole.REASONING, MSGS, Point, purpose="t")).x == 3
    repair_msg = prov.calls[1].messages[-1].content
    assert "rejected" in repair_msg and "not valid JSON" in repair_msg
    purposes = [e.data["purpose"] for e in recorder.events if e.event == "model_call"]
    assert purposes == ["t", "t:repair"]


async def test_structured_semantic_validation(meter, recorder, artifacts) -> None:  # type: ignore[no-untyped-def]
    prov = ScriptedProvider([{"x": -1, "y": 0}, {"x": 5, "y": 0}])
    gw = make_gw(prov, meter, recorder, artifacts)

    def positive(p: Point) -> list[str]:
        return [] if p.x > 0 else ["x must be positive"]

    out = await gw.structured(ModelRole.REASONING, MSGS, Point, purpose="t", validate=positive)
    assert out.x == 5
    assert "x must be positive" in prov.calls[1].messages[-1].content


async def test_structured_gives_up_after_repair(meter, recorder, artifacts) -> None:  # type: ignore[no-untyped-def]
    gw = make_gw(ScriptedProvider([{"x": "a"}, {"y": 1}]), meter, recorder, artifacts)
    with pytest.raises(ModelError, match="after repair"):
        await gw.structured(ModelRole.REASONING, MSGS, Point, purpose="t")


async def test_next_action_json_mode(meter, recorder, artifacts) -> None:  # type: ignore[no-untyped-def]
    prov = ScriptedProvider([action("read_file", path="a.py")])
    gw = make_gw(prov, meter, recorder, artifacts)
    act = await gw.next_action(ModelRole.REASONING, MSGS, TOOLS, purpose="loop")
    assert act.call.name == "read_file" and act.call.arguments == {"path": "a.py"}
    schema = prov.calls[0].json_schema
    assert schema is not None and len(schema["anyOf"]) == 2
    assert schema["anyOf"][0]["properties"]["tool"]["enum"] == ["read_file"]
    msg = gw.tool_result_message(ModelRole.REASONING, act.call, "result")
    assert msg.role == "user"


async def test_next_action_repairs_unknown_tool(meter, recorder, artifacts) -> None:  # type: ignore[no-untyped-def]
    prov = ScriptedProvider([action("rm_rf"), action("finish")])
    gw = make_gw(prov, meter, recorder, artifacts)
    act = await gw.next_action(ModelRole.REASONING, MSGS, TOOLS, purpose="loop")
    assert act.call.name == "finish"
    assert "unknown tool 'rm_rf'" in prov.calls[1].messages[-1].content


async def test_next_action_native_mode(meter, recorder, artifacts) -> None:  # type: ignore[no-untyped-def]
    prov = ScriptedProvider([LLMResponse(content="thinking"), native_call("read_file", path="b")])
    gw = make_gw(prov, meter, recorder, artifacts, mode=ToolMode.NATIVE_TOOLS)
    act = await gw.next_action(ModelRole.REASONING, MSGS, TOOLS, purpose="loop")
    assert act.call.arguments == {"path": "b"}
    assert prov.calls[0].tools == TOOLS and prov.calls[0].json_schema is None
    assert act.assistant_message.tool_calls == [act.call]
    msg = gw.tool_result_message(ModelRole.REASONING, act.call, "out")
    assert msg.role == "tool" and msg.tool_call_id == act.call.id


async def test_token_budget_enforced(recorder, artifacts) -> None:  # type: ignore[no-untyped-def]
    from swe_agent.schemas.state import Budget

    meter = BudgetMeter(
        Budget(max_iterations=1, max_tool_calls=10, max_tokens=100, max_wall_seconds=60)
    )
    prov = ScriptedProvider(
        [{"x": 1, "y": 1}] * 3, usage=Usage(prompt_tokens=90, completion_tokens=20)
    )
    gw = make_gw(prov, meter, recorder, artifacts)
    await gw.structured(ModelRole.REASONING, MSGS, Point, purpose="t")
    assert meter.budget.used_tokens == 110
    with pytest.raises(BudgetExceeded, match="tokens"):
        await gw.structured(ModelRole.REASONING, MSGS, Point, purpose="t")


async def test_model_call_recorded_with_artifacts(meter, recorder, artifacts) -> None:  # type: ignore[no-untyped-def]
    gw = make_gw(ScriptedProvider([{"x": 1, "y": 1}]), meter, recorder, artifacts)
    await gw.structured(ModelRole.REASONING, MSGS, Point, purpose="t")
    ev = next(e for e in recorder.events if e.event == "model_call")
    assert ev.data["tokens_in"] is None  # scripted provider reports no usage
    assert artifacts.read_text(ev.data["prompt_artifact"]).count('"role"') == 2


def test_inline_refs_handles_nested_models_and_title_fields() -> None:
    schema = inline_refs(Findings.model_json_schema())
    assert "$defs" not in schema and "$ref" not in str(schema)
    assert schema["properties"]["evidence"]["items"]["properties"]["path"]["type"] == "string"

    class HasTitle(BaseModel):
        title: str

    s = inline_refs(HasTitle.model_json_schema())
    assert "title" in s["properties"] and s["required"] == ["title"]
