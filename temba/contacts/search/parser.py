import operator
from collections import OrderedDict
from decimal import Decimal
from functools import reduce
from typing import NamedTuple

import pytz
import regex
from antlr4 import CommonTokenStream, InputStream, ParseTreeVisitor
from antlr4.error.Errors import NoViableAltException, ParseCancellationException
from antlr4.error.ErrorStrategy import BailErrorStrategy

from django.utils.encoding import force_text
from django.utils.translation import ugettext_lazy as _

from temba import mailroom
from temba.contacts.models import URN_SCHEME_CONFIG, ContactField
from temba.utils.dates import date_to_day_range_utc, str_to_date, str_to_datetime
from temba.values.constants import Value

TEL_VALUE_REGEX = regex.compile(r"^[+ \d\-\(\)]+$", flags=regex.V0)
CLEAN_SPECIAL_CHARS_REGEX = regex.compile(r"[+ \-\(\)]+", flags=regex.V0)


class SearchException(Exception):
    """
    Exception class for unparseable search queries
    """

    messages = {
        "unexpected_token": _("Invalid query syntax at '%(token)s'"),
        "invalid_number": _("Unable to convert '%(value)s' to a number"),
        "invalid_date": _("Unable to convert '%(value)s' to a date"),
        "invalid_language": _("'%(value)s' is not a valid language code"),
        "invalid_group": _("'%(value)s' is not a valid group name"),
        "invalid_partial_name": _("Using ~ with name requires token of at least %(min_token_length)s characters"),
        "invalid_partial_urn": _("Using ~ with URN requires value of at least %(min_value_length)s characters"),
        "unsupported_contains": _("Can only use ~ with name or URN values"),
        "unsupported_comparison": _("Can only use %(operator)s with number or date values"),
        "unsupported_setcheck": _("Can't check whether '%(property)s' is set or not set"),
        "unknown_property": _("Can't resolve '%(property)s' to a field or URN scheme"),
        "redacted_urns": _("Can't query on URNs in an anonymous workspace"),
    }

    def __init__(self, message, code=None, extra=None):
        self.message = message
        self.code = code
        self.extra = extra

    @classmethod
    def from_mailroom_exception(cls, e):
        return cls(e.response["error"], e.response.get("code"), e.response.get("extra", {}))

    def __str__(self):
        if self.code and self.code in self.messages:
            return self.messages[self.code] % self.extra

        return force_text(self.message)


class ContactQuery:
    """
    A parsed contact query consisting of a hierarchy of conditions and boolean combinations of conditions
    """

    PROP_ATTRIBUTE = "A"
    PROP_SCHEME = "S"
    PROP_FIELD = "F"

    SEARCHABLE_SCHEMES = [s[0] for s in URN_SCHEME_CONFIG]

    def __init__(self, root):
        self.root = root

    def optimized(self):
        return ContactQuery(self.root.simplify().split_by_prop())

    def as_text(self):
        return self.root.as_text()

    def evaluate(self, org, contact_json):
        prop_map = self.get_prop_map(org)

        return self.root.evaluate(org, contact_json, prop_map)

    def get_prop_map(self, org, validate=True):
        """
        Recursively collects all property names from this query and tries to match them to fields, searchable attributes
        and URN schemes.
        """

        all_props = set(self.root.get_prop_names())

        prop_map = {p: None for p in all_props}

        all_contact_fields = ContactField.all_fields.filter(org=org, key__in=all_props, is_active=True)

        user_contactfields = (cf for cf in all_contact_fields if cf.field_type == ContactField.FIELD_TYPE_USER)
        for field in user_contactfields:
            prop_map[field.key] = (self.PROP_FIELD, field)

        system_contactfields = (cf for cf in all_contact_fields if cf.field_type == ContactField.FIELD_TYPE_SYSTEM)
        for attr in system_contactfields:
            prop_map[attr.key] = (self.PROP_ATTRIBUTE, attr)

        prop_map["uuid"] = (self.PROP_ATTRIBUTE, ContactField(key="uuid"))
        prop_map["urn"] = (self.PROP_ATTRIBUTE, ContactField(key="urn"))

        for scheme in self.SEARCHABLE_SCHEMES:
            if scheme in prop_map.keys():
                prop_map[scheme] = (self.PROP_SCHEME, scheme)

        if validate:
            for prop, prop_obj in prop_map.items():
                if not prop_obj:
                    raise SearchException(f"Unrecognized field: '{prop}'")

        return prop_map

    def __eq__(self, other):
        return isinstance(other, ContactQuery) and self.root == other.root

    def __str__(self):
        return str(self.root)

    def __repr__(self):
        return "ContactQuery{%s}" % str(self)


class QueryNode(object):
    """
    A search query node which is either a condition or a boolean combination of other conditions
    """

    def simplify(self):
        return self

    def split_by_prop(self):
        return self

    def as_text(self):  # pragma: no cover
        pass

    def evaluate(self, org, contact_json, prop_map):  # pragma: no cover
        pass


class Condition(QueryNode):
    COMPARATOR_ALIASES = {"is": "=", "has": "~"}

    def __init__(self, prop, comparator, value):
        self.prop = prop
        self.comparator = self.COMPARATOR_ALIASES[comparator] if comparator in self.COMPARATOR_ALIASES else comparator
        self.value = value

    def get_prop_names(self):
        return [self.prop]

    @staticmethod
    def _parse_number(val):
        try:
            return Decimal(val)
        except Exception:
            raise SearchException(f"'{val}' isn't a valid number")

    def as_text(self):
        try:
            Decimal(self.value)
            is_decimal = True
        except Exception:
            is_decimal = False

        value = self.value if is_decimal else '"%s"' % self.value

        return f"{self.prop} {self.comparator} {value}"

    def evaluate(self, org, contact_json, prop_map):
        prop_type, field = prop_map[self.prop]

        if prop_type == ContactQuery.PROP_FIELD:
            field_uuid = str(field.uuid)
            contact_fields = contact_json.get("fields", {})

            if field.value_type == Value.TYPE_TEXT:
                query_value = self.value.upper()
                contact_value = contact_fields.get(field_uuid, {"text": ""}).get("text").upper()

                if self.comparator == "=":
                    return contact_value == query_value
                elif self.comparator == "!=":
                    return contact_value != query_value
                else:
                    raise SearchException(f"Unknown text comparator: '{self.comparator}'")

            elif field.value_type == Value.TYPE_NUMBER:
                query_value = self._parse_number(self.value)

                number_value = contact_fields.get(field_uuid, {"number": None}).get(
                    "number", contact_fields.get(field_uuid, {"decimal": None}).get("decimal")
                )
                if number_value is None:
                    return False

                contact_value = self._parse_number(number_value)

                if self.comparator == "=":
                    return contact_value == query_value
                elif self.comparator == ">":
                    return contact_value > query_value
                elif self.comparator == ">=":
                    return contact_value >= query_value
                elif self.comparator == "<":
                    return contact_value < query_value
                elif self.comparator == "<=":
                    return contact_value <= query_value
                else:
                    raise SearchException(f"Unknown number comparator: '{self.comparator}'")

            elif field.value_type == Value.TYPE_DATETIME:
                query_value = str_to_date(self.value, field.org.get_dayfirst())
                if not query_value:
                    raise SearchException(f"Unable to parse the date '{self.value}'")

                lower_bound, upper_bound = date_to_day_range_utc(query_value, org)

                contact_datetime_value = contact_fields.get(field_uuid, {"datetime": None}).get("datetime")
                if contact_datetime_value is None:
                    return False

                # datetime contact values are serialized as ISO8601 timestamps in local time
                contact_value = str_to_datetime(contact_datetime_value, pytz.UTC, field.org.get_dayfirst())
                contact_value_utc = contact_value.astimezone(pytz.UTC)

                if self.comparator == "=":
                    return contact_value_utc >= lower_bound and contact_value_utc < upper_bound
                elif self.comparator == ">":
                    return contact_value_utc >= upper_bound
                elif self.comparator == ">=":
                    return contact_value_utc >= lower_bound
                elif self.comparator == "<":
                    return contact_value_utc < lower_bound
                elif self.comparator == "<=":
                    return contact_value_utc < upper_bound
                else:
                    raise SearchException(f"Unknown datetime comparator: '{self.comparator}'")

            elif field.value_type in (Value.TYPE_STATE, Value.TYPE_DISTRICT, Value.TYPE_WARD):
                query_value = self.value.upper()

                if field.value_type == Value.TYPE_WARD:
                    ward_value = contact_fields.get(field_uuid, {"ward": ""}).get("ward", "")

                    contact_value = ward_value.upper().split(" > ")[-1]
                elif field.value_type == Value.TYPE_DISTRICT:
                    district_value = contact_fields.get(field_uuid, {"district": ""}).get("district", "")

                    contact_value = district_value.upper().split(" > ")[-1]
                elif field.value_type == Value.TYPE_STATE:
                    state_value = contact_fields.get(field_uuid, {"state": ""}).get("state", "")

                    contact_value = state_value.upper().split(" > ")[-1]
                else:  # pragma: no cover
                    raise SearchException(f"Unknown location type: '{field.value_type}'")

                if self.comparator == "=":
                    return contact_value == query_value
                elif self.comparator == "!=":
                    return contact_value != query_value
                else:
                    raise SearchException(f"Unsupported comparator '{self.comparator}' for location field")

            else:  # pragma: no cover
                raise SearchException(f"Unrecognized contact field type '{field.value_type}'")

        elif prop_type == ContactQuery.PROP_SCHEME:
            if org.is_anon:
                raise SearchException("Cannot query on redacted URNs")

            for urn in contact_json.get("urns"):
                if urn.get("scheme") == field:
                    contact_value = urn.get("path").upper()
                    query_value = self.value.upper()

                    if self.comparator == "=":
                        if contact_value == query_value:
                            return True
                    elif self.comparator == "~":
                        if query_value in contact_value:
                            return True
                    else:
                        raise SearchException(f"Unknown urn scheme comparator: '{self.comparator}'")

            return False

        elif prop_type == ContactQuery.PROP_ATTRIBUTE:
            field_key = field.key

            if field_key == "language":
                query_value = self.value.upper()
                raw_contact_value = contact_json.get("language")
                if raw_contact_value is None:
                    contact_value = ""
                else:
                    contact_value = raw_contact_value.upper()

                if self.comparator == "=":
                    return contact_value == query_value
                elif self.comparator == "!=":
                    return contact_value != query_value
                else:
                    raise SearchException(f"Unknown language comparator: '{self.comparator}'")

            elif field_key == "created_on":
                query_value = str_to_date(self.value, field.org.get_dayfirst())
                if not query_value:
                    raise SearchException(f"Unable to parse the date '{self.value}'")

                lower_bound, upper_bound = date_to_day_range_utc(query_value, org)

                # contact created_on is serialized as ISO8601 timestamp in utc time
                contact_value = str_to_datetime(contact_json.get("created_on"), pytz.UTC, field.org.get_dayfirst())
                contact_value_utc = contact_value.astimezone(pytz.UTC)

                if self.comparator == "=":
                    return contact_value_utc >= lower_bound and contact_value_utc < upper_bound
                elif self.comparator == ">":
                    return contact_value_utc >= upper_bound
                elif self.comparator == ">=":
                    return contact_value_utc >= lower_bound
                elif self.comparator == "<":
                    return contact_value_utc < lower_bound
                elif self.comparator == "<=":
                    return contact_value_utc < upper_bound
                else:
                    raise SearchException(f"Unknown created_on comparator: '{self.comparator}'")

            elif field_key == "name":
                query_value = self.value.upper()
                raw_contact_value = contact_json.get("name")
                if raw_contact_value is None:
                    contact_value = ""
                else:
                    contact_value = raw_contact_value.upper()

                if self.comparator == "=":
                    return contact_value == query_value
                elif self.comparator == "~":
                    return query_value in contact_value
                elif self.comparator == "!=":
                    return contact_value != query_value
                else:  # pragma: no cover
                    raise SearchException(f"Unknown name comparator: '{self.comparator}'")

            elif field_key == "urn":
                if org.is_anon:
                    raise SearchException("Cannot query on redacted URNs")

                query_value = self.value.lower()
                if self.comparator == "=":
                    for urn in contact_json.get("urns"):
                        if urn.get("path").lower() == query_value:
                            return True
                    return False
                if self.comparator == "!=":
                    for urn in contact_json.get("urns"):
                        if urn.get("path").lower() == query_value:
                            return False
                    return True
                elif self.comparator == "~":
                    for urn in contact_json.get("urns"):
                        if query_value in urn.get("path").lower():
                            return True
                    return False
                else:
                    raise SearchException(f"Unknown urn comparator: '{self.comparator}'")

            elif field_key == "uuid":
                query_value = self.value.lower()
                contact_value = contact_json["uuid"]

                if self.comparator == "=":
                    return contact_value == query_value
                elif self.comparator == "!=":
                    return contact_value != query_value
                else:
                    raise SearchException(f"Unknown UUID comparator: '{self.comparator}'")

            else:
                raise SearchException(f"No support for attribute field: '{field}'")
        else:  # pragma: no cover
            raise SearchException(f"Unrecognized contact field type '{prop_type}'")

    def __eq__(self, other):
        return (
            isinstance(other, Condition)
            and self.prop == other.prop
            and self.comparator == other.comparator
            and self.value == other.value
        )

    def __str__(self):
        return f"{self.prop}{self.comparator}{self.value}"


class IsSetCondition(Condition):
    """
    A special type of condition which is just checking whether a property is set or not.
      * A condition of the form x != "" is interpreted as "x is set"
      * A condition of the form x = "" is interpreted as "x is not set"
    """

    IS_SET_LOOKUPS = ("!=",)
    IS_NOT_SET_LOOKUPS = ("is", "=")

    def __init__(self, prop, comparator):
        super().__init__(prop, comparator, "")

    def evaluate(self, org, contact_json, prop_map):
        prop_type, field = prop_map[self.prop]

        if self.comparator.lower() in self.IS_SET_LOOKUPS:
            is_set = True
        elif self.comparator.lower() in self.IS_NOT_SET_LOOKUPS:
            is_set = False
        else:  # pragma: no cover
            raise SearchException("Invalid operator for empty string comparison")

        if prop_type == ContactQuery.PROP_FIELD:
            field_uuid = str(field.uuid)
            contact_fields = contact_json.get("fields")

            contact_field = contact_fields.get(field_uuid)

            # contact field does not exist
            if contact_field is None:
                if is_set:
                    return False
                else:
                    return True
            else:
                if field.value_type == Value.TYPE_TEXT:
                    contact_value = contact_field.get("text")
                    if is_set:
                        if contact_value is not None:
                            return True
                        else:  # pragma: can't cover
                            return False
                    else:
                        if contact_value is not None:
                            return False
                        else:  # pragma: can't cover
                            return True
                elif field.value_type == Value.TYPE_NUMBER:
                    try:
                        contact_value = self._parse_number(contact_field.get("decimal", contact_field.get("number")))
                    except SearchException:
                        contact_value = None

                    if is_set:
                        if contact_value is not None:
                            return True
                        else:
                            return False
                    else:
                        if contact_value is not None:
                            return False
                        else:
                            return True

                elif field.value_type == Value.TYPE_DATETIME:
                    contact_value = str_to_date(contact_field.get("datetime"), field.org.get_dayfirst())
                    if is_set:
                        if contact_value is not None:
                            return True
                        else:
                            return False
                    else:
                        if contact_value is not None:
                            return False
                        else:
                            return True

                elif field.value_type == Value.TYPE_WARD:
                    contact_value = contact_field.get("ward")
                    if is_set:
                        if contact_value is not None:
                            return True
                        else:
                            return False
                    else:
                        if contact_value is not None:
                            return False
                        else:
                            return True

                elif field.value_type == Value.TYPE_DISTRICT:
                    contact_value = contact_field.get("district")
                    if is_set:
                        if contact_value is not None:
                            return True
                        else:
                            return False
                    else:
                        if contact_value is not None:
                            return False
                        else:
                            return True

                elif field.value_type == Value.TYPE_STATE:
                    contact_value = contact_field.get("state")
                    if is_set:
                        if contact_value is not None:
                            return True
                        else:
                            return False
                    else:
                        if contact_value is not None:
                            return False
                        else:
                            return True

                else:  # pragma: no cover
                    raise SearchException(f"Unrecognized contact field type '{field.value_type}'")

        elif prop_type == ContactQuery.PROP_SCHEME:
            urn_exists = next((urn for urn in contact_json.get("urns") if urn.get("scheme") == field), None)

            if not urn_exists:
                if is_set:
                    return False
                else:
                    return True
            else:
                if is_set:
                    return True
                else:
                    return False
        elif prop_type == ContactQuery.PROP_ATTRIBUTE:
            field_key = field.key

            if field_key == "language":
                contact_value = contact_json.get("language")
                if is_set:
                    if contact_value is not None:
                        return True
                    else:
                        return False
                else:
                    if contact_value is not None:
                        return False
                    else:
                        return True

            elif field_key == "name":
                contact_value = contact_json.get("name")
                if is_set:
                    if contact_value is not None:
                        return True
                    else:
                        return False
                else:
                    if contact_value is not None:
                        return False
                    else:
                        return True

            elif field_key == "urn":
                if is_set:
                    return bool(contact_json["urns"])
                else:
                    return not bool(contact_json["urns"])

            else:  # pragma: no cover
                raise SearchException(f"No support for attribute field: '{field}'")
        else:  # pragma: no cover
            raise SearchException(f"Unrecognized contact field type '{prop_type}'")


class BoolCombination(QueryNode):
    """
    A combination of two or more conditions using an AND or OR logical operation
    """

    AND = operator.and_
    OR = operator.or_

    def __init__(self, op, *children):
        self.op = op
        self.children = list(children)

    def get_prop_names(self):
        names = []
        for child in self.children:
            names += child.get_prop_names()
        return names

    def simplify(self):
        """
        The expression `x OR y OR z` will be parsed as `OR(OR(x, y), z)` but because the logical operators AND/OR are
        associative we can simplify this as `OR(x, y, z)`.
        """
        self.children = [c.simplify() for c in self.children]  # simplify our children first

        simplified = []

        for child in self.children:
            if isinstance(child, Condition):
                simplified.append(child)
            elif child.op != self.op:
                return self  # can't optimize if children are combined with a different boolean op
            else:
                simplified += child.children

        return BoolCombination(self.op, *simplified)

    def split_by_prop(self):
        """
        The expression `OR(a=1, b=2, a=3)` can be re-arranged to `OR(OR(a=1, a=3), b=2)` so that `a=1 OR a=3` can be
        more efficiently checked using a single query on `a`.
        """
        self.children = [c.split_by_prop() for c in self.children]  # split our children first

        children_by_prop = OrderedDict()
        for child in self.children:
            prop = child.prop if isinstance(child, Condition) else None
            if prop not in children_by_prop:
                children_by_prop[prop] = []
            children_by_prop[prop].append(child)

        new_children = []
        for prop, children in children_by_prop.items():
            if len(children) > 1 and prop is not None:
                new_children.append(SinglePropCombination(prop, self.op, *children))
            else:
                new_children += children

        if len(new_children) == 1:
            return new_children[0]

        return BoolCombination(self.op, *new_children)

    def evaluate(self, org, contact_json, prop_map):
        return reduce(self.op, [child.evaluate(org, contact_json, prop_map) for child in self.children])

    def as_text(self):
        op = " OR " if self.op == self.OR else " AND "
        children = []
        for c in self.children:
            if isinstance(c, BoolCombination):
                children.append("(%s)" % c.as_text())
            else:
                children.append(c.as_text())

        return op.join(children)

    def __eq__(self, other):
        return isinstance(other, BoolCombination) and self.op == other.op and self.children == other.children

    def __str__(self):
        op = "OR" if self.op == self.OR else "AND"
        return "%s(%s)" % (op, ", ".join([str(c) for c in self.children]))


class SinglePropCombination(BoolCombination):
    """
    A special case combination where all conditions are on the same property and so may be optimized to query the value
    table only once.
    """

    def __init__(self, prop, op, *children):
        assert all([isinstance(c, Condition) and c.prop == prop for c in children])

        self.prop = prop

        super().__init__(op, *children)

    def __eq__(self, other):
        return isinstance(other, SinglePropCombination) and self.prop == other.prop and super().__eq__(other)

    def __str__(self):
        op = "OR" if self.op == self.OR else "AND"
        children = ", ".join(f"{c.comparator}{c.value}" for c in self.children)

        return f"{op}[{self.prop}]({children})"


class ContactQLVisitor(ParseTreeVisitor):
    def __init__(self, as_anon):
        self.as_anon = as_anon

    def visitParse(self, ctx):
        return self.visit(ctx.expression())

    def visitImplicitCondition(self, ctx):
        """
        expression : TEXT
        """
        value = ctx.TEXT().getText()

        if self.as_anon:
            try:
                value = int(value)
                return Condition("id", "=", str(value))
            except ValueError:
                pass
        elif TEL_VALUE_REGEX.match(value):
            return Condition("tel", "~", value)

        return Condition("name", "~", value)

    def visitCondition(self, ctx):
        """
        expression : TEXT COMPARATOR literal
        """
        prop = ctx.TEXT().getText().lower()
        comparator = ctx.COMPARATOR().getText().lower()
        value = self.visit(ctx.literal())

        if value == "":
            return IsSetCondition(prop, comparator)
        else:
            return Condition(prop, comparator, value)

    def visitCombinationAnd(self, ctx):
        """
        expression : expression AND expression
        """
        return BoolCombination(BoolCombination.AND, self.visit(ctx.expression(0)), self.visit(ctx.expression(1)))

    def visitCombinationImpicitAnd(self, ctx):
        """
        expression : expression expression
        """
        return BoolCombination(BoolCombination.AND, self.visit(ctx.expression(0)), self.visit(ctx.expression(1)))

    def visitCombinationOr(self, ctx):
        """
        expression : expression OR expression
        """
        return BoolCombination(BoolCombination.OR, self.visit(ctx.expression(0)), self.visit(ctx.expression(1)))

    def visitExpressionGrouping(self, ctx):
        """
        expression : LPAREN expression RPAREN
        """
        return self.visit(ctx.expression())

    def visitTextLiteral(self, ctx):
        """
        literal : TEXT
        """
        return ctx.getText()

    def visitStringLiteral(self, ctx):
        """
        literal : STRING
        """
        value = ctx.getText()[1:-1]
        return value.replace(r"\"", '"')  # unescape embedded quotes


class Metadata(NamedTuple):
    attributes: list = []
    schemes: list = []
    fields: list = []
    groups: list = []
    allow_as_group: bool = False


class ParsedQuery(NamedTuple):
    query: str
    elastic_query: dict
    metadata: Metadata = Metadata()


def parse_query(org_id, query, group_uuid=""):
    """
    Parses the passed in query in the context of the org
    """
    try:
        client = mailroom.get_client()
        response = client.parse_query(org_id, query, group_uuid=str(group_uuid))
        return ParsedQuery(response["query"], response["elastic_query"], Metadata(**response.get("metadata", {})),)

    except mailroom.MailroomException as e:
        raise SearchException.from_mailroom_exception(e)


class SearchResults(NamedTuple):
    total: int
    query: str
    contact_ids: list
    metadata: Metadata = Metadata()


def search_contacts(org_id, group_uuid, query, sort=None, offset=None):
    try:
        client = mailroom.get_client()
        response = client.contact_search(org_id, str(group_uuid), query, sort, offset=offset)
        return SearchResults(
            response["total"], response["query"], response["contact_ids"], Metadata(**response.get("metadata", {})),
        )

    except mailroom.MailroomException as e:
        raise SearchException.from_mailroom_exception(e)


def legacy_parse_query(text, optimize=True, as_anon=False):  # pragma: no cover
    """
    Parses the given contact query and optionally optimizes it
    """
    from .gen.ContactQLLexer import ContactQLLexer
    from .gen.ContactQLParser import ContactQLParser

    is_phone, cleaned_phone = is_phonenumber(text)

    if not as_anon and is_phone:
        stream = InputStream(cleaned_phone)
    else:
        stream = InputStream(text)

    lexer = ContactQLLexer(stream)
    tokens = CommonTokenStream(lexer)
    parser = ContactQLParser(tokens)
    parser._errHandler = BailErrorStrategy()

    try:
        tree = parser.parse()
    except ParseCancellationException as ex:
        message = None
        if ex.args and isinstance(ex.args[0], NoViableAltException):
            token = ex.args[0].offendingToken
            if token is not None and token.type != ContactQLParser.EOF:
                message = "Search query contains an error at: %s" % token.text

        if message is None:
            message = "Search query contains an error"

        raise SearchException(message)

    visitor = ContactQLVisitor(as_anon)

    query = ContactQuery(visitor.visit(tree))
    return query.optimized() if optimize else query


def evaluate_query(org, text, contact_json=dict):
    parsed = legacy_parse_query(text, optimize=True, as_anon=org.is_anon)

    return parsed.evaluate(org, contact_json)


def is_phonenumber(text):
    """
    Checks if query looks like a phone number, and if so returns a cleaned version of it
    """
    matches = TEL_VALUE_REGEX.match(text)
    if matches:
        return True, CLEAN_SPECIAL_CHARS_REGEX.sub("", text)
    else:
        return False, None
