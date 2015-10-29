describe 'Directives:', ->

  $rootScope = null
  $compile = null
  $timeout = null
  Flow = null

  beforeEach ->
    # initialize our angular app
    module 'app'

  beforeEach inject((_$rootScope_, _$compile_, _$timeout_, _Flow_) ->
    $rootScope = _$rootScope_.$new()
    $compile = _$compile_
    $timeout = _$timeout_
    Flow = _Flow_
  )

  describe 'Select2', ->

    it 'should show proper options for static list', ->
      ele = angular.element("<ng-form><input ng-model='field' name='field' text='[[action.label]]' select-static='[[contactFields]]' required='' key='[[action.field]]' type='hidden'/></ng-form>")
      scope = $rootScope.$new()
      scope.contactFields = [{id:'national_id',text:'National ID'}]
      scope.action =
        field: 'national_id'
        label: 'National ID'

      $compile(ele)(scope)
      scope.$digest()
      $timeout.flush()

      # should have created a select2 widget
      expect(ele.html()).toMatch(/select2/)

      # and the default should be national_id
      expect(ele.html()).toMatch(/national_id/)


  describe 'Action directive', ->

    it 'should show the correct message', ->

      Flow.flow = getJSONFixture('favorites.json').flows[0]
      scope = $rootScope.$new()

      # pick our first action to build some html for
      scope.action = Flow.flow.action_sets[0].actions[0]

      # our action translation hasn't been inspected yet
      expect(scope.action._missingTranslation).toBeUndefined()

      # create an element for our directive and compile it
      ele = angular.element("<div action='action'>[[action._translation]]</div>")
      $compile(ele)(scope)
      scope.$digest()

      # now our translation has been inspected, confirm its not missing
      expect(scope.action._missingTranslation).toBe(false)
      expect(ele.html()).toBe('What is your favorite color?')
