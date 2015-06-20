describe 'Services:', ->
  # initialize our angular app
  beforeEach ->
    module 'app'

  # we want access to http and flow service
  $http = null
  flows = null
  beforeEach inject((_$httpBackend_) ->
    $http = _$httpBackend_

    # wire up our mock flows
    flows = {
      'favorites': { id: 1, languages:[] },
      'rules_first': { id: 2, languages:[] }
      'loop_detection': { id: 3, languages:[] }
    }

    $http.whenGET('/contactfield/json/').respond([])
    $http.whenGET('/label/').respond([])

    for file, config of flows

      $http.whenPOST('/flow/json/' + config.id + '/').respond()
      $http.whenGET('/flow/json/' + config.id + '/').respond(
        {
          flow: getJSONFixture(file + '.json'),
          languages: config.languages
        }
      )

      $http.whenGET('/flow/versions/' + config.id + '/').respond(
        [],  {'content-type':'application/json'}
      )

      $http.whenGET('/flow/completion/?flow=' + config.id).respond([])
  )

  describe 'Flow', ->

    $window = null
    $rootScope = null
    $compile = null
    flowService = null

    beforeEach inject((_$rootScope_, _$compile_, _$window_, _Flow_) ->
      $rootScope = _$rootScope_.$new()
      $compile = _$compile_
      $window = _$window_
      flowService = _Flow_
    )

    it 'should set flow defintion after fetching', ->
      flowService.fetch(flows.rules_first.id).then (response) ->
        expect(flowService.flow).not.toBe(null)
      , (error) ->
        throwError('Failed to fetch mock flow data:' + error)
      $http.flush()

    it 'should determine the flow entry', ->
      flowService.fetch(flows.favorites.id).then ->

        flow = flowService.flow

        # our entry should already be set from reading in the file
        expect(flow.entry).toBe('ec4c8328-f7b6-4386-90c0-b7e6a3517e9b')

        # now determine the start point
        flowService.determineFlowStart()

        # it shouldn't have changed from what we had
        expect(flow.entry).toBe('ec4c8328-f7b6-4386-90c0-b7e6a3517e9b')

        # now let's move our entry node down
        entry = getNode(flow, 'ec4c8328-f7b6-4386-90c0-b7e6a3517e9b')
        entry.y = 200
        flowService.determineFlowStart()

        # our 'other' action set is now the top
        expect(flow.entry).toBe('dcd9541a-0263-474e-b3f1-03a28993f95a')

      $http.flush()

    it 'should merge duplicate rules to the same destination', ->

      ruleset = {
        rules: [
          { test: {test: "A", type: "contains_any"}, category: "A", destination: "Action_A", uuid: "Rule_A" },
          { test: {test: "B", type: "contains_any"}, category: "B", destination: "Action_B", uuid: "Rule_B" },
          { test: {test: "C", type: "contains_any"}, category: "A", destination: null, uuid: "Rule_C" },
          { test: {test: "true", type: "true"}, category: "Other", destination: null, uuid: "Rule_Other" },
        ]
      }

      flowService.deriveCategories(ruleset)

      # we create a UI version of our rules
      expect(ruleset._categories).not.toBe(undefined)

      # we have four rules, but only three categories
      expect(ruleset.rules.length).toBe(4)
      expect(ruleset._categories.length).toBe(3)

      # pull out some rules and confirm them
      ruleA = ruleset.rules[0]
      ruleB = ruleset.rules[1]
      ruleC = ruleset.rules[2]
      ruleOther = ruleset.rules[3]
      expect(ruleA.uuid).toBe("Rule_A")
      expect(ruleB.uuid).toBe("Rule_B")
      expect(ruleC.uuid).toBe("Rule_C")
      expect(ruleOther.uuid).toBe("Rule_Other")

      # Rule_C should get the same destination as Rule_A
      expect(ruleC.destination).toBe(ruleA.destination)

      # try case insensitive munging
      ruleC.category = 'b'
      flowService.deriveCategories(ruleset)
      expect(ruleC.destination).toBe(ruleB.destination)

      # the other rule should never be munged
      ruleOther.category = 'b'
      flowService.deriveCategories(ruleset)
      expect(ruleOther.destination).toBe(null)

    it 'should merge duplicate rules to the same destination for localized flows', ->

      ruleset = {
        rules: [
          { test: {test: {eng:"A"}, type: "contains_any"}, category: {eng:"A"}, destination: "Action_A", uuid: "Rule_A" },
          { test: {test: {eng:"B"}, type: "contains_any"}, category: {eng:"B"}, destination: "Action_B", uuid: "Rule_B" },
          { test: {test: {eng:"C"}, type: "contains_any"}, category: {eng:"A"}, destination: null, uuid: "Rule_C" },
          { test: {test: "true", type: "true"}, category: {eng:"Other"}, destination: null, uuid: "Rule_Other" },
        ]
      }

      flowService.deriveCategories(ruleset, 'eng')

      # we create a UI version of our rules
      expect(ruleset._categories).not.toBe(undefined)

      # we have four rules, but only three categories
      expect(ruleset.rules.length).toBe(4)
      expect(ruleset._categories.length).toBe(3)

      # pull out some rules and confirm them
      ruleA = ruleset.rules[0]
      ruleB = ruleset.rules[1]
      ruleC = ruleset.rules[2]
      ruleOther = ruleset.rules[3]
      expect(ruleA.uuid).toBe("Rule_A")
      expect(ruleB.uuid).toBe("Rule_B")
      expect(ruleC.uuid).toBe("Rule_C")
      expect(ruleOther.uuid).toBe("Rule_Other")

      # Rule_C should get the same destination as Rule_A
      expect(ruleC.destination).toBe(ruleA.destination)

      # try case insensitive munging
      ruleC.category = {eng:'b'}
      flowService.deriveCategories(ruleset, 'eng')
      expect(ruleC.destination).toBe(ruleB.destination)

      # the other rule should never be munged
      ruleOther.category = {eng:'b'}
      flowService.deriveCategories(ruleset, 'eng')
      expect(ruleOther.destination).toBe(null)

    describe 'checkTerminal', ->

      actionset = null
      beforeEach ->
        actionset =
          "y": 0, "x": 0
          "destination": null
          "uuid": "dcd9541a-0263-474e-b3f1-03a28993f95a"
          "actions": [{ "msg": "I don't know that color. Try again.", "type": "reply"}]

      it 'should detect terminal actionsets', ->

        # we haven't determined our terminal status yet
        expect(actionset._terminal).toBe(undefined)

        # we aren't terminal
        flowService.checkTerminal(actionset)
        expect(actionset._terminal).toBe(false)

      # ivr doesn't require a message either
      it 'should make sure IVR flows require a message', ->
        window.ivr = true
        flowService.checkTerminal(actionset)
        expect(actionset._terminal).toBe(false)

    describe 'isConnectionAllowed()', ->

      flow = null
      beforeEach ->
        flowService.fetch(flows.loop_detection.id).then ->
          # derive all our categories
          flow = flowService.flow
          for ruleset in flow.rule_sets
            flowService.deriveCategories(ruleset, 'eng')
        $http.flush()

      messageOne = '13977cf2-68ee-49b9-8d88-2b9dbce12c5b'
      groupSplit = '9e348f0c-f7fa-4c06-a78b-9ffa839e5779'
      groupA = '605e4e98-5d85-45e7-a885-9c198977b63c'
      groupB = '81ba32a2-b3ea-4d46-aa7e-2ef32d7ced1e'

      nameSplit = '782e9e71-c116-4195-add3-1867132f95b6'
      rowan = 'f78edeea-4339-4f06-b95e-141975b97cb8'

      messageSplitA = '1f1adefb-0791-4e3c-9e8f-10dc6d56d3a5'
      messageSplitB = '771088fd-fc77-4966-8541-93c3c59c923d'
      messageSplitRule = '865baac0-da29-4752-be1e-1488457f708c'

      it 'should detect looping to same rule', ->
        ruleSelfLoop = flowService.isConnectionAllowed(groupSplit + '_' + groupA, groupSplit)
        expect(ruleSelfLoop).toBe(false, "Rule was able to point to it's parent")

      it 'should detect two passive rules in a row', ->
        ruleLoop = flowService.isConnectionAllowed(nameSplit + '_' + rowan, groupSplit)
        expect(ruleLoop).toBe(false, "Non blocking rule infinite loop")

      it 'should detect a passive rule to an action and back', ->
        ruleActionLoop = flowService.isConnectionAllowed(groupSplit, messageOne, groupA)
        expect(ruleActionLoop).toBe(false, "Rule to action loop without blocking ruleset")

      it 'should detect back to back pause rules', ->
        rulePauseLoop = flowService.isConnectionAllowed(messageSplitB, messageSplitA, messageSplitRule)
        expect(rulePauseLoop).toBe(false, "Two pausing rulesets in a row")

      it 'should allow top level connection with downstream splits to same node', ->
        flowService.updateDestination(messageOne, null)

        # set our group b to go to the same as other (name split)
        flowService.updateDestination(groupSplit + '_' + groupB, nameSplit)

        # now try reconnective our first message
        allowed = flowService.isConnectionAllowed(messageOne, groupSplit)
        expect(allowed).toBe(true, "Failed to allow legitimately branched connection")

    describe 'updateDestination()', ->
      colorActionsId = 'ec4c8328-f7b6-4386-90c0-b7e6a3517e9b'
      colorRulesId = '1a08ec37-2218-48fd-b6b0-846b14407041'
      redRuleId = 'e82dfba9-aaf3-438c-b52d-5dee50b1260c'
      greenRuleId = '6ac83530-aab5-423f-809e-56b6657dd543'
      beerActionsId = '2469ada5-3c36-4d74-bf73-daab0a56c37c'
      nameActionsId = 'e990c809-62f3-44e7-b50a-e127324a6cde'

      flow = null

      beforeEach ->
        flowService.fetch(flows.favorites.id).then ->
          # derive all our categories
          flow = flowService.flow
          for ruleset in flow.rule_sets
            flowService.deriveCategories(ruleset)
        $http.flush()


      it 'should handle null action destinations', ->

        # check our test rule is going to the right place first
        question = getNode(flow, colorActionsId)
        expect(question.destination).toBe(colorRulesId)

        # update to no destination
        flowService.updateDestination(colorActionsId, null)
        expect(question.destination).toBe(null)

      it 'should handle null ruleset destinations', ->

        # check our test rule is going to the right place first
        color = getRule(flow, colorRulesId, redRuleId)
        expect(color.destination).toBe(beerActionsId)

        # update to no destination
        flowService.updateDestination(colorRulesId + '_' + redRuleId, null)
        expect(color.destination).toBe(null)

      it 'should allow _source suffix', ->

        # actions with _source suffix
        question = getNode(flow, colorActionsId)
        flowService.updateDestination(colorActionsId + '_source', null)
        expect(question.destination).toBe(null)

        # rule with _source suffix
        color = getRule(flow, colorRulesId, redRuleId)
        flowService.updateDestination(colorRulesId + '_' + redRuleId + '_source', null)
        expect(color.destination).toBe(null)

      it 'should connect actions to a new rule', ->
        flowService.updateDestination(colorRulesId + '_' + greenRuleId, nameActionsId)
        green = getRule(flow, colorRulesId, greenRuleId)
        expect(green.destination).toBe(nameActionsId, 'couldnt update rule to valid action')

        # make sure our category updated too
        colors = getNode(flow, colorRulesId)
        expect(colors._categories[1].name).toBe('Green')
        expect(colors._categories[1].target).toBe(nameActionsId)

      it 'should update joined rules', ->

        # check we have the right number of categories to start
        colors = getNode(flow, colorRulesId)
        expect(colors._categories.length).toBe(4, 'categories were not derived properly')

        # now set the green category name to the same as red
        green = getRule(flow, colorRulesId, greenRuleId)
        green.category = 'red'
        flowService.deriveCategories(colors)
        expect(colors._categories.length).toBe(3, 'like named category did not get merged')

        # change red to skip a question
        flowService.updateDestination(colorRulesId + '_' + redRuleId, nameActionsId)

        # green should have moved there too automatically
        red = getRule(flow, colorRulesId, redRuleId)
        green = getRule(flow, colorRulesId, greenRuleId)
        expect(green.destination).toBe(nameActionsId, 'green rule didnt update')
        expect(red.destination).toBe(nameActionsId, 'red rule didnt update')
