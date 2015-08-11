describe 'Controllers:', ->

  beforeEach ->
    # initialize our angular app
    module 'app'
    module 'partials'

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
      'favorites': { id: 1, languages:[] },
      'rules_first': { id: 2, languages:[] },
      'loop_detection': { id: 3, languages:[] },
      'webhook_rule_first': { id: 4, languages:[] },
    }

    $http.whenGET('/contactfield/json/').respond([])
    $http.whenGET('/label/').respond([])

    for file, config of flows

      $http.whenPOST('/flow/json/' + config.id + '/').respond()
      $http.whenGET('/flow/json/' + config.id + '/').respond(
        {
          flow: getJSONFixture(file + '.json').flows[0].definition,
          languages: config.languages
        }
      )

      $http.whenGET('/flow/versions/' + config.id + '/').respond(
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

    beforeEach inject(($controller, _Flow_) ->

      flowService = _Flow_
      flowController = $controller 'FlowController',
        $scope: $scope
        $rootScope: $rootScope
        $log: $log
        Flow: flowService
    )

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

          # we don't have a language
          expect(flowService.language).toBe(undefined)

          # but we do have base language
          expect(modalScope.base_language, 'eng')
          expect(modalScope.action.msg.eng, 'Testing this out')

      $http.flush()

    it 'should should ruleset category translation', ->

      # go grab our flow
      flowService.fetch(flows.webhook_rule_first.id)
      flowService.contactFieldSearch = []
      $http.flush()

      ruleset = flowService.flow.rule_sets[0]
      $scope.clickRuleset(ruleset)
      $scope.dialog.opened.then ->
        modalScope = $modalStack.getTop().value.modalScope

        # we don't have a language
        expect(flowService.language).toBe(undefined)

        # but we do have base language
        expect(modalScope.base_language).toBe('eng')
        expect(modalScope.ruleset.uuid).toBe(ruleset.uuid)

      $timeout.flush()

      # now toggle our language so we are in translation mode
      flowService.language = {iso_code:'ara', name:'Arabic'}
      $scope.clickRuleset(ruleset)
      $scope.dialog.opened.then ->
        modalScope = $modalStack.getTop().value.modalScope

        # we should be in translation mode now
        expect(modalScope.languages.from).toBe('eng')
        expect(modalScope.languages.to).toBe('ara')

      $timeout.flush()
