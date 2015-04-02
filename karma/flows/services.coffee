gettext = (text) ->
  return text

getNode = (flow, uuid) ->
  for actionset in flow.action_sets
    if actionset.uuid == uuid
      return actionset

  for ruleset in flow.rule_sets
    if ruleset.uuid == uuid
      return ruleset


describe 'Services:', ->

  test = this

  # initialize our angular app
  beforeEach ->
    module 'app'

  # we want access to http and flow service
  test.httpBackend = null
  test.flowService = null
  test.rootScope = null
  test.window = null

  beforeEach inject(($httpBackend, Flow, $rootScope, $window) ->
    test.flowService = Flow
    test.http = $httpBackend
    test.rootScope = $rootScope.$new()
    test.window = $window

    # bootstrap our json fixtures
    jasmine.getJSONFixtures().fixturesPath='base/media/test_flows';

    # wire up our mock flows
    test.flows = {
      'favorites': { id: 1, languages:[] },
      'rules_first': { id: 2, languages:[] }
    }

    $httpBackend.whenGET('/contactfield/json/').respond([])
    $httpBackend.whenGET('/label/').respond([])

    for file, config of test.flows

      $httpBackend.whenPOST('/flow/json/' + config.id + '/').respond(
      )

      $httpBackend.whenGET('/flow/json/' + config.id + '/').respond(
        {
          flow: getJSONFixture(file + '.json'),
          languages: config.languages
        }
      )

      $httpBackend.whenGET('/flow/versions/' + config.id + '/').respond(
        [],  {'content-type':'application/json'}
      )

      $httpBackend.whenGET('/flow/completion/?flow=' + config.id).respond([])
  )

  describe 'Flow service', ->

    afterEach ->
      test.http.flush()
      test.http.verifyNoOutstandingRequest()

    it 'should set flow defintion after fetching', ->
      test.window.flowId = test.flows.rules_first.id
      rootScope = test.rootScope
      test.flowService.fetch().then (response) ->
        expect(rootScope.flow).not.toBe(null)
      , (error) ->
        throwError('Failed to fetch mock flow data:' + error)


    it 'should determine the flow entry', ->
      test.window.flowId = test.flows.favorites.id
      test.flowService.fetch().then ->

        # our entry should already be set from reading in the file
        expect(test.rootScope.flow.entry).toBe('ec4c8328-f7b6-4386-90c0-b7e6a3517e9b')

        # now determine the start point
        test.flowService.determineFlowStart(test.rootScope.flow)

        # it shouldn't have changed from what we had
        expect(test.rootScope.flow.entry).toBe('ec4c8328-f7b6-4386-90c0-b7e6a3517e9b')

        # now let's move our entry node down
        entry = getNode(test.rootScope.flow, 'ec4c8328-f7b6-4386-90c0-b7e6a3517e9b')
        entry.y = 200
        test.flowService.determineFlowStart(test.rootScope.flow)

        # our 'other' action set is now the top
        expect(test.rootScope.flow.entry).toBe('dcd9541a-0263-474e-b3f1-03a28993f95a')
