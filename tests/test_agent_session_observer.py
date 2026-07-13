from __future__ import annotations

import asyncio
from typing import Any

import pytest
from livekit.protocol.agent_pb import agent_session as agent_pb

from livekit_agent_simulator.livekit.agent_session_observer import (
    TOPIC_SESSION_MESSAGES,
    AgentSessionObserver,
)
from livekit_agent_simulator.logging.event_writer import EventWriter


class _ByteWriter:
    def __init__(self) -> None:
        self.data = b""
        self.closed = False

    async def write(self, data: bytes) -> None:
        self.data += data

    async def aclose(self) -> None:
        self.closed = True


class _LocalParticipant:
    def __init__(self) -> None:
        self.writers: list[_ByteWriter] = []
        self.calls: list[dict[str, Any]] = []

    async def stream_bytes(self, **kwargs: Any) -> _ByteWriter:
        writer = _ByteWriter()
        self.writers.append(writer)
        self.calls.append(kwargs)
        return writer


class _Room:
    def __init__(self) -> None:
        self.local_participant = _LocalParticipant()
        self.handlers: dict[str, Any] = {}

    def register_byte_stream_handler(self, topic: str, handler: Any) -> None:
        self.handlers[topic] = handler

    def unregister_byte_stream_handler(self, topic: str) -> None:
        del self.handlers[topic]


class _ByteReader:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> Any:
        return self._iterate()

    async def _iterate(self) -> Any:
        for chunk in self._chunks:
            yield chunk


def _observer(tmp_path: Any) -> tuple[AgentSessionObserver, EventWriter, _Room]:
    writer = EventWriter("r-test", tmp_path / "reports" / "r-test", timezone_name="UTC")
    room = _Room()
    observer = AgentSessionObserver(  # type: ignore[arg-type]
        room,
        writer,
        "agent-1",
        request_timeout_s=0.5,
    )
    return observer, writer, room


def test_tools_executed_emits_paired_start_and_end(tmp_path: Any) -> None:
    observer, writer, _ = _observer(tmp_path)
    call = agent_pb.FunctionCall(
        id="item-1",
        call_id="call-1",
        name="lookup_dictionary",
        arguments='{"word":"hello"}',
    )
    output = agent_pb.FunctionCallOutput(
        id="item-2",
        call_id="call-1",
        name="lookup_dictionary",
        output='"こんにちは"',
    )
    message = agent_pb.AgentSessionMessage(
        event=agent_pb.AgentSessionEvent(
            function_tools_executed=agent_pb.AgentSessionEvent.FunctionToolsExecuted(
                function_calls=[call],
                function_call_outputs=[output],
            )
        )
    )

    observer.handle_message(message)

    tools = [event for event in writer.events if event["kind"].startswith("tool.")]
    assert [event["kind"] for event in tools] == ["tool.start", "tool.end"]
    assert tools[0]["spec"]["arguments"] == '{"word":"hello"}'
    assert tools[1]["spec"]["name"] == "lookup_dictionary"
    assert tools[1]["spec"]["output"] == '"こんにちは"'
    assert tools[1]["parent_event_id"] == tools[0]["event_id"]


def test_tools_executed_warns_on_call_id_mismatch_and_marks_error(tmp_path: Any) -> None:
    observer, writer, _ = _observer(tmp_path)
    observer.handle_message(
        agent_pb.AgentSessionMessage(
            event=agent_pb.AgentSessionEvent(
                function_tools_executed=agent_pb.AgentSessionEvent.FunctionToolsExecuted(
                    function_calls=[
                        agent_pb.FunctionCall(call_id="call-1", name="lookup")
                    ],
                    function_call_outputs=[
                        agent_pb.FunctionCallOutput(
                            call_id="call-2",
                            name="lookup",
                            output="not found",
                            is_error=True,
                        )
                    ],
                )
            )
        )
    )

    assert any(event["kind"] == "observer.warning" for event in writer.events)
    error = next(event for event in writer.events if event["kind"] == "tool.error")
    assert error["spec"]["error"] == "not found"


def test_history_reconcile_only_emits_missing_tool_events(tmp_path: Any) -> None:
    observer, writer, _ = _observer(tmp_path)
    items = [
        agent_pb.ChatContext.ChatItem(
            function_call=agent_pb.FunctionCall(
                call_id="call-history",
                name="lookup",
                arguments="{}",
            )
        ),
        agent_pb.ChatContext.ChatItem(
            function_call_output=agent_pb.FunctionCallOutput(
                call_id="call-history",
                name="lookup",
                output="ok",
            )
        ),
    ]

    observer._reconcile_history(items)
    observer._reconcile_history(items)

    assert [event["kind"] for event in writer.events] == ["tool.start", "tool.end"]


def test_history_reconcile_pairs_output_even_when_it_precedes_call(tmp_path: Any) -> None:
    observer, writer, _ = _observer(tmp_path)
    call = agent_pb.ChatContext.ChatItem(
        function_call=agent_pb.FunctionCall(call_id="call-reversed", name="lookup")
    )
    output = agent_pb.ChatContext.ChatItem(
        function_call_output=agent_pb.FunctionCallOutput(
            call_id="call-reversed",
            name="lookup",
            output="ok",
        )
    )

    observer._reconcile_history([output, call])

    tools = [event for event in writer.events if event["kind"].startswith("tool.")]
    assert [event["kind"] for event in tools] == ["tool.start", "tool.end"]
    assert tools[1]["parent_event_id"] == tools[0]["event_id"]


def test_tool_execution_update_is_session_progress_only(tmp_path: Any) -> None:
    observer, writer, _ = _observer(tmp_path)
    observer.handle_message(
        agent_pb.AgentSessionMessage(
            event=agent_pb.AgentSessionEvent(
                tool_execution_updated=agent_pb.AgentSessionEvent.ToolExecutionUpdated(
                    started=agent_pb.AgentSessionEvent.ToolExecutionUpdated.Started(
                        function_call=agent_pb.FunctionCall(
                            call_id="progress-1",
                            name="lookup",
                        )
                    )
                )
            )
        )
    )

    assert [event["kind"] for event in writer.events] == [
        "session.tool_execution"
    ]


@pytest.mark.asyncio
async def test_ingress_joins_chunks_and_filters_participant(tmp_path: Any) -> None:
    observer, writer, room = _observer(tmp_path)
    observer.attach()
    payload = agent_pb.AgentSessionMessage(
        event=agent_pb.AgentSessionEvent(
            error=agent_pb.AgentSessionEvent.Error(message="model failed")
        )
    ).SerializeToString()

    handler = room.handlers[TOPIC_SESSION_MESSAGES]
    handler(_ByteReader([payload[:3], payload[3:]]), "other-participant")
    handler(_ByteReader([payload[:3], payload[3:]]), "agent-1")
    while observer._tasks:
        await asyncio.sleep(0)

    errors = [event for event in writer.events if event["kind"] == "session.error"]
    assert len(errors) == 1
    assert errors[0]["spec"]["message"] == "model failed"
    await observer.detach()


@pytest.mark.asyncio
async def test_request_response_pairing_uses_agent_destination(tmp_path: Any) -> None:
    observer, _, room = _observer(tmp_path)
    observer.attach()
    request = agent_pb.SessionRequest(
        request_id="req_test",
        get_chat_history=agent_pb.SessionRequest.GetChatHistory(),
    )

    task = asyncio.create_task(observer._send_request(request))
    while not room.local_participant.writers:
        await asyncio.sleep(0)

    sent = agent_pb.AgentSessionMessage()
    sent.ParseFromString(room.local_participant.writers[0].data)
    assert sent.request.request_id == "req_test"
    assert room.local_participant.calls[0]["topic"] == TOPIC_SESSION_MESSAGES
    assert room.local_participant.calls[0]["destination_identities"] == ["agent-1"]

    observer.handle_message(
        agent_pb.AgentSessionMessage(
            response=agent_pb.SessionResponse(
                request_id="req_test",
                get_chat_history=agent_pb.SessionResponse.GetChatHistoryResponse(),
            )
        )
    )
    response = await task
    assert response.HasField("get_chat_history")

    await observer.detach()
    assert TOPIC_SESSION_MESSAGES not in room.handlers
