describe 'Controllers:', ->

  beforeEach ->
    # initialize our angular app
    module 'app'
    module 'partials'
    window.testing = true

  $rootScope = null
  $compile = null
  $scope = null
  $modal = null
  $log = null
  window.mutable = true

  $http = null
  flows = null

  beforeEach inject((_$httpBackend_) ->

    $http = _$httpBackend_

    # wire up our mock flows
    flows = {
      'favorites': { id: 1, languages:[], channel_countries: [] },
      'rules_first': { id: 2, languages:[], channel_countries: [] },
      'loop_detection': { id: 3, languages:[], channel_countries: [] },
      'webhook_rule_first': { id: 4, languages:[], channel_countries: [] },
      'ussd_example': { id: 5, languages:[], channel_countries: [] },
    }

    $http.whenGET('/contactfield/json/').respond([])
    $http.whenGET('/label/').respond([])

    for file, config of flows

      $http.whenPOST('/flow/json/' + config.id + '/').respond()
      $http.whenGET('/flow/json/' + config.id + '/').respond(
        {
          flow: getJSONFixture(file + '.json').flows[0],
          languages: config.languages,
          channel_countries: config.channel_countries
        }
      )

      $http.whenGET('/flow/revisions/' + config.id + '/').respond(
        [],  {'content-type':'application/json'}
      )

      $http.whenGET('/flow/completion/?flow=' + config.id).respond([])
  )

  $modalStack = null
  $timeout = null

  beforeEach inject((_$rootScope_, _$compile_, _$log_, _$modal_, _$modalStack_, _$timeout_) ->
      $rootScope = _$rootScope_.$new()
      $scope = $rootScope.$new()
      $modal = _$modal_
      $modalStack = _$modalStack_
      $timeout = _$timeout_

      $rootScope.ghost =
        hide: ->

      $scope.$parent = $rootScope
      $compile = _$compile_
      $log = _$log_
    )

  # TODO: FlowController does more than it should. It should not have knowledge of
  # of jsplumb connection objects and should lean more on services.
  describe 'FlowController', ->

    flowController = null
    flowService = null
    utils = null

    beforeEach inject(($controller, _Flow_, _utils_) ->

      flowService = _Flow_
      flowController = $controller 'FlowController',
        $scope: $scope
        $rootScope: $rootScope
        $log: $log
        Flow: flowService
      utils = _utils_
    )

    getRuleConfig = (type) ->
      for ruleset in flowService.rulesets
        if ruleset.type == type
          return ruleset

    loadFavoritesFlow = ->
      flowService.fetch(flows.favorites.id)
      flowService.contactFieldSearch = []
      $http.flush()

    getAction = (type) ->
      for action in flowService.actions
        if action.type == type
          return action

    editRules = (ruleset, edits) ->
      $scope.clickRuleset(ruleset)
      $scope.dialog.opened.then ->
        modalScope = $modalStack.getTop().value.modalScope
        edits(modalScope)
        modalScope.formData.rulesetConfig = getRuleConfig(modalScope.ruleset.ruleset_type)

        if not modalScope.splitEditor
          modalScope.splitEditor = {}
        modalScope.okRules(modalScope.splitEditor)
      $timeout.flush()

    editAction = (actionset, action, edits) ->
      # open our editor modal so we can save it
      $scope.clickAction(actionset, action)
      $scope.dialog.opened.then ->
        modalScope = $modalStack.getTop().value.modalScope
        edits(modalScope)
      $timeout.flush()

    editRuleset = (ruleset, edits) ->
      # open our editor modal so we can save it
      $scope.clickRuleset(ruleset)
      $scope.dialog.opened.then ->
        modalScope = $modalStack.getTop().value.modalScope
        edits(modalScope)
        modalScope.ok()
      $timeout.flush()


    it 'should show warning when attempting an infinite loop', ->

      flowService.fetch(flows.webhook_rule_first.id).then ->
        $scope.flow = flowService.flow
        connection =
          sourceId: 'c81d60ec-9a74-48d6-a55f-e70a5d7195d3'
          targetId: '9b3b6b7d-13ec-46ea-8918-a83a4099be33'

        expect($scope.dialog).toBe(undefined)
        $scope.onBeforeConnectorDrop(connection)

        $scope.dialog.opened.then ->
          modalScope = $modalStack.getTop().value.modalScope
          expect(modalScope.title, 'Infinite Loop')

      $http.flush()

    it 'should view localized flows without org language', ->
      # mock our contact fields
      flowService.contactFieldSearch = []

      flowService.fetch(flows.webhook_rule_first.id).then ->
        actionset = flowService.flow.action_sets[0]
        $scope.clickAction(actionset, actionset.actions[0])
        expect($scope.dialog).not.toBe(undefined)

        $scope.dialog.opened.then ->
          modalScope = $modalStack.getTop().value.modalScope

          expect(flowService.language.iso_code).toBe('eng')

          # but we do have base language
          expect(modalScope.base_language, 'eng')
          expect(modalScope.action.msg.eng, 'Testing this out')

      $http.flush()

    it 'should allow ruleset category translation', ->

      # go grab our flow
      flowService.fetch(flows.webhook_rule_first.id)
      flowService.contactFieldSearch = []
      $http.flush()

      ruleset = flowService.flow.rule_sets[0]

      editRules ruleset, (scope) ->
        expect(flowService.language.iso_code).toBe('eng')

        # but we do have base language
        expect(scope.base_language).toBe('eng')
        expect(scope.ruleset.uuid).toBe(ruleset.uuid)

      # now toggle our language so we are in translation mode
      flowService.language = {iso_code:'ara', name:'Arabic'}
      editRuleset ruleset, (scope) ->
        # we should be in translation mode now
        expect(scope.languages.from).toBe('eng')
        expect(scope.languages.to).toBe('ara')

    it 'should filter split options based on flow type', ->

      loadFavoritesFlow()

      ruleset = flowService.flow.rule_sets[0]
      editRules ruleset, (scope) ->

        expect(scope.isVisibleRulesetType(getRuleConfig('wait_message'))).toBe(true)
        expect(scope.isVisibleRulesetType(getRuleConfig('webhook'))).toBe(true)

        # these are for ivr
        expect(scope.isVisibleRulesetType(getRuleConfig('wait_digits'))).toBe(false)
        expect(scope.isVisibleRulesetType(getRuleConfig('wait_digit'))).toBe(false)
        expect(scope.isVisibleRulesetType(getRuleConfig('wait_recording'))).toBe(false)

        # now pretend we are a voice flow
        flowService.flow.flow_type = 'V'
        expect(scope.isVisibleRulesetType(getRuleConfig('wait_digits'))).toBe(true)
        expect(scope.isVisibleRulesetType(getRuleConfig('wait_digit'))).toBe(true)
        expect(scope.isVisibleRulesetType(getRuleConfig('wait_recording'))).toBe(true)

        # and now a survey flow
        flowService.flow.flow_type = 'S'
        expect(scope.isVisibleRulesetType(getRuleConfig('wait_message'))).toBe(true)
        expect(scope.isVisibleRulesetType(getRuleConfig('wait_digits'))).toBe(false)
        expect(scope.isVisibleRulesetType(getRuleConfig('webhook'))).toBe(false)

        # USSD flow
        flowService.flow.flow_type = 'U'
        expect(scope.isVisibleRulesetType(getRuleConfig('wait_menu'))).toBe(true)
        expect(scope.isVisibleRulesetType(getRuleConfig('wait_ussd'))).toBe(true)
        expect(scope.isVisibleRulesetType(getRuleConfig('wait_message'))).toBe(false)

      $timeout.flush()

    it 'should create timeout rules if necessary', ->

      loadFavoritesFlow()
      ruleset = flowService.flow.rule_sets[0]

      # five rules and our other
      expect(ruleset.rules.length).toBe(6)

      editRules ruleset, (scope) ->
        scope.formData.hasTimeout = true
        scope.formData.timeout = scope.formData.timeoutOptions[5]

      # now we have five rules, our other, and a timeout
      ruleset = flowService.flow.rule_sets[0]
      expect(ruleset.rules.length).toBe(7)

      # checkout our timeout rule as the right settings
      lastRule = ruleset.rules[ruleset.rules.length - 1]
      expect(lastRule['test']['type']).toBe('timeout')
      expect(lastRule['test']['minutes']).toBe(10)

      # simulate open ended questions with timeout
      editRules ruleset, (scope) ->
        scope.ruleset.ruleset_type = 'wait_message'
        scope.ruleset.rules = []
        scope.formData.hasTimeout = true
        scope.formData.timeout = scope.formData.timeoutOptions[5]

      # now should have 2 rules; All responses and the timeout
      ruleset = flowService.flow.rule_sets[0]
      expect(ruleset.rules.length).toBe(2)

      allResponseRule = ruleset.rules[0]
      timeoutRule = ruleset.rules[1]

      expect(allResponseRule['test']['type']).toBe('true')
      expect(allResponseRule.category.base).toBe('All Responses')
      expect(timeoutRule['test']['type']).toBe('timeout')
      expect(timeoutRule['test']['minutes']).toBe(10)

    it 'should save group split rulesets', ->
      loadFavoritesFlow()
      ruleset = flowService.flow.rule_sets[0]

      editRules ruleset, (scope) ->
        scope.ruleset.ruleset_type = 'group'
        scope.splitEditor =
          omnibox:
            selected:
              groups: [{name:"Can't Hold Us", id:'group1_uuid'}]
              variables: []

      ruleset = flowService.flow.rule_sets[0]
      expect(ruleset.ruleset_type).toBe('group')
      expect(ruleset.rules.length).toBe(2)
      expect(ruleset.operand).toBe('@step.value')
      expect(JSON.stringify(ruleset.rules[0].test)).toBe('{"type":"in_group","test":{"name":"Can\'t Hold Us","uuid":"group1_uuid"}}')
      expect(JSON.stringify(ruleset.rules[1].test)).toBe('{"test":"true","type":"true"}')

    it 'should retain destination for group split', ->
      loadFavoritesFlow()

      ruleset = flowService.flow.rule_sets[0]
      editRules ruleset, (scope) ->
        scope.ruleset.ruleset_type = 'group'
        scope.splitEditor =
          omnibox:
            selected:
              groups: [{name:"Can't Hold Us", id:'group1_uuid'}]
              variables: []

      # set a destination
      flowService.flow.rule_sets[0].rules[0]['destination'] = flowService.flow.action_sets[1].uuid

      # now edit our rule again
      ruleset = flowService.flow.rule_sets[0]
      editRules ruleset, (scope) ->
        scope.ruleset.ruleset_type = 'group'
        scope.splitEditor =
          omnibox:
            selected:
              groups: [
                { name:"Can't Hold Us", id:'group1_uuid' }
                { name:"In the Pines", id:'group2_uuid' }
                { name:"New Group" }
              ]
              variables: []

      ruleset = flowService.flow.rule_sets[0]
      expect(ruleset.rules.length).toBe(4)

      expect(ruleset.rules[0]['destination']).toBe(flowService.flow.action_sets[1].uuid)
      expect(JSON.stringify(ruleset.rules[0].test)).toBe('{"type":"in_group","test":{"name":"Can\'t Hold Us","uuid":"group1_uuid"}}')
      expect(JSON.stringify(ruleset.rules[1].test)).toBe('{"type":"in_group","test":{"name":"In the Pines","uuid":"group2_uuid"}}')
      expect(JSON.stringify(ruleset.rules[2].test)).toBe('{"type":"in_group","test":{"name":"New Group"}}')
      expect(JSON.stringify(ruleset.rules[3].test)).toBe('{"test":"true","type":"true"}')

    it 'should honor order when saving group split', ->

      loadFavoritesFlow()
      ruleset = flowService.flow.rule_sets[0]

      # now try reordering our rules
      editRules ruleset, (scope) ->
        scope.ruleset.ruleset_type = 'group'
        scope.splitEditor =
          omnibox:
            selected:
              groups: [
                { name:"Can't Hold Us", id:'group1_uuid' }
                { name:"In the Pines", id:'group2_uuid' }
              ]
              variables: []

      ruleset = flowService.flow.rule_sets[0]

      # now try reordering our rules
      editRules ruleset, (scope) ->
        scope.ruleset.ruleset_type = 'group'
        scope.splitEditor =
          omnibox:
            selected:
              groups: [
                { name:"In the Pines", id:'group2_uuid' }
                { name:"Can't Hold Us", id:'group1_uuid' }
              ]
              variables: []

      ruleset = flowService.flow.rule_sets[0]
      expect(ruleset.rules.length).toBe(3)

      # check that our order has been swapped
      expect(JSON.stringify(ruleset.rules[0].test)).toBe('{"type":"in_group","test":{"name":"In the Pines","uuid":"group2_uuid"}}')
      expect(JSON.stringify(ruleset.rules[1].test)).toBe('{"type":"in_group","test":{"name":"Can\'t Hold Us","uuid":"group1_uuid"}}')

    it 'should save random rulesets', ->
      loadFavoritesFlow()

      ruleset = flowService.flow.rule_sets[0]
      editRules ruleset, (scope) ->
        scope.ruleset.ruleset_type = 'random'
        scope.formData.buckets = 2
        scope.updateRandomBuckets()

      ruleset = flowService.flow.rule_sets[0]
      expect(ruleset.ruleset_type).toBe('random')
      expect(ruleset.rules.length).toBe(2)
      expect(ruleset.operand).toBe('@(RAND())')
      expect(JSON.stringify(ruleset.rules[0].test)).toBe('{"type":"between","min":"0","max":"0.5"}')
      expect(JSON.stringify(ruleset.rules[1].test)).toBe('{"type":"between","min":"0.5","max":"1"}')

      # now try setting it to 4 buckets
      editRules ruleset, (scope) ->
        scope.ruleset.ruleset_type = 'random'
        scope.formData.buckets = 4
        scope.updateRandomBuckets()

      ruleset = flowService.flow.rule_sets[0]
      expect(ruleset.rules.length).toBe(4)
      expect(JSON.stringify(ruleset.rules[0].test)).toBe('{"type":"between","min":"0","max":"0.25"}')
      expect(JSON.stringify(ruleset.rules[1].test)).toBe('{"type":"between","min":"0.25","max":"0.5"}')
      expect(JSON.stringify(ruleset.rules[2].test)).toBe('{"type":"between","min":"0.5","max":"0.75"}')
      expect(JSON.stringify(ruleset.rules[3].test)).toBe('{"type":"between","min":"0.75","max":"1"}')

      # flip it over to a normal wait ruleset
      editRules ruleset, (scope) ->
        scope.ruleset.ruleset_type = 'wait_message'
        scope.ruleset.rules = []

      ruleset = flowService.flow.rule_sets[0]
      expect(ruleset.rules.length).toBe(1)
      expect(ruleset.operand).toBe('@step.value')

    it 'should save resthook rulesets', ->

      loadFavoritesFlow()

      ruleset = flowService.flow.rule_sets[0]
      editRules ruleset, (scope) ->
        scope.ruleset.ruleset_type = 'resthook'
        scope.splitEditor =
          resthook:
            selected: [{id:'resthook-name'}]

      ruleset = flowService.flow.rule_sets[0]
      expect(ruleset.ruleset_type).toBe('resthook')
      expect(ruleset.rules.length).toBe(2)
      expect(JSON.stringify(ruleset.rules[0].test)).toBe('{"type":"webhook_status","status":"success"}')
      expect(JSON.stringify(ruleset.rules[1].test)).toBe('{"type":"webhook_status","status":"failure"}')

    it 'should save webhook rulesets', ->

      loadFavoritesFlow()

      ruleset = flowService.flow.rule_sets[0]
      editRules ruleset, (scope) ->
        scope.ruleset.ruleset_type = 'webhook'
        scope.formData.webhook = 'http://www.nyaruka.com'
        scope.formData.webhook_action = 'POST'
        scope.formData.webhook_headers = [{name: 'Authorization', value: 'Token 12345'}]

      ruleset = flowService.flow.rule_sets[0]
      expect(ruleset.ruleset_type).toBe('webhook')
      expect(ruleset.rules.length).toBe(2)
      expect(JSON.stringify(ruleset.rules[0].test)).toBe('{"type":"webhook_status","status":"success"}')
      expect(JSON.stringify(ruleset.rules[1].test)).toBe('{"type":"webhook_status","status":"failure"}')

      # our config should have a url
      expect(ruleset.config.webhook).toBe('http://www.nyaruka.com')
      expect(ruleset.config.webhook_action).toBe('POST')
      expect(ruleset.config.webhook_headers[0]['name']).toBe('Authorization')
      expect(ruleset.config.webhook_headers[0]['value']).toBe('Token 12345')

      # do it again, make sure we have the right number of rules
      editRules ruleset, (scope) ->
        scope.ruleset.ruleset_type = 'webhook'
      expect(flowService.flow.rule_sets[0].rules.length).toBe(2)

      # now save it as a regular wait
      editRules ruleset, (scope) ->
        scope.ruleset.ruleset_type = 'wait_message'

      ruleset = flowService.flow.rule_sets[0]
      for rule in ruleset.rules
        if rule.test.type == 'webhook_status'
          fail('Webhook rule found on non webhook ruleset')
          break

      # it should be All Responses, not Other
      expect(ruleset.rules.length).toBe(1)
      expect(ruleset.rules[0].category.base).toBe('All Responses')

    it 'should save subflow rulesets', ->
      loadFavoritesFlow()

      ruleset = flowService.flow.rule_sets[0]

      editRules ruleset, (scope) ->
        # simulate selecting a child flow
        scope.ruleset.ruleset_type = 'subflow'
        scope.splitEditor =
          flow:
            selected:[{id: 'cf785f12-658a-4821-ae62-7735ea5c6cef', text: 'Child Flow'}]

      ruleset = flowService.flow.rule_sets[0]
      expect(ruleset.ruleset_type).toBe('subflow')
      expect(ruleset.rules.length).toBe(2)
      expect(JSON.stringify(ruleset.config)).toBe('{"flow":{"name":"Child Flow","uuid":"cf785f12-658a-4821-ae62-7735ea5c6cef"}}')

      # click on it a second time and save it to make sure we
      # still end up with only two rules
      editRules ruleset, (scope) ->
        # simulate selecting a child flow
        scope.ruleset.ruleset_type = 'subflow'
        scope.splitEditor =
          flow:
            selected:[{id: 'cf785f12-658a-4821-ae62-7735ea5c6cef', text: 'Child Flow'}]

      ruleset = flowService.flow.rule_sets[0]
      expect(ruleset.rules.length).toBe(2)

      # now save it as a regular wait
      editRules ruleset, (scope) ->
        scope.ruleset.ruleset_type = 'wait_message'

      ruleset = flowService.flow.rule_sets[0]
      for rule in ruleset.rules
        if rule.test.type == 'subflow'
          fail('Subflow rule found on non subflow ruleset')
          break

     it 'should save airtime rulesets', ->

      loadFavoritesFlow()

      ruleset = flowService.flow.rule_sets[0]
      editRules ruleset, (scope) ->
        scope.ruleset.ruleset_type = 'airtime'

      # our ruleset should have 2 rules
      ruleset = flowService.flow.rule_sets[0]
      expect(ruleset.ruleset_type).toBe('airtime')
      expect(ruleset.rules.length).toBe(2)

    it 'should maintain connections which toggling timeouts', ->

      loadFavoritesFlow()

      # our first ruleset, starts off with five rules
      ruleset = flowService.flow.rule_sets[0]
      expect(ruleset.rules.length).toBe(6)

      # make our last "true" rule route to the entry node
      ruleset.rules[4].destination = '127f3736-77ce-4006-9ab0-0c07cea88956'

      # click on the ruleset and then ok
      editRules(ruleset, (scope) -> scope.ruleset.ruleset_type = 'wait_message')

      # our route should still be there
      ruleset = flowService.flow.rule_sets[0]
      expect(ruleset.rules[4].destination).toBe('127f3736-77ce-4006-9ab0-0c07cea88956')

      # click on ruleset, then check timeout option
      editRules(ruleset, (scope) -> scope.formData.hasTimeout = true)

      # should now have 6 rules to account for the timeout
      ruleset = flowService.flow.rule_sets[0]
      expect(ruleset.rules.length).toBe(7)

      # but our route should still be there
      expect(ruleset.rules[4].destination).toBe('127f3736-77ce-4006-9ab0-0c07cea88956')

    it 'should maintain connections on prescribed rulesets', ->

      loadFavoritesFlow()

      # our subflow selection, used on each edit
      splitEditor =
        flow:
          selected: [{id: 'cf785f12-658a-4821-ae62-7735ea5c6cef', text: 'Child Flow'}]

      # turn our first ruleset into a subflow
      ruleset = flowService.flow.rule_sets[0]
      editRules ruleset, (scope) ->
        scope.ruleset.ruleset_type = 'subflow'
        scope.splitEditor = splitEditor

      # route our two subflow rules
      ruleset = flowService.flow.rule_sets[0]
      ruleset.rules[0].destination = 'destination a'
      ruleset.rules[1].destination = 'destination b'

      # now click on the ruleset again
      editRules ruleset, (scope) ->
        scope.ruleset.ruleset_type = 'subflow'
        scope.splitEditor = splitEditor

      # destinations should still be there
      ruleset = flowService.flow.rule_sets[0]
      expect(ruleset.rules[0].destination).toBe('destination a')
      expect(ruleset.rules[1].destination).toBe('destination b')


    it 'should filter action options based on flow type', ->

      loadFavoritesFlow()

      actionset = flowService.flow.action_sets[0]
      action = actionset.actions[0]
      $scope.clickAction(actionset, action)

      $scope.dialog.opened.then ->
        modalScope = $modalStack.getTop().value.modalScope

        expect(modalScope.validActionFilter(getAction('reply'))).toBe(true)
        expect(modalScope.validActionFilter(getAction('end_ussd'))).toBe(false)

        # ivr only
        expect(modalScope.validActionFilter(getAction('say'))).toBe(false)
        expect(modalScope.validActionFilter(getAction('play'))).toBe(false)
        expect(modalScope.validActionFilter(getAction('api'))).toBe(true)
        expect(modalScope.validActionFilter(getAction('end_ussd'))).toBe(false)

        # pretend we are a voice flow
        flowService.flow.flow_type = 'V'
        expect(modalScope.validActionFilter(getAction('reply'))).toBe(true)
        expect(modalScope.validActionFilter(getAction('say'))).toBe(true)
        expect(modalScope.validActionFilter(getAction('play'))).toBe(true)
        expect(modalScope.validActionFilter(getAction('api'))).toBe(true)
        expect(modalScope.validActionFilter(getAction('end_ussd'))).toBe(false)

        # now try a survey
        flowService.flow.flow_type = 'S'
        expect(modalScope.validActionFilter(getAction('reply'))).toBe(true)
        expect(modalScope.validActionFilter(getAction('say'))).toBe(false)
        expect(modalScope.validActionFilter(getAction('play'))).toBe(false)
        expect(modalScope.validActionFilter(getAction('api'))).toBe(false)
        expect(modalScope.validActionFilter(getAction('end_ussd'))).toBe(false)

        # USSD flow
        flowService.flow.flow_type = 'U'
        expect(modalScope.validActionFilter(getAction('reply'))).toBe(true)
        expect(modalScope.validActionFilter(getAction('say'))).toBe(false)
        expect(modalScope.validActionFilter(getAction('play'))).toBe(false)
        expect(modalScope.validActionFilter(getAction('api'))).toBe(false)
        expect(modalScope.validActionFilter(getAction('end_ussd'))).toBe(true)

      $timeout.flush()

    it 'should allow users to create groups in place', ->

      loadFavoritesFlow()

      actionset = flowService.flow.action_sets[0]
      action = actionset.actions[0]

      editAction actionset, action, (modalScope) ->
        omnibox =
          groups: ["Can't Hold Us"]
          variables: []
        modalScope.saveGroups('add_group', omnibox)

      actionset = flowService.flow.action_sets[0]
      action = actionset.actions[0]

      expect(action.type).toBe('add_group')
      expect(action.groups.length).toBe(1)
      expect(action.groups[0]).toBe("Can't Hold Us")

      # our reply should be gone now
      expect(action.msg).toBe(undefined)

    it 'should let you remove all groups', ->
      loadFavoritesFlow()

      actionset = flowService.flow.action_sets[0]
      action = actionset.actions[0]

      # remove all groups
      editAction actionset, action, (modalScope) ->
        modalScope.saveGroups('del_group', null, true)

      actionset = flowService.flow.action_sets[0]
      action = actionset.actions[0]

      expect(action.type).toBe('del_group')
      expect(action.groups.length).toBe(0)

    it 'updateContactAction should not duplicate fields on save', ->

      loadFavoritesFlow()

      flowService.contactFieldSearch = [{id:'national_id',text:'National ID'}]
      flowService.updateContactSearch = [{id:'national_id',text:'National ID'}]

      # find an actin to edit
      actionset = flowService.flow.action_sets[0]
      action = actionset.actions[0]

      editAction actionset, action, (modalScope) ->
        field =
          id: 'national_id'
          text: 'National ID'
        modalScope.saveUpdateContact(field, '@flow.natl_id')

      # should still have one to choose from
      expect(flowService.contactFieldSearch.length).toBe(1)
      expect(flowService.updateContactSearch.length).toBe(1)

      editAction actionset, action, (modalScope) ->
        field =
          id: '[_NEW_]a_new_field'
          text: 'Add new variable: A New Field'
        modalScope.saveUpdateContact(field, 'save ,e')

      # new fields should be tacked on the end
      expect(flowService.contactFieldSearch.length).toBe(2)
      expect(flowService.updateContactSearch.length).toBe(2)

      # check that the NEW markers are stripped off
      added = flowService.contactFieldSearch[1]
      expect(added.id).toBe('a_new_field')
      expect(added.text).toBe('A New Field')

      added = flowService.updateContactSearch[1]
      expect(added.id).toBe('a_new_field')
      expect(added.text).toBe('A New Field')


    it 'should give proper language choices', ->

      loadFavoritesFlow()

      actionset = flowService.flow.action_sets[0]
      action = actionset.actions[0]
      action.type = 'lang'
      action.name = 'Achinese'
      action.lang = 'ace'

      $scope.clickAction(actionset, action)

      $scope.dialog.opened.then ->
        modalScope = $modalStack.getTop().value.modalScope

        # Achinese should be added as an option since it was previously
        # set on the flow even though it is not an org language
        expect(modalScope.languages[0]).toEqual({name:'Achinese', iso_code:'ace'})

        # make sure 'Default' isn't added as an option
        expect(modalScope.languages.length).toEqual(1)

      $timeout.flush()

    it 'should have the USSD Menu synced with ruleset', ->

      # load a USSD flow
      flowService.fetch(flows.ussd_example.id)
      flowService.contactFieldSearch = []
      flowService.language = {iso_code:'base'}
      $http.flush()

      ruleset = flowService.flow.rule_sets[0]
      editRuleset ruleset, (scope) ->
        for rule in scope.ruleset.rules
          if rule.label
            expect(rule.uuid).toBeDefined()
            expect(rule.category.base).toBeDefined()
            expect(rule.label).toBeDefined()
            expect(rule._config.type).toBe('eq')

    it 'isRuleComplete should have proper validation', ->

      loadFavoritesFlow()

      ruleset = flowService.flow.rule_sets[0]
      editRuleset ruleset, (scope) ->

        rule_tests = [
          {rule: {category: null, _config: {operands: null}, test: {}}, complete: false},
          {rule: {category: {_base: null}, _config: {operands: 0}, test: {}}, complete: false},
          {rule: {category: {_base: 'Red'},_config: {operands: 0}, test: {}}, complete: true},
          {rule: {category: {_base: 'Red'}, _config: {operands: 1}, test: {}}, complete: false},
          {rule: {category: {_base: 'Red'}, _config: {operands: 1}, test: {_base: 'Red'}}, complete: true},
          {rule: {category: {_base: 'Red'}, _config: {operands: 1}, test: {_base: 'Red'}}, complete: true},
          {rule: {category: {_base: 'Red'}, _config: {type: 'between', operands: 2}, test: {min: null, max: null}}, complete: false},
          {rule: {category: {_base: 'Red'}, _config: {type: 'between', operands: 2}, test: {min: 5, max: null}}, complete: false},
          {rule: {category: {_base: 'Red'}, _config: {type: 'between', operands: 2}, test: {min: null, max: 10}}, complete: false},
          {rule: {category: {_base: 'Red'}, _config: {type: 'between', operands: 2}, test: {min: 5, max: 10}}, complete: true},
          {rule: {category: {_base: 'Red'}, _config: {type: 'ward', operands: 2}, test: {state: null, district: null}}, complete: false},
          {rule: {category: {_base: 'Red'}, _config: {type: 'ward', operands: 2}, test: {state: 'state', district: null}}, complete: false},
          {rule: {category: {_base: 'Red'}, _config: {type: 'ward', operands: 2}, test: {state: null, district: 'district'}}, complete: false},
          {rule: {category: {_base: 'Red'}, _config: {type: 'ward', operands: 2}, test: {state: 'state', district: 'district'}}, complete: true},
        ]

        for rule_test in rule_tests
          expect(scope.isRuleComplete(rule_test['rule'])).toBe(rule_test['complete'])

          
     it 'should generate json quick replies to send', ->
      loadFavoritesFlow()

      actionset = flowService.flow.action_sets[0]
      action = actionset.actions[0]
    
      json_quick_reply = ['Yes', 'No']

      editAction actionset, action, (scope) ->
        scope.quickReplies = []
        scope.action.quick_replies = {}
        scope.addNewQuickReply()
        scope.quickReplies[0] = 'Yes'
        scope.quickReplies[1] = 'No'
        scope.formData.msg = "test"
        scope.saveMessage('test', type='reply')

      actionset = flowService.flow.action_sets[0]
      action = actionset.actions[0]
      expect(JSON.stringify(action.quick_replies)).toBe(JSON.stringify(json_quick_reply))