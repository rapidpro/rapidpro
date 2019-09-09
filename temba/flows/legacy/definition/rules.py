from decimal import Decimal

import phonenumbers
import regex
from temba_expressions.utils import tokenize

from temba.contacts.models import ContactGroup
from temba.locations.models import AdminBoundary
from temba.utils.dates import str_to_datetime
from temba.utils.email import is_valid_address

from ..expressions import evaluate


class Rule:
    def __init__(self, uuid, category, destination, destination_type, test, label=None):
        self.uuid = uuid
        self.category = category
        self.destination = destination
        self.destination_type = destination_type
        self.test = test
        self.label = label

    def get_category_name(self, flow_lang, contact_lang=None):
        if not self.category:  # pragma: needs cover
            if isinstance(self.test, BetweenTest):
                return "%s-%s" % (self.test.min, self.test.max)

        # return the category name for the flow language version
        if isinstance(self.category, dict):
            category = None
            if contact_lang:
                category = self.category.get(contact_lang)

            if not category and flow_lang:
                category = self.category.get(flow_lang)

            if not category:  # pragma: needs cover
                category = list(self.category.values())[0]

            return category

        return self.category  # pragma: needs cover

    def matches(self, run, sms, context, text):
        return self.test.evaluate(run, sms, context, text)

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

    def evaluate(self, run, sms, context, text):  # pragma: no cover
        pass


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

    def evaluate(self, run, sms, context, text):  # pragma: no cover
        # we treat any 20* return code as successful
        success = 200 <= int(text) < 300

        if success and self.status == WebhookStatusTest.STATUS_SUCCESS:
            return 1, text
        elif not success and self.status == WebhookStatusTest.STATUS_FAILURE:
            return 1, text
        else:
            return 0, None


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

    def evaluate(self, run, sms, context, text):  # pragma: no cover
        pass


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

    def evaluate(self, run, sms, context, text):  # pragma: no cover
        if run.contact.user_groups.filter(id=self.group.id).first():
            return 1, self.group.name
        return 0, None


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

    def evaluate(self, run, sms, context, text):  # pragma: no cover
        if self.exit_type == text:
            return 1, self.exit_type
        return 0, None


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

    def evaluate(self, run, sms, context, text):  # pragma: no cover
        pass


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

    def evaluate(self, run, sms, context, text):  # pragma: no cover
        return 1, text


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

    def evaluate(self, run, sms, context, text):  # pragma: no cover
        return 0, None


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

    def evaluate(self, run, sms, context, text):  # pragma: no cover
        matches = []
        for test in self.tests:
            (result, value) = test.evaluate(run, sms, context, text)
            if result:
                matches.append(value)
            else:
                return 0, None

        # all came out true, we are true
        return 1, " ".join(matches)


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

    def evaluate(self, run, sms, context, text):  # pragma: no cover
        for test in self.tests:
            (result, value) = test.evaluate(run, sms, context, text)
            if result:
                return result, value

        return 0, None


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

    def evaluate(self, run, sms, context, text):  # pragma: no cover
        if text and len(text.strip()):
            return 1, text.strip()
        return 0, None


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

    def test_in_words(self, test, words, raw_words):
        matches = []
        for index, word in enumerate(words):
            if word == test:
                matches.append(index)
                continue

        return matches

    def evaluate(self, run, sms, context, text):  # pragma: no cover
        from ..engine import get_localized_text

        # substitute any variables
        test = get_localized_text(run.flow, self.test, run.contact)
        test, errors = evaluate(test, context, org=run.flow.org)

        # tokenize our test
        tests = tokenize(test.lower())

        # tokenize our sms
        words = tokenize(text.lower())
        raw_words = tokenize(text)

        tests = [elt for elt in tests if elt != ""]
        words = [elt for elt in words if elt != ""]
        raw_words = [elt for elt in raw_words if elt != ""]

        # run through each of our tests
        matches = set()
        matched_tests = 0
        for test in tests:
            match = self.test_in_words(test, words, raw_words)
            if match:
                matched_tests += 1
                matches.update(match)

        # we are a match only if every test matches
        if matched_tests == len(tests):
            matches = sorted(list(matches))
            matched_words = " ".join([raw_words[idx] for idx in matches])
            return len(tests), matched_words
        else:
            return 0, None


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

    def evaluate(self, run, sms, context, text):
        # split on whitespace
        words = text.split()
        for word in words:
            word = word.strip(",.;:|()[]\"'<>?&*/\\")
            if is_valid_address(word):
                return 1, word

        return 0, None


class ContainsAnyTest(ContainsTest):
    """
    { op: "contains_any", "test": "red" }
    """

    TEST = "test"
    TYPE = "contains_any"

    def as_json(self):
        return dict(type=ContainsAnyTest.TYPE, test=self.test)

    def evaluate(self, run, sms, context, text):  # pragma: no cover
        from ..engine import get_localized_text

        # substitute any variables
        test = get_localized_text(run.flow, self.test, run.contact)
        test, errors = evaluate(test, context, org=run.flow.org)

        # tokenize our test
        tests = tokenize(test.lower())

        # tokenize our sms
        words = tokenize(text.lower())
        raw_words = tokenize(text)

        tests = [elt for elt in tests if elt != ""]
        words = [elt for elt in words if elt != ""]
        raw_words = [elt for elt in raw_words if elt != ""]

        # run through each of our tests
        matches = set()
        for test in tests:
            match = self.test_in_words(test, words, raw_words)
            if match:
                matches.update(match)

        # we are a match if at least one test matches
        if matches:
            matches = sorted(list(matches))
            matched_words = " ".join([raw_words[idx] for idx in matches])
            return 1, matched_words
        else:
            return 0, None


class ContainsOnlyPhraseTest(ContainsTest):
    """
    { op: "contains_only_phrase", "test": "red" }
    """

    TEST = "test"
    TYPE = "contains_only_phrase"

    def as_json(self):  # pragma: no cover
        return dict(type=ContainsOnlyPhraseTest.TYPE, test=self.test)

    def evaluate(self, run, sms, context, text):  # pragma: no cover
        from ..engine import get_localized_text

        # substitute any variables
        test = get_localized_text(run.flow, self.test, run.contact)
        test, errors = evaluate(test, context, org=run.flow.org)

        # tokenize our test
        tests = tokenize(test.lower())

        # tokenize our sms
        words = tokenize(text.lower())
        raw_words = tokenize(text)

        # they are the same? then we matched
        if tests == words:
            return 1, " ".join(raw_words)
        else:
            return 0, None


class ContainsPhraseTest(ContainsTest):
    """
    { op: "contains_phrase", "test": "red" }
    """

    TEST = "test"
    TYPE = "contains_phrase"

    def as_json(self):  # pragma: no cover
        return dict(type=ContainsPhraseTest.TYPE, test=self.test)

    def evaluate(self, run, sms, context, text):  # pragma: no cover
        from ..engine import get_localized_text

        # substitute any variables
        test = get_localized_text(run.flow, self.test, run.contact)
        test, errors = evaluate(test, context, org=run.flow.org)

        # tokenize our test
        tests = tokenize(test.lower())
        if not tests:
            return True, ""

        # tokenize our sms
        words = tokenize(text.lower())
        raw_words = tokenize(text)

        # look for the phrase
        test_idx = 0
        matches = []
        for i in range(len(words)):
            if tests[test_idx] == words[i]:
                matches.append(raw_words[i])
                test_idx += 1
                if test_idx == len(tests):
                    break
            else:
                matches = []
                test_idx = 0

        # we found the phrase
        if test_idx == len(tests):
            matched_words = " ".join(matches)
            return 1, matched_words
        else:
            return 0, None


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

    def evaluate(self, run, sms, context, text):  # pragma: no cover
        from ..engine import get_localized_text

        # substitute any variables in our test
        test = get_localized_text(run.flow, self.test, run.contact)
        test, errors = evaluate(test, context, org=run.flow.org)

        # strip leading and trailing whitespace
        text = text.strip()

        # see whether we start with our test
        if text.lower().find(test.lower()) == 0:
            return 1, text[: len(test)]
        else:
            return 0, None


class HasStateTest(Test):
    TYPE = "state"

    def __init__(self):
        pass

    @classmethod
    def from_json(cls, org, json):
        return cls()

    def as_json(self):
        return dict(type=self.TYPE)

    def evaluate(self, run, sms, context, text):  # pragma: no cover
        org = run.flow.org

        # if they removed their country since adding the rule
        if not org.country:
            return 0, None

        state = org.parse_location(text, AdminBoundary.LEVEL_STATE)
        if state:
            return 1, state[0]

        return 0, None


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

    def evaluate(self, run, sms, context, text):  # pragma: no cover
        # if they removed their country since adding the rule
        org = run.flow.org
        if not org.country:
            return 0, None

        # evaluate our district in case it has a replacement variable
        state, errors = evaluate(self.state, context, org=run.flow.org)

        parent = org.parse_location(state, AdminBoundary.LEVEL_STATE)
        if parent:
            district = org.parse_location(text, AdminBoundary.LEVEL_DISTRICT, parent[0])
            if district:
                return 1, district[0]
        district = org.parse_location(text, AdminBoundary.LEVEL_DISTRICT)

        # parse location when state contraint is not provided or available
        if (errors or not state) and len(district) == 1:
            return 1, district[0]

        return 0, None


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

    def evaluate(self, run, sms, context, text):  # pragma: no cover
        # if they removed their country since adding the rule
        org = run.flow.org
        if not org.country:  # pragma: needs cover
            return 0, None
        district = None

        # evaluate our district in case it has a replacement variable
        district_name, missing_district = evaluate(self.district, context, org=run.flow.org)
        state_name, missing_state = evaluate(self.state, context, org=run.flow.org)
        if (district_name and state_name) and (len(missing_district) == 0 and len(missing_state) == 0):
            state = org.parse_location(state_name, AdminBoundary.LEVEL_STATE)
            if state:
                district = org.parse_location(district_name, AdminBoundary.LEVEL_DISTRICT, state[0])
                if district:
                    ward = org.parse_location(text, AdminBoundary.LEVEL_WARD, district[0])
                    if ward:
                        return 1, ward[0]

        # parse location when district contraint is not provided or available
        ward = org.parse_location(text, AdminBoundary.LEVEL_WARD)
        if len(ward) == 1 and district is None:
            return 1, ward[0]

        return 0, None


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

    def evaluate_date_test(self, date_message, date_test):
        return date_message is not None

    def evaluate(self, run, sms, context, text):  # pragma: no cover
        org = run.flow.org
        day_first = org.get_dayfirst()
        tz = org.timezone

        test, errors = evaluate(self.test, context, org=org)
        if not errors:
            date_message = str_to_datetime(text, tz=tz, dayfirst=day_first)
            date_test = str_to_datetime(test, tz=tz, dayfirst=day_first)

            if self.evaluate_date_test(date_message, date_test):
                return 1, date_message.astimezone(tz)

        return 0, None


class DateEqualTest(DateTest):
    TEST = "test"
    TYPE = "date_equal"

    def evaluate_date_test(self, date_message, date_test):  # pragma: no cover
        return date_message and date_test and date_message.date() == date_test.date()


class DateAfterTest(DateTest):
    TEST = "test"
    TYPE = "date_after"

    def evaluate_date_test(self, date_message, date_test):  # pragma: no cover
        return date_message and date_test and date_message >= date_test


class DateBeforeTest(DateTest):
    TEST = "test"
    TYPE = "date_before"

    def evaluate_date_test(self, date_message, date_test):  # pragma: no cover
        return date_message and date_test and date_message <= date_test


class NumericTest(Test):
    """
    Base class for those tests that do numeric tests.
    """

    TEST = "test"
    TYPE = ""

    @classmethod
    def convert_to_decimal(cls, word):  # pragma: no cover
        try:
            return (word, Decimal(word))
        except Exception as e:
            # does this start with a number?  just use that part if so
            match = regex.match(r"^[$£€]?([\d,][\d,\.]*([\.,]\d+)?)\D*$", word, regex.UNICODE | regex.V0)

            if match:
                return (match.group(1), Decimal(match.group(1)))
            else:
                raise e

    # test every word in the message against our test
    def evaluate(self, run, sms, context, text):  # pragma: no cover
        text = text.replace(",", "")
        for word in regex.split(r"\s+", text, flags=regex.UNICODE | regex.V0):
            try:
                (word, decimal) = NumericTest.convert_to_decimal(word)
                if self.evaluate_numeric_test(run, context, decimal):
                    return 1, decimal
            except Exception:  # pragma: needs cover
                pass
        return 0, None


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

    def evaluate_numeric_test(self, run, context, decimal_value):  # pragma: no cover
        min_val, min_errors = evaluate(self.min, context, org=run.flow.org)
        max_val, max_errors = evaluate(self.max, context, org=run.flow.org)

        if not min_errors and not max_errors:
            try:
                return Decimal(min_val) <= decimal_value <= Decimal(max_val)
            except Exception:
                pass

        return False


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

    def evaluate_numeric_test(self, run, context, decimal_value):  # pragma: no cover
        return True


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

    def evaluate_numeric_test(self, message_numeric, test_numeric):  # pragma: no cover
        pass

    def evaluate(self, run, sms, context, text):  # pragma: no cover
        test, errors = evaluate(str(self.test), context, org=run.flow.org)

        text = text.replace(",", "")
        for word in regex.split(r"\s+", text, flags=regex.UNICODE | regex.V0):
            try:
                (word, decimal) = NumericTest.convert_to_decimal(word)
                if self.evaluate_numeric_test(decimal, Decimal(test)):
                    return 1, decimal
            except Exception:
                pass
        return 0, None


class GtTest(SimpleNumericTest):
    TEST = "test"
    TYPE = "gt"

    def evaluate_numeric_test(self, message_numeric, test_numeric):  # pragma: no cover
        return message_numeric > test_numeric


class GteTest(SimpleNumericTest):
    TEST = "test"
    TYPE = "gte"

    def evaluate_numeric_test(self, message_numeric, test_numeric):  # pragma: no cover
        return message_numeric >= test_numeric


class LtTest(SimpleNumericTest):
    TEST = "test"
    TYPE = "lt"

    def evaluate_numeric_test(self, message_numeric, test_numeric):  # pragma: no cover
        return message_numeric < test_numeric


class LteTest(SimpleNumericTest):
    TEST = "test"
    TYPE = "lte"

    def evaluate_numeric_test(self, message_numeric, test_numeric):  # pragma: no cover
        return message_numeric <= test_numeric


class EqTest(SimpleNumericTest):
    TEST = "test"
    TYPE = "eq"

    def evaluate_numeric_test(self, message_numeric, test_numeric):  # pragma: no cover
        return message_numeric == test_numeric


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

    def evaluate(self, run, sms, context, text):  # pragma: no cover
        org = run.flow.org

        # try to find a phone number in the text we have been sent
        country_code = org.get_country_code()
        if not country_code:  # pragma: needs cover
            country_code = "US"

        number = None
        matches = phonenumbers.PhoneNumberMatcher(text, country_code)

        # try it as an international number if we failed
        if not matches.has_next():  # pragma: needs cover
            matches = phonenumbers.PhoneNumberMatcher("+" + text, country_code)

        for match in matches:
            number = phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164)

        return number, number


class RegexTest(Test):  # pragma: no cover
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

    def evaluate(self, run, sms, context, text):
        from ..engine import get_localized_text, _update_run_fields

        try:
            test = get_localized_text(run.flow, self.test, run.contact)

            # check whether we match
            rexp = regex.compile(test, regex.UNICODE | regex.IGNORECASE | regex.MULTILINE | regex.V0)
            match = rexp.search(text)

            # if so, $0 will be what we return
            if match:
                return_match = match.group(0)

                # build up a dictionary that contains indexed group matches
                group_dict = {}
                for idx in range(rexp.groups + 1):
                    group_dict[str(idx)] = match.group(idx)

                # set it on run@extra
                _update_run_fields(run, group_dict)

                # return all matched values
                return True, return_match

        except Exception:
            pass

        return False, None
