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
    }

    $http.whenGET('/contactfield/json/').respond([])
    $http.whenGET('/label/').respond([])

    for file, config of flows

      $http.whenPOST('/flow/json/' + config.id + '/').respond(
      )

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

  describe 'Flow service', ->

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
      $window.flowId = flows.rules_first.id

      flowService.fetch().then (response) ->
        expect($rootScope.flow).not.toBe(null)
      , (error) ->
        throwError('Failed to fetch mock flow data:' + error)
      $http.flush()

    it 'should determine the flow entry', ->
      $window.flowId = flows.favorites.id
      flowService.fetch().then ->

        # our entry should already be set from reading in the file
        expect($rootScope.flow.entry).toBe('ec4c8328-f7b6-4386-90c0-b7e6a3517e9b')

        # now determine the start point
        flowService.determineFlowStart($rootScope.flow)

        # it shouldn't have changed from what we had
        expect($rootScope.flow.entry).toBe('ec4c8328-f7b6-4386-90c0-b7e6a3517e9b')

        # now let's move our entry node down
        entry = getNode($rootScope.flow, 'ec4c8328-f7b6-4386-90c0-b7e6a3517e9b')
        entry.y = 200
        flowService.determineFlowStart($rootScope.flow)

        # our 'other' action set is now the top
        expect($rootScope.flow.entry).toBe('dcd9541a-0263-474e-b3f1-03a28993f95a')

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
