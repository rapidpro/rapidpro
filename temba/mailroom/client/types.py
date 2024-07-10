from dataclasses import dataclass, field


@dataclass
class ContactSpec:
    """
    Describes a contact to be created
    """

    name: str
    language: str
    status: str
    urns: list[str]
    fields: dict[str, str]
    groups: list[str]


@dataclass
class Inclusions:
    group_uuids: list = field(default_factory=list)
    contact_uuids: list = field(default_factory=list)
    query: str = ""


@dataclass
class Exclusions:
    non_active: bool = False  # contacts who are blocked, stopped or archived
    in_a_flow: bool = False  # contacts who are currently in a flow (including this one)
    started_previously: bool = False  # contacts who have been in this flow in the last 90 days
    not_seen_since_days: int = 0  # contacts who have not been seen for more than this number of days


@dataclass(frozen=True)
class QueryMetadata:
    """
    Contact query metadata
    """

    attributes: list = field(default_factory=list)
    schemes: list = field(default_factory=list)
    fields: list = field(default_factory=list)
    groups: list = field(default_factory=list)
    allow_as_group: bool = False


@dataclass(frozen=True)
class ParsedQuery:
    query: str
    metadata: QueryMetadata


@dataclass(frozen=True)
class SearchResults:
    query: str
    total: int
    contact_ids: list
    metadata: QueryMetadata


@dataclass(frozen=True)
class RecipientsPreview:
    query: str
    total: int


@dataclass
class URNResult:
    normalized: str
    contact_id: int = None
    error: str = None
    e164: bool = False


@dataclass
class ScheduleSpec:
    """
    Describes a schedule to be created
    """

    start: str
    repeat_period: str
    repeat_days_of_week: str = None
