from typing import Dict, List, NamedTuple


class FieldRef(NamedTuple):
    key: str
    name: str


class GroupRef(NamedTuple):
    uuid: str
    name: str


class Modifier:
    type: str

    def as_def(self) -> Dict:
        return {"type": self.type, **self.__dict__}

    def __eq__(self, other):
        return self.__dict__ == other.__dict__


class Name(Modifier):
    type = "name"

    def __init__(self, name: str):
        self.name = name


class Language(Modifier):
    type = "language"

    def __init__(self, language: str):
        self.language = language


class Field(Modifier):
    type = "field"

    def __init__(self, field: FieldRef, value: str):
        self.field = field
        self.value = value

    def as_def(self) -> Dict:
        return {"type": self.type, "field": self.field._asdict(), "value": self.value}


class Status(Modifier):
    ACTIVE = "active"
    BLOCKED = "blocked"
    STOPPED = "stopped"
    ARCHIVED = "archived"

    type = "status"

    def __init__(self, status: str):
        self.status = status


class Groups(Modifier):
    type = "groups"

    def __init__(self, groups: List[GroupRef], modification: str):
        self.groups = groups
        self.modification = modification

    def as_def(self) -> Dict:
        return {"type": self.type, "groups": [g._asdict() for g in self.groups], "modification": self.modification}


class URNs(Modifier):
    type = "urns"

    def __init__(self, urns: List[str], modification: str):
        self.urns = urns
        self.modification = modification
