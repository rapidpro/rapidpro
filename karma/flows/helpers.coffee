gettext = (text) ->
  return text

getNode = (flow, uuid) ->
  for actionset in flow.action_sets
    if actionset.uuid == uuid
      return actionset

  for ruleset in flow.rule_sets
    if ruleset.uuid == uuid
      return ruleset

# bootstrap our json fixtures
jasmine.getJSONFixtures().fixturesPath='base/media/test_flows';