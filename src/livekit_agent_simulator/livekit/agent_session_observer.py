"""LiveKit Agents RemoteSession observer for tools and session diagnostics."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from google.protobuf.json_format import MessageToDict
from livekit import rtc
from livekit.protocol.agent_pb import agent_session as agent_pb

from ..logging.event_writer import EventWriter

TOPIC_SESSION_MESSAGES = "lk.agent.session"
_SOURCE = TOPIC_SESSION_MESSAGES


def _message_dict(message: Any) -> dict[str, Any]:
    return MessageToDict(
        message,
        preserving_proto_field_name=True,
        use_integers_for_enums=False,
    )


def _enum_name(message: Any, field_name: str) -> str:
    field = message.DESCRIPTOR.fields_by_name[field_name]
    value = int(getattr(message, field_name))
    enum_value = field.enum_type.values_by_number.get(value)
    return enum_value.name if enum_value is not None else str(value)


def _function_call_spec(call: agent_pb.FunctionCall) -> dict[str, Any]:
    return {
        "id": call.id or None,
        "call_id": call.call_id or None,
        "name": call.name or None,
        "arguments": call.arguments,
    }


def _function_output_spec(output: agent_pb.FunctionCallOutput) -> dict[str, Any]:
    return {
        "id": output.id or None,
        "call_id": output.call_id or None,
        "name": output.name or None,
        "output": output.output,
        "is_error": bool(output.is_error),
    }


def _chat_item_dict(item: agent_pb.ChatContext.ChatItem) -> dict[str, Any]:
    item_type = item.WhichOneof("item")
    if item_type == "function_call":
        return {"type": item_type, **_function_call_spec(item.function_call)}
    if item_type == "function_call_output":
        return {"type": item_type, **_function_output_spec(item.function_call_output)}
    if item_type == "message":
        return {"type": item_type, **_message_dict(item.message)}
    if item_type == "agent_handoff":
        return {"type": item_type, **_message_dict(item.agent_handoff)}
    if item_type == "agent_config_update":
        return {"type": item_type, **_message_dict(item.agent_config_update)}
    return {"type": item_type or "unknown"}


class AgentSessionObserver:
    """Observe and query one agent's RemoteSession protocol."""

    def __init__(
        self,
        room: rtc.Room,
        writer: EventWriter,
        agent_identity: str,
        *,
        request_timeout_s: float = 60.0,
    ) -> None:
        self.room = room
        self.writer = writer
        self.agent_identity = agent_identity
        self.request_timeout_s = request_timeout_s

        self._attached = False
        self._tasks: set[asyncio.Task[None]] = set()
        self._pending_requests: dict[str, asyncio.Future[agent_pb.SessionResponse]] = {}
        self._open_tools: dict[str, dict[str, Any]] = {}
        self._started_call_ids: set[str] = set()
        self._completed_call_ids: set[str] = set()
        self._last_usage: dict[str, Any] | None = None

    def attach(self) -> None:
        if self._attached:
            return
        self.room.register_byte_stream_handler(TOPIC_SESSION_MESSAGES, self._on_byte_stream)
        self._attached = True

    async def detach(self) -> None:
        if self._attached:
            try:
                self.room.unregister_byte_stream_handler(TOPIC_SESSION_MESSAGES)
            except (ValueError, AttributeError):
                pass
            self._attached = False

        for future in self._pending_requests.values():
            if not future.done():
                future.cancel()
        self._pending_requests.clear()

        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def _on_byte_stream(self, reader: rtc.ByteStreamReader, participant_identity: str) -> None:
        if participant_identity != self.agent_identity:
            return
        task = asyncio.create_task(self._read_stream(reader))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _read_stream(self, reader: rtc.ByteStreamReader) -> None:
        try:
            chunks: list[bytes] = []
            async for chunk in reader:
                chunks.append(chunk)
            message = agent_pb.AgentSessionMessage()
            message.ParseFromString(b"".join(chunks))
            self.handle_message(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.writer.emit(
                "observer.error",
                spec={
                    "where": TOPIC_SESSION_MESSAGES,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                source=_SOURCE,
                include_dialogue=False,
            )

    def handle_message(self, message: agent_pb.AgentSessionMessage) -> None:
        """Dispatch a parsed wire message. Public for deterministic protocol tests."""
        if message.HasField("response"):
            response = message.response
            future = self._pending_requests.pop(response.request_id, None)
            if future is not None and not future.done():
                future.set_result(response)
            return
        if message.HasField("event"):
            self._handle_event(message.event)

    def _handle_event(self, event: agent_pb.AgentSessionEvent) -> None:
        event_type = event.WhichOneof("event")
        if event_type == "function_tools_started":
            for call in event.function_tools_started.function_calls:
                self._emit_tool_start(call)
            return
        if event_type == "function_tools_executed":
            self._handle_tools_executed(event.function_tools_executed)
            return
        if event_type == "tool_execution_updated":
            update = event.tool_execution_updated
            self.writer.emit(
                "session.tool_execution",
                spec=_message_dict(update),
                source=_SOURCE,
                include_dialogue=False,
            )
            return
        if event_type == "agent_state_changed":
            changed = event.agent_state_changed
            self._emit_session(
                "session.agent_state",
                {"old_state": _enum_name(changed, "old_state"), "new_state": _enum_name(changed, "new_state")},
            )
            return
        if event_type == "user_state_changed":
            changed = event.user_state_changed
            self._emit_session(
                "session.user_state",
                {"old_state": _enum_name(changed, "old_state"), "new_state": _enum_name(changed, "new_state")},
            )
            return
        if event_type == "session_usage_updated":
            usage = _message_dict(event.session_usage_updated.usage)
            self._emit_usage(usage)
            return
        if event_type == "error":
            self._emit_session("session.error", {"message": event.error.message})
            return
        if event_type == "overlapping_speech":
            self._emit_session(
                "session.overlapping_speech",
                _message_dict(event.overlapping_speech),
            )
            return
        if event_type == "debug_message":
            debug_value = event.debug_message
            spec = (
                _message_dict(debug_value)
                if hasattr(debug_value, "DESCRIPTOR")
                else {"message": str(debug_value)}
            )
            if any(value not in ("", None, [], {}) for value in spec.values()):
                self._emit_session("session.debug", spec)

    def _emit_session(self, kind: str, spec: dict[str, Any]) -> None:
        self.writer.emit(kind, spec=spec, source=_SOURCE, include_dialogue=False)

    def _emit_usage(self, usage: dict[str, Any]) -> None:
        if usage == self._last_usage:
            return
        self._last_usage = usage
        self._emit_session("session.usage", usage)

    def _tool_key(self, call_id: str | None, item_id: str | None) -> str | None:
        return call_id or item_id or None

    def _emit_tool_start(self, call: agent_pb.FunctionCall) -> dict[str, Any] | None:
        spec = _function_call_spec(call)
        key = self._tool_key(call.call_id, call.id)
        if key and key in self._started_call_ids:
            return self._open_tools.get(key)
        event = self.writer.emit("tool.start", spec=spec, source=_SOURCE)
        if key:
            self._started_call_ids.add(key)
            self._open_tools[key] = event
        return event

    def _emit_tool_output(
        self,
        output: agent_pb.FunctionCallOutput,
        *,
        paired_start: dict[str, Any] | None = None,
        paired_key: str | None = None,
    ) -> None:
        key = self._tool_key(output.call_id, output.id)
        if (key and key in self._completed_call_ids) or (
            paired_key and paired_key in self._completed_call_ids
        ):
            return

        start = paired_start or (self._open_tools.pop(key, None) if key else None)
        if paired_key:
            self._open_tools.pop(paired_key, None)
        spec = _function_output_spec(output)
        if not spec["name"] and start is not None:
            spec["name"] = start.get("spec", {}).get("name")
        if start is not None:
            spec["duration_ms"] = max(
                0,
                int((time.monotonic() - self.writer.run_start_mono) * 1000)
                - int(start["ts_mono_ms"]),
            )
        if output.is_error:
            spec["error"] = output.output
        self.writer.emit(
            "tool.error" if output.is_error else "tool.end",
            spec=spec,
            source=_SOURCE,
            parent_event_id=start["event_id"] if start is not None else None,
        )
        if key:
            self._completed_call_ids.add(key)
        if paired_key:
            self._completed_call_ids.add(paired_key)

    def _handle_tools_executed(
        self, executed: agent_pb.AgentSessionEvent.FunctionToolsExecuted
    ) -> None:
        calls = list(executed.function_calls)
        outputs = list(executed.function_call_outputs)
        for index, call in enumerate(calls):
            start = self._emit_tool_start(call)
            if index >= len(outputs):
                continue
            output = outputs[index]
            if call.call_id and output.call_id and call.call_id != output.call_id:
                self.writer.emit(
                    "observer.warning",
                    spec={
                        "where": "function_tools_executed",
                        "message": "call/output call_id mismatch; paired by array index",
                        "call_id": call.call_id,
                        "output_call_id": output.call_id,
                        "index": index,
                    },
                    source=_SOURCE,
                    include_dialogue=False,
                )
            self._emit_tool_output(
                output,
                paired_start=start,
                paired_key=self._tool_key(call.call_id, call.id),
            )
        for output in outputs[len(calls) :]:
            self._emit_tool_output(output)

    async def _send_request(
        self,
        request: agent_pb.SessionRequest,
    ) -> agent_pb.SessionResponse:
        request_type = request.WhichOneof("request")
        future: asyncio.Future[agent_pb.SessionResponse] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending_requests[request.request_id] = future
        try:
            writer = await self.room.local_participant.stream_bytes(
                name=f"AS_{uuid.uuid4().hex[:12]}",
                topic=TOPIC_SESSION_MESSAGES,
                destination_identities=[self.agent_identity],
            )
            await writer.write(
                agent_pb.AgentSessionMessage(request=request).SerializeToString()
            )
            await writer.aclose()
            response = await asyncio.wait_for(future, timeout=self.request_timeout_s)
        except Exception:
            self._pending_requests.pop(request.request_id, None)
            raise
        if response.error:
            raise RuntimeError(f"session request {request_type} failed: {response.error}")
        return response

    async def fetch_session_snapshot(self) -> None:
        """Fetch final chat history and usage without making snapshot failure fatal."""
        await self._fetch_chat_history()
        await self._fetch_session_usage()

    async def _fetch_chat_history(self) -> None:
        request = agent_pb.SessionRequest(
            request_id=f"req_{uuid.uuid4().hex[:12]}",
            get_chat_history=agent_pb.SessionRequest.GetChatHistory(),
        )
        try:
            response = await self._send_request(request)
        except Exception as exc:
            self._emit_snapshot_error("get_chat_history", exc)
            return

        items = [_chat_item_dict(item) for item in response.get_chat_history.items]
        self._emit_session("session.chat_history", {"items": items})
        self._reconcile_history(response.get_chat_history.items)

    async def _fetch_session_usage(self) -> None:
        request = agent_pb.SessionRequest(
            request_id=f"req_{uuid.uuid4().hex[:12]}",
            get_session_usage=agent_pb.SessionRequest.GetSessionUsage(),
        )
        try:
            response = await self._send_request(request)
        except Exception as exc:
            self._emit_snapshot_error("get_session_usage", exc)
            return
        self._emit_usage(_message_dict(response.get_session_usage.usage))

    def _emit_snapshot_error(self, operation: str, exc: Exception) -> None:
        self.writer.emit(
            "observer.error",
            spec={
                "where": f"{TOPIC_SESSION_MESSAGES}.{operation}",
                "error": f"{type(exc).__name__}: {exc}",
            },
            source=_SOURCE,
            include_dialogue=False,
        )

    def _reconcile_history(self, items: Any) -> None:
        history_items = list(items)
        calls_by_key: dict[str, agent_pb.FunctionCall] = {}
        for item in history_items:
            if item.WhichOneof("item") != "function_call":
                continue
            call = item.function_call
            key = self._tool_key(call.call_id, call.id)
            if key:
                calls_by_key[key] = call
        for item in history_items:
            item_type = item.WhichOneof("item")
            if item_type == "function_call":
                call = item.function_call
                self._emit_tool_start(call)
            elif item_type == "function_call_output":
                output = item.function_call_output
                key = self._tool_key(output.call_id, output.id)
                if key and key not in self._started_call_ids and key in calls_by_key:
                    self._emit_tool_start(calls_by_key[key])
                self._emit_tool_output(output)
