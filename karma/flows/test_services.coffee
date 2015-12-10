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

      $http.whenGET('/flow/json/' + config.id + '/').respond(
        {
          flow: getJSONFixture(file + '.json').flows[0],
          languages: config.languages
        }
      )

      $http.whenGET('/flow/revisions/' + config.id + '/').respond(
        [],  {'content-type':'application/json'}
      )

      $http.whenGET('/flow/completion/?flow=' + config.id).respond(
        {message_completions: [{name:'contact.name', display:'Contact Name'}], function_completions: [{name:'SUM', display:'Returns the sum of all arguments'}]}
      ,  {'content-type':'application/json'}
      )
  )

  describe 'Flow', ->

    $window = null
    $rootScope = null
    $compile = null
    flowService = null
    $timeout = null

    beforeEach inject((_$rootScope_, _$compile_, _$window_, _Flow_, _$timeout_) ->
      $rootScope = _$rootScope_.$new()
      $compile = _$compile_
      $window = _$window_
      flowService = _Flow_
      $timeout = _$timeout_
    )

    it 'should slugify properly', ->
      expect(flowService.slugify('This Response Slugged')).toBe('this_response_slugged')
      expect(flowService.slugify('This RESPONSE$@slugGed')).toBe('this_response_slugged')
      expect(flowService.slugify('this    response %slugged')).toBe('this_response_slugged')

    it 'should set flow definition after fetching', ->
      flowService.fetch(flows.rules_first.id).then (response) ->
        expect(flowService.flow).not.toBe(null)
      , (error) ->
        throwError('Failed to fetch mock flow data:' + error)
      $http.flush()

      expect(flowService.completions).toEqual([{name:'contact.name', display:'Contact Name'}])
      expect(flowService.function_completions).toEqual([{name:'SUM', display:'Returns the sum of all arguments'}])
      expect(flowService.variables_and_functions).toEqual([flowService.completions..., flowService.function_completions...])

    it 'should determine the flow entry', ->
      flowService.fetch(flows.favorites.id).then ->

        flow = flowService.flow

        # our entry should already be set from reading in the file
        expect(flow.entry).toBe('127f3736-77ce-4006-9ab0-0c07cea88956')

        # now determine the start point
        flowService.determineFlowStart()

        # it shouldn't have changed from what we had
        expect(flow.entry).toBe('127f3736-77ce-4006-9ab0-0c07cea88956')

        # now let's move our entry node down
        entry = getNode(flow, '127f3736-77ce-4006-9ab0-0c07cea88956')
        entry.y = 200
        flowService.determineFlowStart()

        # our 'other' action set is now the top
        expect(flow.entry).toBe('f9adf38f-ab18-49d3-a8ac-db2fe8f1e77f')

      $http.flush()

    it 'should merge duplicate rules to the same destination', ->

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

    describe 'makeDirty()', ->

      it 'should handle saving with intermittent connections', ->

        # execute our fetch
        flowService.fetch(flows.favorites.id)
        $http.flush()
        $timeout.flush()

        # we should start at the starting delay
        expect($rootScope.errorDelay).toBe(500)

        # simulate our server going offline
        post = $http.whenPOST('/flow/json/' + flows.favorites.id + '/').respond(0, '')

        # mark as dirty
        flowService.dirty = true
        $rootScope.$apply()
        $timeout.flush()
        $http.flush()

        # we should now have a step down in effect now
        expect($rootScope.errorDelay).toBe(1000)

        # now our server comes back online
        post.respond(200, '')

        # run our delay which marks us dirty
        $timeout.flush()

        # apply our new dirty state
        $rootScope.$apply()
        $timeout.flush()
        $http.flush()

        # now we are back to a standard error delay after success
        expect($rootScope.errorDelay).toBe(500)

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

      endOfFlow = '3a0f77d1-f6bf-47f1-b194-de2051ba0738'

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

        # now try reconnecting our first message
        allowed = flowService.isConnectionAllowed(messageOne, groupSplit)
        expect(allowed).toBe(true, "Failed to allow legitimately branched connection")

      it 'should detect arbitrary expression pause', ->
        for ruleset in flow.rule_sets
          if ruleset.uuid == messageSplitA
            ruleset.operand = '=(step.value= contact.last_four_digit)'
            ruleset.ruleset_type = 'wait_message'

        allowed = flowService.isConnectionAllowed(endOfFlow, messageSplitA)
        expect(allowed).toBe(true, 'Failed to find expression step value')

    describe 'getFieldSelection()', ->

      flow = null
      flowFields = null
      contactFields = null

      beforeEach ->
        flowService.fetch(flows.favorites.id).then ->
          flow = flowService.flow
        $http.flush()

        flowFields = [
          {id:'response_1', name:'Response 1'},
          {id:'response_2', name:'Response 2'},
          {id:'response_3', name:'Response 3'}
        ]

        contactFields = [
          {id:'name', name:'Contact Name'},
          {id:'email', name:'Contact Email'},
          {id:'district', name:'Contact District'}
        ]

      it 'should identify flow fields', ->
        previousFieldCount = flowFields.length
        selection = flowService.getFieldSelection(flowFields, '@flow.response_1', true)
        expect(selection.id).toBe('response_1')
        expect(flowFields.length).toBe(previousFieldCount)

      it 'should add a missing element if appropriate', ->
        previousFieldCount = flowFields.length
        selection = flowService.getFieldSelection(flowFields, '@flow.favorite_color', true)
        expect(selection.id).toBe('favorite_color')
        expect(selection.text).toBe('favorite_color (missing)')
        expect(flowFields.length).toBe(previousFieldCount + 1)

      it 'should handle contact field lookups', ->
        previousFieldCount = contactFields.length
        selection = flowService.getFieldSelection(contactFields, '@contact.name', false)
        expect(selection.id).toBe('name')
        expect(contactFields.length).toBe(previousFieldCount)

      it 'should handle missing contact field lookups', ->
        previousFieldCount = contactFields.length
        selection = flowService.getFieldSelection(contactFields, '@contact.favorite_donut', false)
        expect(selection.id).toBe('favorite_donut')
        expect(selection.text).toBe('favorite_donut (missing)')
        expect(contactFields.length).toBe(previousFieldCount+1)

      it 'should return first contact field if not found', ->
        previousFieldCount = contactFields.length
        selection = flowService.getFieldSelection(contactFields, '@flow.response_1', false)
        expect(selection).toBe(contactFields[0])
        expect(flowFields.length).toBe(previousFieldCount)

      it 'should return first flow field if not found', ->
        previousFieldCount = flowFields.length
        selection = flowService.getFieldSelection(flowFields, '@contact.favorite_donut', true)
        expect(selection).toBe(flowFields[0])
        expect(flowFields.length).toBe(previousFieldCount)

    describe 'updateDestination()', ->
      colorActionsId = '127f3736-77ce-4006-9ab0-0c07cea88956'
      colorRulesId = '2bff5c33-9d29-4cfc-8bb7-0a1b9f97d830'
      redRuleId = '8cd25a3f-0be2-494b-8b4c-3a4f0de7f9b2'
      greenRuleId = 'db2863cf-7fda-4489-9345-d44dacf4e750'
      beerActionsId = '44471ade-7979-4c94-8028-6cfb68836337'
      nameActionsId = '89c5624e-3320-4668-a066-308865133080'

      flow = null

      beforeEach ->
        flowService.fetch(flows.favorites.id).then ->
          # derive all our categories
          flow = flowService.flow
          for ruleset in flow.rule_sets
            flowService.deriveCategories(ruleset, 'base')
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
        expect(colors._categories[1].name['base']).toBe('Green')
        expect(colors._categories[1].target).toBe(nameActionsId)

      it 'should update joined rules', ->

        # check we have the right number of categories to start
        colors = getNode(flow, colorRulesId)
        expect(colors._categories.length).toBe(4, 'categories were not derived properly')

        # now set the green category name to the same as red
        green = getRule(flow, colorRulesId, greenRuleId)
        green.category = {base: 'red'}
        flowService.deriveCategories(colors, 'base')
        expect(colors._categories.length).toBe(3, 'like named category did not get merged')

        # change red to skip a question
        flowService.updateDestination(colorRulesId + '_' + redRuleId, nameActionsId)

        # green should have moved there too automatically
        red = getRule(flow, colorRulesId, redRuleId)
        green = getRule(flow, colorRulesId, greenRuleId)
        expect(green.destination).toBe(nameActionsId, 'green rule didnt update')
        expect(red.destination).toBe(nameActionsId, 'red rule didnt update')
