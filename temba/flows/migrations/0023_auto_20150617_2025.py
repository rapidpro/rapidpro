# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from temba.flows.models import RuleSet

class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0022_ruleset_ruleset_type'),
    ]

    def populate_ruleset_type(apps, schema_editor):

        def requires_step(operand):

            if not operand:
                operand = '@step.value'

            # remove any padding
            if operand:
                operand = operand.strip()

            # if we start with =( then we are an expression
            is_expression = operand and len(operand) > 2 and operand[0:2] == '=('
            if '@step' in operand or (is_expression and 'step' in operand):
                return True
            return False

        RuleSet = apps.get_model("flows", "RuleSet")

        #RuleSet.objects.filter(response_type='M').update(ruleset_type='wait_digit')
        #RuleSet.objects.filter(response_type='K').update(ruleset_type='wait_digits')
        #RuleSet.objects.filter(response_type='R').update(ruleset_type='recording')

        for ruleset in RuleSet.objects.all():

            operand = ruleset.operand
            if not operand:
                operand = ''
            operand = operand.strip()

            # all previous ruleset that require step should be wait_message
            if requires_step(ruleset.operand):

                # if we have an empty operand, go ahead and update it
                if not ruleset.operand:
                    ruleset.operand = '@step.value'

                if ruleset.response_type == 'K':
                    ruleset.ruleset_type = RULESET_WAIT_DIGITS
                elif ruleset.response_type == 'M':
                    ruleset.ruleset_type = RULESET_WAIT_DIGIT
                elif ruleset.response_type == 'R':
                    ruleset.ruleset_type = RULESET_WAIT_RECORDING
                else:
                    ruleset.result_type = 'wait_message'

                ruleset.save()

            else:
                # if there's no reference to step, figure out our type
                ruleset.ruleset_type = 'expression'
                # special case contact and flow fields
                if ' ' not in operand and '|' not in operand:

                    # special case the contact.groups so they aren't contact_field
                    if operand == '@contact.groups':
                        ruleset.ruleset_type = 'expression'
                    elif operand.find('@contact.') == 0:
                        ruleset.ruleset_type = 'contact_field'
                    elif operand.find('@flow.') == 0:
                        ruleset.ruleset_type = 'flow_field'

                ruleset.save()

    operations = [
        migrations.RunPython(populate_ruleset_type),
        migrations.RemoveField(
            model_name='ruleset',
            name='response_type',
        ),
    ]
