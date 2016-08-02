from __future__ import absolute_import, unicode_literals
from temba.utils import str_to_bool

OTHER = 'other'
BUTTON = 'button'
QUICK_REPLIES = 'quick_replies'
WAIT_MESSAGE = 'wait_message'
RULES = 'rules'
CATEGORY = 'category'
BASE = 'base'
RULESET_TYPE = 'ruleset_type'
TEXT = 'text'
POSTBACK = 'postback'
TEMPLATE = 'template'
TEST = 'test'
STR_TRUE = 'true'


def get_fb_payload(msg, text):
    from temba.msgs.models import Msg
    from temba.flows.models import RuleSet

    payload = dict(text=text)

    real_msg = Msg.all_messages.filter(id=msg.id).first()
    if real_msg:
        step = real_msg.get_flow_step()
        destination = step.get_step().destination

        try:
            rules = RuleSet.objects.filter(uuid=destination).first().as_json()
        except:
            rules = None

        model = get_model(rules.get(RULES))
        if model and rules.get(RULESET_TYPE) == WAIT_MESSAGE:

            buttons = []
            for rule in rules.get(RULES):
                category, value = get_value_payload(rule)

                if category and value:
                    if model == BUTTON:
                        buttons.append(dict(type=POSTBACK, title=category, payload=value))
                    else:
                        buttons.append(dict(content_type=TEXT, title=category, payload=value))

            if model == BUTTON:
                obj_payload = dict(template_type=BUTTON, text=text, buttons=buttons)
                attachment = dict(type=TEMPLATE, payload=obj_payload)
                payload = dict(attachment=attachment)
            else:
                payload = dict(text=text, quick_replies=buttons)

    return payload


def get_model(rules):

    if len(rules) == 1:
        if rules[0].get(TEST).get(TEST) == STR_TRUE:
            return None

    if 0 < len(rules) <= 3:
        response = BUTTON
    elif 3 < len(rules) <= 10:
        response = QUICK_REPLIES
    else:
        return None

    return response


def get_value_payload(rule):
    category = rule.get(CATEGORY)
    test = rule.get(TEST).get(TEST)
    value = None

    if test == STR_TRUE:
        pass
    elif category.get(BASE) != OTHER.capitalize():
        base = test.get(BASE)
        value = base.split(' ')[0]

    return category.get(BASE), value
