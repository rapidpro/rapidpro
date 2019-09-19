from temba.contacts.models import ContactGroup


class Rule:
    def __init__(self, uuid, category, destination, destination_type, test, label=None):
        self.uuid = uuid
        self.category = category
        self.destination = destination
        self.destination_type = destination_type
        self.test = test
        self.label = label

    def as_json(self):
        return dict(
            uuid=self.uuid,
            category=self.category,
            destination=self.destination,
            destination_type=self.destination_type,
            test=self.test.as_json(),
            label=self.label,
        )

    @classmethod
    def from_json_array(cls, org, json):
        from temba.flows.models import Flow

        rules = []
        for rule in json:
            category = rule.get("category", None)

            if isinstance(category, dict):
                # prune all of our translations to 36
                for k, v in category.items():
                    if isinstance(v, str):
                        category[k] = v[:36]
            elif category:
                category = category[:36]

            destination = rule.get("destination", None)
            destination_type = None

            # determine our destination type, if its not set its an action set
            if destination:
                destination_type = rule.get("destination_type", Flow.NODE_TYPE_ACTIONSET)

            rules.append(
                Rule(
                    rule.get("uuid"),
                    category,
                    destination,
                    destination_type,
                    Test.from_json(org, rule["test"]),
                    rule.get("label"),
                )
            )

        return rules


class Test:
    TYPE = "type"
    __test_mapping = None

    @classmethod
    def from_json(cls, org, json_dict):
        if not cls.__test_mapping:
            cls.__test_mapping = {
                AirtimeStatusTest.TYPE: AirtimeStatusTest,
                AndTest.TYPE: AndTest,
                BetweenTest.TYPE: BetweenTest,
                ContainsAnyTest.TYPE: ContainsAnyTest,
                ContainsOnlyPhraseTest.TYPE: ContainsOnlyPhraseTest,
                ContainsPhraseTest.TYPE: ContainsPhraseTest,
                ContainsTest.TYPE: ContainsTest,
                DateAfterTest.TYPE: DateAfterTest,
                DateBeforeTest.TYPE: DateBeforeTest,
                DateEqualTest.TYPE: DateEqualTest,
                EqTest.TYPE: EqTest,
                FalseTest.TYPE: FalseTest,
                GtTest.TYPE: GtTest,
                GteTest.TYPE: GteTest,
                DateTest.TYPE: DateTest,
                HasDistrictTest.TYPE: HasDistrictTest,
                HasEmailTest.TYPE: HasEmailTest,
                HasStateTest.TYPE: HasStateTest,
                HasWardTest.TYPE: HasWardTest,
                InGroupTest.TYPE: InGroupTest,
                LtTest.TYPE: LtTest,
                LteTest.TYPE: LteTest,
                NotEmptyTest.TYPE: NotEmptyTest,
                NumberTest.TYPE: NumberTest,
                OrTest.TYPE: OrTest,
                PhoneTest.TYPE: PhoneTest,
                RegexTest.TYPE: RegexTest,
                StartsWithTest.TYPE: StartsWithTest,
                SubflowTest.TYPE: SubflowTest,
                TimeoutTest.TYPE: TimeoutTest,
                TrueTest.TYPE: TrueTest,
                WebhookStatusTest.TYPE: WebhookStatusTest,
            }

        type = json_dict.get(cls.TYPE, None)
        return cls.__test_mapping[type].from_json(org, json_dict)

    @classmethod
    def from_json_array(cls, org, json):
        tests = []
        for inner in json:
            tests.append(Test.from_json(org, inner))

        return tests


class WebhookStatusTest(Test):
    """
    {op: 'webhook', status: 'success' }
    """

    TYPE = "webhook_status"
    STATUS = "status"

    STATUS_SUCCESS = "success"
    STATUS_FAILURE = "failure"

    def __init__(self, status):
        self.status = status

    @classmethod
    def from_json(cls, org, json):
        return WebhookStatusTest(json.get("status"))

    def as_json(self):  # pragma: needs cover
        return dict(type=WebhookStatusTest.TYPE, status=self.status)


class AirtimeStatusTest(Test):
    """
    {op: 'airtime_status'}
    """

    TYPE = "airtime_status"
    EXIT = "exit_status"

    def __init__(self, exit_status):
        self.exit_status = exit_status

    @classmethod
    def from_json(cls, org, json):
        return AirtimeStatusTest(json.get("exit_status"))

    def as_json(self):  # pragma: needs cover
        return dict(type=AirtimeStatusTest.TYPE, exit_status=self.exit_status)


class InGroupTest(Test):
    """
    { op: "in_group" }
    """

    TYPE = "in_group"
    NAME = "name"
    UUID = "uuid"
    TEST = "test"

    def __init__(self, group):
        self.group = group

    @classmethod
    def from_json(cls, org, json):
        group = json.get(InGroupTest.TEST)
        name = group.get(InGroupTest.NAME)
        uuid = group.get(InGroupTest.UUID)
        return InGroupTest(ContactGroup.get_or_create(org, org.created_by, name, uuid=uuid))

    def as_json(self):
        group = ContactGroup.get_or_create(
            self.group.org, self.group.org.created_by, self.group.name, uuid=self.group.uuid
        )
        return dict(type=InGroupTest.TYPE, test=dict(name=group.name, uuid=group.uuid))


class SubflowTest(Test):
    """
    { op: "subflow" }
    """

    TYPE = "subflow"
    EXIT = "exit_type"

    TYPE_COMPLETED = "completed"
    TYPE_EXPIRED = "expired"

    def __init__(self, exit_type):
        self.exit_type = exit_type

    @classmethod
    def from_json(cls, org, json):
        return SubflowTest(json.get(SubflowTest.EXIT))

    def as_json(self):  # pragma: needs cover
        return dict(type=SubflowTest.TYPE, exit_type=self.exit_type)


class TimeoutTest(Test):
    """
    { op: "timeout", minutes: 60 }
    """

    TYPE = "timeout"
    MINUTES = "minutes"

    def __init__(self, minutes):
        self.minutes = minutes

    @classmethod
    def from_json(cls, org, json):
        return TimeoutTest(int(json.get(TimeoutTest.MINUTES)))

    def as_json(self):  # pragma: no cover
        return {"type": TimeoutTest.TYPE, TimeoutTest.MINUTES: self.minutes}


class TrueTest(Test):
    """
    { op: "true" }
    """

    TYPE = "true"

    def __init__(self):
        pass

    @classmethod
    def from_json(cls, org, json):
        return TrueTest()

    def as_json(self):
        return dict(type=TrueTest.TYPE)


class FalseTest(Test):
    """
    { op: "false" }
    """

    TYPE = "false"

    def __init__(self):
        pass

    @classmethod
    def from_json(cls, org, json):
        return FalseTest()

    def as_json(self):
        return dict(type=FalseTest.TYPE)


class AndTest(Test):
    """
    { op: "and",  "tests": [ ... ] }
    """

    TESTS = "tests"
    TYPE = "and"

    def __init__(self, tests):
        self.tests = tests

    @classmethod
    def from_json(cls, org, json):
        return AndTest(Test.from_json_array(org, json[cls.TESTS]))

    def as_json(self):
        return dict(type=AndTest.TYPE, tests=[_.as_json() for _ in self.tests])


class OrTest(Test):
    """
    { op: "or",  "tests": [ ... ] }
    """

    TESTS = "tests"
    TYPE = "or"

    def __init__(self, tests):
        self.tests = tests

    @classmethod
    def from_json(cls, org, json):
        return OrTest(Test.from_json_array(org, json[cls.TESTS]))

    def as_json(self):
        return dict(type=OrTest.TYPE, tests=[_.as_json() for _ in self.tests])


class NotEmptyTest(Test):
    """
    { op: "not_empty" }
    """

    TYPE = "not_empty"

    def __init__(self):  # pragma: needs cover
        pass

    @classmethod
    def from_json(cls, org, json):  # pragma: needs cover
        return NotEmptyTest()

    def as_json(self):  # pragma: needs cover
        return dict(type=NotEmptyTest.TYPE)


class ContainsTest(Test):
    """
    { op: "contains", "test": "red" }
    """

    TEST = "test"
    TYPE = "contains"

    def __init__(self, test):
        self.test = test

    @classmethod
    def from_json(cls, org, json):
        return cls(json[cls.TEST])

    def as_json(self):
        json = dict(type=ContainsTest.TYPE, test=self.test)
        return json


class HasEmailTest(Test):  # pragma: no cover
    """
    { op: "has_email" }
    """

    TYPE = "has_email"

    def __init__(self):
        pass

    @classmethod
    def from_json(cls, org, json):
        return cls()

    def as_json(self):
        return dict(type=self.TYPE)


class ContainsAnyTest(ContainsTest):
    """
    { op: "contains_any", "test": "red" }
    """

    TEST = "test"
    TYPE = "contains_any"

    def as_json(self):
        return dict(type=ContainsAnyTest.TYPE, test=self.test)


class ContainsOnlyPhraseTest(ContainsTest):
    """
    { op: "contains_only_phrase", "test": "red" }
    """

    TEST = "test"
    TYPE = "contains_only_phrase"

    def as_json(self):  # pragma: no cover
        return dict(type=ContainsOnlyPhraseTest.TYPE, test=self.test)


class ContainsPhraseTest(ContainsTest):
    """
    { op: "contains_phrase", "test": "red" }
    """

    TEST = "test"
    TYPE = "contains_phrase"

    def as_json(self):  # pragma: no cover
        return dict(type=ContainsPhraseTest.TYPE, test=self.test)


class StartsWithTest(Test):
    """
    { op: "starts", "test": "red" }
    """

    TEST = "test"
    TYPE = "starts"

    def __init__(self, test):
        self.test = test

    @classmethod
    def from_json(cls, org, json):
        return cls(json[cls.TEST])

    def as_json(self):  # pragma: needs cover
        return dict(type=StartsWithTest.TYPE, test=self.test)


class HasStateTest(Test):
    TYPE = "state"

    def __init__(self):
        pass

    @classmethod
    def from_json(cls, org, json):
        return cls()

    def as_json(self):
        return dict(type=self.TYPE)


class HasDistrictTest(Test):
    TYPE = "district"
    TEST = "test"

    def __init__(self, state=None):
        self.state = state

    @classmethod
    def from_json(cls, org, json):
        return cls(json[cls.TEST])

    def as_json(self):
        return dict(type=self.TYPE, test=self.state)


class HasWardTest(Test):
    TYPE = "ward"
    STATE = "state"
    DISTRICT = "district"

    def __init__(self, state=None, district=None):
        self.state = state
        self.district = district

    @classmethod
    def from_json(cls, org, json):
        return cls(json[cls.STATE], json[cls.DISTRICT])

    def as_json(self):
        return dict(type=self.TYPE, state=self.state, district=self.district)


class DateTest(Test):
    """
    Base class for those tests that check relative dates
    """

    TEST = None
    TYPE = "date"

    def __init__(self, test=None):
        self.test = test

    @classmethod
    def from_json(cls, org, json):
        if cls.TEST:
            return cls(json[cls.TEST])
        else:
            return cls()

    def as_json(self):
        if self.test:
            return dict(type=self.TYPE, test=self.test)
        else:
            return dict(type=self.TYPE)


class DateEqualTest(DateTest):
    TEST = "test"
    TYPE = "date_equal"


class DateAfterTest(DateTest):
    TEST = "test"
    TYPE = "date_after"


class DateBeforeTest(DateTest):
    TEST = "test"
    TYPE = "date_before"


class NumericTest(Test):
    """
    Base class for those tests that do numeric tests.
    """

    TEST = "test"
    TYPE = ""


class BetweenTest(NumericTest):
    """
    Test whether we are between two numbers (inclusive)
    """

    MIN = "min"
    MAX = "max"
    TYPE = "between"

    def __init__(self, min_val, max_val):
        self.min = min_val
        self.max = max_val

    @classmethod
    def from_json(cls, org, json):
        return cls(json[cls.MIN], json[cls.MAX])

    def as_json(self):
        return dict(type=self.TYPE, min=self.min, max=self.max)


class NumberTest(NumericTest):
    """
    Tests that there is any number in the string.
    """

    TYPE = "number"

    def __init__(self):
        pass

    @classmethod
    def from_json(cls, org, json):
        return cls()

    def as_json(self):  # pragma: needs cover
        return dict(type=self.TYPE)


class SimpleNumericTest(NumericTest):
    """
    Base class for those tests that do a numeric test with a single value
    """

    TEST = "test"
    TYPE = ""

    def __init__(self, test):
        self.test = test

    @classmethod
    def from_json(cls, org, json):
        return cls(json[cls.TEST])

    def as_json(self):
        return dict(type=self.TYPE, test=self.test)


class GtTest(SimpleNumericTest):
    TEST = "test"
    TYPE = "gt"


class GteTest(SimpleNumericTest):
    TEST = "test"
    TYPE = "gte"


class LtTest(SimpleNumericTest):
    TEST = "test"
    TYPE = "lt"


class LteTest(SimpleNumericTest):
    TEST = "test"
    TYPE = "lte"


class EqTest(SimpleNumericTest):
    TEST = "test"
    TYPE = "eq"


class PhoneTest(Test):
    """
    Test for whether a response contains a phone number
    """

    TYPE = "phone"

    def __init__(self):
        pass

    @classmethod
    def from_json(cls, org, json):
        return cls()

    def as_json(self):  # pragma: needs cover
        return dict(type=self.TYPE)


class RegexTest(Test):
    """
    Test for whether a response matches a regular expression
    """

    TEST = "test"
    TYPE = "regex"

    def __init__(self, test):
        self.test = test

    @classmethod
    def from_json(cls, org, json):
        return cls(json[cls.TEST])

    def as_json(self):
        return dict(type=self.TYPE, test=self.test)
