from datetime import timedelta

import regex
from temba_expressions.evaluator import (
    DEFAULT_FUNCTION_MANAGER,
    DateStyle,
    EvaluationContext,
    EvaluationStrategy,
    Evaluator,
)

from django.utils import timezone

from temba.contacts.models import TEL_SCHEME, TWITTER_SCHEME, TWITTERID_SCHEME, Contact, ContactField, ContactURN
from temba.utils.dates import datetime_to_str

ALLOWED_TOP_LEVELS = ("channel", "contact", "date", "extra", "flow", "step", "parent", "child")

evaluator = Evaluator(allowed_top_levels=ALLOWED_TOP_LEVELS)

listing = None  # lazily initialized


def evaluate(text, context, org=None, url_encode=False, partial_vars=False):
    """
    Given input ```text```, tries to find variables in the format @foo.bar and replace them according to
    the passed in context, contact and org. If some variables are not resolved to values, then the variable
    name will remain (ie, @foo.bar).

    Returns a tuple of the substituted text and whether there were are substitution failures.
    """
    # shortcut for cases where there is no way we would substitute anything as there are no variables
    if not text or text.find("@") < 0:
        return text, []

    # add 'step.contact' if it isn't populated for backwards compatibility
    if "step" not in context:
        context["step"] = dict()
    if "contact" not in context["step"]:
        context["step"]["contact"] = context.get("contact")

    if not org:
        dayfirst = True
        tz = timezone.get_current_timezone()
    else:
        dayfirst = org.get_dayfirst()
        tz = org.timezone

    (format_date, format_time) = org.get_datetime_formats()

    now = timezone.now().astimezone(tz)

    # add date.* constants to context
    context["date"] = {
        "__default__": now.isoformat(),
        "now": now.isoformat(),
        "today": datetime_to_str(timezone.now(), format=format_date, tz=tz),
        "tomorrow": datetime_to_str(timezone.now() + timedelta(days=1), format=format_date, tz=tz),
        "yesterday": datetime_to_str(timezone.now() - timedelta(days=1), format=format_date, tz=tz),
    }

    date_style = DateStyle.DAY_FIRST if dayfirst else DateStyle.MONTH_FIRST
    context = EvaluationContext(context, tz, date_style)

    # returns tuple of output and errors
    return evaluate_template(text, context, url_encode, partial_vars)


def evaluate_template(template, context, url_encode=False, partial_vars=False):
    strategy = EvaluationStrategy.RESOLVE_AVAILABLE if partial_vars else EvaluationStrategy.COMPLETE
    return evaluator.evaluate_template(template, context, url_encode, strategy)


def get_function_listing():
    global listing

    if listing is None:
        listing = [
            {"name": f["name"], "display": f["description"], "signature": _build_function_signature(f)}
            for f in DEFAULT_FUNCTION_MANAGER.build_listing()
        ]
    return listing


def _build_function_signature(f):
    signature = f["name"] + "("
    params_len = len(f["params"])

    formatted_params_list = []
    for param in f["params"]:
        formatted_param = param["name"]
        optional = param["optional"]
        vararg = param["vararg"]

        if optional and vararg:
            formatted_param = "[" + formatted_param + "], ..."

        elif optional:
            formatted_param = "[" + formatted_param + "]"

        elif vararg:
            formatted_param += ", ..."

        if len(formatted_params_list) < params_len - 1:
            formatted_param += ","
        formatted_params_list.append(formatted_param)

    return signature + " ".join(formatted_params_list) + ")"


def channel_context(channel):
    address = channel.get_address_display()
    default = address if address else str(channel)

    # for backwards compatibility
    if TEL_SCHEME in channel.schemes:
        tel = address
        tel_e164 = channel.get_address_display(e164=True)
    else:
        tel = ""
        tel_e164 = ""

    return dict(__default__=default, name=channel.get_name(), address=address, tel=tel, tel_e164=tel_e164)


def contact_context(contact):
    """
    Builds a dictionary suitable for use in variable substitution in messages.
    """
    contact.initialize_cache()

    org = contact.org
    context = {
        "__default__": contact.get_display(for_expressions=True),
        Contact.NAME: contact.name or "",
        Contact.FIRST_NAME: contact.first_name(org),
        Contact.LANGUAGE: contact.language,
        "tel_e164": contact.get_urn_display(scheme=TEL_SCHEME, org=org, formatted=False),
        "groups": ",".join([_.name for _ in contact.cached_user_groups]),
        "uuid": contact.uuid,
        "created_on": contact.created_on.isoformat(),
    }

    # anonymous orgs also get @contact.id
    if org.is_anon:
        context["id"] = contact.id

    def get_urn_context(scheme=None):
        urn = contact.get_urn(scheme)
        if not urn:
            return ""

        return urn_context(urn, org)

    # add all URNs
    for scheme, label in ContactURN.SCHEME_CHOICES:
        context[scheme] = get_urn_context(scheme=scheme)

    # populate twitter address if we have a twitter id
    if context[TWITTERID_SCHEME] and not context[TWITTER_SCHEME]:
        context[TWITTER_SCHEME] = context[TWITTERID_SCHEME]

    # add all active fields to our context
    for field in ContactField.user_fields.active_for_org(org=contact.org):
        field_value = contact.get_field_serialized(field)
        context[field.key] = field_value if field_value is not None else ""

    return context


def urn_context(urn, org):
    if org.is_anon:
        return {
            "__default__": ContactURN.ANON_MASK,
            "scheme": urn.scheme,
            "path": ContactURN.ANON_MASK,
            "display": ContactURN.ANON_MASK,
            "urn": ContactURN.ANON_MASK,
        }
    display = urn.get_display(org=org, formatted=True, international=False)

    return {"__default__": display, "scheme": urn.scheme, "path": urn.path, "display": display, "urn": urn.urn}


def msg_context(msg):
    date_format = msg.org.get_datetime_formats()[1]
    value = str(msg)
    attachments = {str(a): attachment.url for a, attachment in enumerate(msg.get_attachments())}

    context = {
        "__default__": value,
        "value": value,
        "text": msg.text,
        "attachments": attachments,
        "time": datetime_to_str(msg.created_on, format=date_format, tz=msg.org.timezone),
    }

    if msg.contact_urn:
        context["urn"] = urn_context(msg.contact_urn, msg.org)

    return context


def run_context(run, contact_ctx=None, raw_input=None):
    from temba.flows.models import FlowRun

    def result_wrapper(res):
        """
        Wraps a result, lets us do a nice representation of both @flow.foo and @flow.foo.text
        """
        return {
            "__default__": res[FlowRun.RESULT_VALUE],
            "text": res.get(FlowRun.RESULT_INPUT),
            "time": res[FlowRun.RESULT_CREATED_ON],
            "category": res.get(FlowRun.RESULT_CATEGORY_LOCALIZED, res[FlowRun.RESULT_CATEGORY]),
            "value": res[FlowRun.RESULT_VALUE],
        }

    context = {}
    default_lines = []

    for key, result in run.results.items():
        context[key] = result_wrapper(result)
        default_lines.append("%s: %s" % (result[FlowRun.RESULT_NAME], result[FlowRun.RESULT_VALUE]))

    context["__default__"] = "\n".join(default_lines)

    # if we don't have a contact context, build one
    if not contact_ctx:  # pragma: no cover
        run.contact.org = run.org
        contact_ctx = contact_context(run.contact)

    context["contact"] = contact_ctx

    return context


def flow_context(flow, contact, msg, run=None):
    from temba.msgs.models import Msg

    contact_ctx = contact_context(contact) if contact else {}

    # our default value
    channel_ctx = None

    # add our message context
    if msg:
        message_ctx = msg_context(msg)

        if msg.channel:
            channel_ctx = channel_context(msg.channel)
    else:
        message_ctx = dict(__default__="")

    # If we still don't know our channel and have a contact, derive the right channel to use
    if not channel_ctx and contact:
        _contact, contact_urn = Msg.resolve_recipient(flow.org, flow.created_by, contact, None)

        # only populate channel if this contact can actually be reached (ie, has a URN)
        if contact_urn:
            channel = contact.cached_send_channel(contact_urn=contact_urn)
            if channel:
                channel_ctx = channel_context(channel)

    if not run:
        run = flow.runs.filter(contact=contact).order_by("-created_on").first()

    if run:
        run.org = flow.org
        run.contact = contact

        run_ctx = run.fields
        flow_ctx = run_context(run, contact_ctx, message_ctx.get("text"))
    else:  # pragma: no cover
        run_ctx = {}
        flow_ctx = {}

    context = dict(flow=flow_ctx, channel=channel_ctx, step=message_ctx, extra=run_ctx)

    # if we have parent or child contexts, add them in too
    if run:
        run.contact = contact

        if run.parent_context is not None:
            context["parent"] = run.parent_context.copy()
            parent_contact_uuid = context["parent"]["contact"]

            if parent_contact_uuid != str(contact.uuid):
                parent_contact = Contact.objects.filter(org=run.org, uuid=parent_contact_uuid, is_active=True).first()
                if parent_contact:
                    context["parent"]["contact"] = contact_context(parent_contact)
                else:
                    # contact may have since been deleted
                    context["parent"]["contact"] = {"uuid": parent_contact_uuid}  # pragma: no cover
            else:
                context["parent"]["contact"] = contact_context

        # see if we spawned any children and add them too
        if run.child_context is not None:
            context["child"] = run.child_context.copy()
            context["child"]["contact"] = contact_ctx

    if contact:
        context["contact"] = contact_ctx

    return context


# ======================================================================================================================
# Pre v8 style expression migration
# ======================================================================================================================

FILTER_REPLACEMENTS = {
    "lower_case": "LOWER({0})",
    "upper_case": "UPPER({0})",
    "capitalize": "PROPER({0})",
    "title_case": "PROPER({0})",
    "first_word": "FIRST_WORD({0})",
    "remove_first_word": "REMOVE_FIRST_WORD({0})",
    "read_digits": "READ_DIGITS({0})",
    "time_delta": "{0} + {1}",
}


def migrate_v7_template(text):
    """
    Migrates text which may contain filter style expressions or equals style expressions
    """
    migrated = text

    if "=" in migrated:
        migrated = _replace_equals_style(migrated)
    if "@" in migrated and "|" in migrated:
        migrated = _replace_filter_style(migrated)

    return migrated


def _replace_filter_style(text):
    """
    Migrates text which may contain filter style expressions, e.g. "Hi @contact.name|upper_case", converting them to
    new style expressions, e.g. "Hi @(UPPER(contact))"
    """

    def replace_expression(match):
        expression = match.group(1)
        new_style = _convert_filter_style(expression)
        if "|" in expression:
            new_style = "(%s)" % new_style  # add enclosing parentheses
        return "@" + new_style

    context_keys_joined_pattern = r"[\|\w]*|".join(ALLOWED_TOP_LEVELS)
    pattern = r"\B@([\w]+[\.][\w\.\|]*[\w](:([\"\']).*?\3)?|" + context_keys_joined_pattern + r"[\|\w]*)"

    rexp = regex.compile(pattern, flags=regex.MULTILINE | regex.UNICODE | regex.V0)
    return rexp.sub(replace_expression, text)


def _convert_filter_style(expression):
    """
    Converts a filter style expression, e.g. contact.name|upper_case, to new style, e.g. UPPER(contact)
    """
    if "|" not in expression:
        return expression

    components = expression.split("|")
    context_item = components[0]
    filters = components[1:]

    new_style = context_item
    for _filter in filters:
        if ":" in _filter:
            name, param = _filter.split(":")
            if param[0] == '"' or param[0] == "'":
                param = param[1:-1]  # strip quotes
        else:
            name, param = _filter, ""

        replacement = FILTER_REPLACEMENTS.get(name.lower(), None)
        if replacement:
            new_style = replacement.replace("{0}", new_style).replace("{1}", param)

    new_style = new_style.replace("+ -", "- ")  # collapse "+ -N" to "- N"

    return new_style


def _replace_equals_style(text):
    """
    Migrates text which may contain equals style expressions, e.g. "Hi =UPPER(contact)", converting them to new style
    expressions, e.g. "Hi @(UPPER(contact))"
    """
    STATE_BODY = 0  # not in a expression
    STATE_PREFIX = 1  # '=' prefix that denotes the start of an expression
    STATE_IDENTIFIER = 2  # the identifier part, e.g. 'SUM' in '=SUM(1, 2)' or 'contact.age' in '=contact.age'
    STATE_BALANCED = 3  # the balanced parentheses delimited part, e.g. '(1, 2)' in 'SUM(1, 2)'
    STATE_STRING_LITERAL = 4  # a string literal
    input_chars = list(text)
    output_chars = []
    state = STATE_BODY
    current_expression_chars = []
    current_expression_terminated = False
    parentheses_level = 0

    def replace_expression(expression):
        expression_body = expression[1:]

        # if expression doesn't end with ) then check it's an allowed top level context reference
        if not expression_body.endswith(")"):
            top_level = expression_body.split(".")[0].lower()
            if top_level not in ALLOWED_TOP_LEVELS:
                return expression

        return "@" + _convert_equals_style(expression_body)

    # determines whether the given character is a word character, i.e. \w in a regex
    def is_word_char(c):
        return c and (c.isalnum() or c == "_")

    for pos, ch in enumerate(input_chars):
        # in order to determine if the b in a.b terminates an identifier, we have to peek two characters ahead as it
        # could be a.b. (b terminates) or a.b.c (b doesn't terminate)
        next_ch = input_chars[pos + 1] if (pos < (len(input_chars) - 1)) else None
        next_next_ch = input_chars[pos + 2] if (pos < (len(input_chars) - 2)) else None

        if state == STATE_BODY:
            if ch == "=" and (is_word_char(next_ch) or next_ch == "("):
                state = STATE_PREFIX
                current_expression_chars = [ch]
            else:
                output_chars.append(ch)

        elif state == STATE_PREFIX:
            if is_word_char(ch):
                # we're parsing an expression like =XXX or =YYY()
                state = STATE_IDENTIFIER
            elif ch == "(":
                # we're parsing an expression like =(1 + 2)
                state = STATE_BALANCED
                parentheses_level += 1

            current_expression_chars.append(ch)

        elif state == STATE_IDENTIFIER:
            if ch == "(":
                state = STATE_BALANCED
                parentheses_level += 1

            current_expression_chars.append(ch)

        elif state == STATE_BALANCED:
            if ch == "(":
                parentheses_level += 1
            elif ch == ")":
                parentheses_level -= 1
            elif ch == '"':
                state = STATE_STRING_LITERAL

            current_expression_chars.append(ch)

            # expression terminates if parentheses balance
            if parentheses_level == 0:
                current_expression_terminated = True

        elif state == STATE_STRING_LITERAL:
            if ch == '"':
                state = STATE_BALANCED
            current_expression_chars.append(ch)

        # identifier can terminate expression in 3 ways:
        #  1. next char is null (i.e. end of the input)
        #  2. next char is not a word character or period or left parentheses
        #  3. next char is a period, but it's not followed by a word character
        if state == STATE_IDENTIFIER:
            if (
                not next_ch
                or (not is_word_char(next_ch) and not next_ch == "." and not next_ch == "(")
                or (next_ch == "." and not is_word_char(next_next_ch))
            ):
                current_expression_terminated = True

        if current_expression_terminated:
            output_chars.append(replace_expression("".join(current_expression_chars)))
            current_expression_chars = []
            current_expression_terminated = False
            state = STATE_BODY

    return "".join(output_chars)


def _convert_equals_style(expression):
    """
    Converts a equals style expression, e.g. UPPER(contact), to new style, e.g. (UPPER(contact))
    """
    if "(" not in expression:  # e.g. contact or contact.name
        return expression

    # some users have been putting @ expressions inside = expressions which works due to the old two pass nature of
    # expression evaluation
    def replace_embedded_filter_style(match):
        filter_style = match.group(2)
        return _convert_filter_style(filter_style)

    pattern = r'(")?@((%s)[\.\w\|]*)(\1)?' % "|".join(ALLOWED_TOP_LEVELS)

    rexp = regex.compile(pattern, flags=regex.MULTILINE | regex.UNICODE | regex.V0)
    expression = rexp.sub(replace_embedded_filter_style, expression)

    if not expression.startswith("("):
        expression = "(%s)" % expression

    return expression
