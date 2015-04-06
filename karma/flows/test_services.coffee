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
