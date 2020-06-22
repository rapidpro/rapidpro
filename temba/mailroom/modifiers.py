from typing import Dict, List, NamedTuple


class GroupRef(NamedTuple):
    uuid: str
    name: str


class Modifier:
    def as_def(self) -> Dict:
        return self._asdict()


class Name(NamedTuple, Modifier):
    name: str
    type: str = "name"


class Language(NamedTuple, Modifier):
    language: str
    type: str = "language"


class Status(NamedTuple, Modifier):
    status: str
    type: str = "status"


class Groups(NamedTuple, Modifier):
    modification: str
    groups: List[GroupRef]
    type: str = "groups"

    def as_def(self) -> Dict:
        return {"type": self.type, "modification": self.modification, "groups": [g._asdict() for g in self.groups]}
