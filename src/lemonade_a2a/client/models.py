"""Provider-neutral application view; upstream SDK objects remain authoritative."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from a2a.types import Part, StreamResponse, Task, TaskState
from google.protobuf.json_format import MessageToDict

TERMINAL_STATES = frozenset(
    TaskState.Name(state)
    for state in (
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_REJECTED,
        # Interrupted: the remote agent waits for its caller, so polling would never end.
        TaskState.TASK_STATE_INPUT_REQUIRED,
        TaskState.TASK_STATE_AUTH_REQUIRED,
    )
)
# A model's thinking, streamed by a Lemonade agent with LEMONADE_A2A_REASONING=artifact:
# not part of the answer.
HIDDEN_ARTIFACTS = frozenset({"lemonade-reasoning"})


@dataclass(frozen=True)
class AgentResult:
    agent_id: str
    task_id: str | None
    status: str
    artifacts: list[Any]
    correlation_id: str


def part_text(part: Part) -> str:
    """Readable text of one part: text as is, structured data as JSON, files by reference."""
    kind = part.WhichOneof("content")
    if kind == "text":
        return part.text
    if kind == "data":
        return json.dumps(MessageToDict(part.data), ensure_ascii=False, sort_keys=True)
    if kind == "url":
        return f"[file: {part.url}]"
    if kind == "raw":
        return f"[binary {part.media_type or 'data'}: {len(part.raw)} bytes]"
    return ""


def _file_parts(parts: Any) -> list[Part]:
    return [part for part in parts if part.WhichOneof("content") in ("url", "raw")]


def parts_text(parts: Any) -> str:
    return "".join(part_text(part) for part in parts)


@dataclass
class AgentReply:
    """What a remote agent answered, folded from every event it sent for one message."""

    state: str = ""  # a TaskState name, or "message" when the agent answered without a task
    task_id: str | None = None
    context_id: str | None = None
    status_text: str = ""
    _artifacts: dict[str, list[str]] = field(default_factory=dict, repr=False)
    _files: dict[str, list[Part]] = field(default_factory=dict, repr=False)

    @property
    def text(self) -> str:
        """The answer: every artifact (or the direct reply message), in order."""
        return "".join("".join(chunks) for chunks in self._artifacts.values())

    @property
    def files(self) -> list[Part]:
        """File parts (``url`` or ``raw``) the agent returned, in order."""
        return [part for parts in self._files.values() for part in parts]

    @property
    def finished(self) -> bool:
        return self.state == "message" or self.state in TERMINAL_STATES

    @property
    def succeeded(self) -> bool:
        return self.state in ("message", "TASK_STATE_COMPLETED")

    def add_task(self, task: Task) -> None:
        self.task_id = task.id or self.task_id
        self.context_id = task.context_id or self.context_id
        self.state = TaskState.Name(task.status.state)
        if task.status.HasField("message"):
            self.status_text = parts_text(task.status.message.parts)
        if task.artifacts:
            shown = [
                (artifact.artifact_id or str(index), artifact)
                for index, artifact in enumerate(task.artifacts)
                if artifact.name not in HIDDEN_ARTIFACTS
            ]
            self._artifacts = {key: [parts_text(a.parts)] for key, a in shown}
            self._files = {key: _file_parts(a.parts) for key, a in shown}

    def add(self, event: StreamResponse) -> None:
        if event.HasField("message"):
            self.state = "message"
            self.context_id = event.message.context_id or self.context_id
            self._artifacts = {"message": [parts_text(event.message.parts)]}
            self._files = {"message": _file_parts(event.message.parts)}
        elif event.HasField("task"):
            self.add_task(event.task)
        elif event.HasField("status_update"):
            update = event.status_update
            self.task_id = update.task_id or self.task_id
            self.context_id = update.context_id or self.context_id
            self.state = TaskState.Name(update.status.state)
            if update.status.HasField("message"):
                self.status_text = parts_text(update.status.message.parts)
        elif event.HasField("artifact_update"):
            update = event.artifact_update
            if update.artifact.name in HIDDEN_ARTIFACTS:
                return
            key = update.artifact.artifact_id or "default"
            chunks = self._artifacts.setdefault(key, [])
            files = self._files.setdefault(key, [])
            if not update.append:
                chunks.clear()
                files.clear()
            chunks.append(parts_text(update.artifact.parts))
            files.extend(_file_parts(update.artifact.parts))
