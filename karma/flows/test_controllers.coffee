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

  beforeEach inject((_$rootScope_, _$compile_, _$log_, _$modal_) ->
      $rootScope = _$rootScope_.$new()
      $scope = $rootScope.$new()
      $modal = _$modal_

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
        expect($scope.dialog).not.toBe(undefined)

      $http.flush()
