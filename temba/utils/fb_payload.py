from __future__ import absolute_import, unicode_literals

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
CONTAINS_ANY = 'contains_any'
TYPE = 'type'
EQ = 'eq'


def get_fb_payload(msg, text):
    from temba.msgs.models import Msg
    from temba.flows.models import RuleSet

    payload = dict(text=text)

    real_msg = Msg.all_messages.filter(id=msg.id).first()
    if real_msg:
        step = real_msg.get_flow_step()
        destination = step.get_step().destination

        try:
            rule = RuleSet.objects.filter(uuid=destination).first()
            lang = rule.flow.base_language
            rules = rule.as_json()
        except:
            lang = BASE
            rules = None

        if rules:
            model = get_model(rules.get(RULES))
            if model and rules.get(RULESET_TYPE) == WAIT_MESSAGE:

                buttons = []
                for rule in rules.get(RULES):
                    category, value = get_value_payload(rule, lang)

                    if category and value:
                        if model == BUTTON:
                            buttons.append(dict(type=POSTBACK, title=category, payload=value))
                        else:
                            buttons.append(dict(content_type=TEXT, title=category, payload=value))

                if buttons:
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

    if 0 < len(rules) <= 4:
        response = BUTTON
    elif 4 < len(rules) <= 10:
        response = QUICK_REPLIES
    else:
        return None

    return response


def get_value_payload(rule, lang):
    category = rule.get(CATEGORY)
    test = rule.get(TEST).get(TEST)
    value = None

    PERM = [EQ, CONTAINS_ANY]

    if test == STR_TRUE or rule.get(TEST).get(TYPE) not in PERM:
        pass
    elif category.get(lang) != OTHER.capitalize():
        try:
            base = test.get(BASE)
        except:
            base = test

        print(base)
        value = base.split(' ')[0]

    print("--- %s" % category.get(lang))

    return category.get(lang), value
