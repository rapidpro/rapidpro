from typing import Dict, List, NamedTuple


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


class Status(Modifier):
    type = "status"

    def __init__(self, status: str):
        self.status = status


class Groups(Modifier):
    type = "groups"

    def __init__(self, modification: str, groups: List[GroupRef]):
        self.modification = modification
        self.groups = groups

    def as_def(self) -> Dict:
        return {"type": self.type, "modification": self.modification, "groups": [g._asdict() for g in self.groups]}
