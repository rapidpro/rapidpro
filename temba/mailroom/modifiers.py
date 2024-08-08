from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class FieldRef:
    key: str
    name: str


@dataclass(frozen=True)
class GroupRef:
    uuid: str
    name: str


@dataclass(frozen=True)
class TopicRef:
    uuid: str
    name: str


@dataclass(frozen=True)
class UserRef:
    email: str
    name: str


@dataclass(frozen=True)
class Modifier:
    type: str


@dataclass(frozen=True)
class Name(Modifier):
    type: str = field(default="name", init=False)
    name: str


@dataclass(frozen=True)
class Language(Modifier):
    type: str = field(default="language", init=False)
    language: str


@dataclass(frozen=True)
class Field(Modifier):
    type: str = field(default="field", init=False)
    field: FieldRef
    value: str


@dataclass(frozen=True)
class Status(Modifier):
    ACTIVE = "active"
    BLOCKED = "blocked"
    STOPPED = "stopped"
    ARCHIVED = "archived"

    type: str = field(default="status", init=False)
    status: str


@dataclass(frozen=True)
class Groups(Modifier):
    type: str = field(default="groups", init=False)
    groups: list[GroupRef]
    modification: str


@dataclass(frozen=True)
class Ticket(Modifier):
    type: str = field(default="ticket", init=False)
    topic: TopicRef
    assignee: Optional[UserRef]
    note: Optional[str]


@dataclass(frozen=True)
class URNs(Modifier):
    type: str = field(default="urns", init=False)
    urns: list[str]
    modification: str
