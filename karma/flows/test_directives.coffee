describe 'Directives:', ->

  $rootScope = null
  $compile = null
  $timeout = null
  $http = null
  Flow = null
  utils = null
  $templateCache = null

  beforeEach ->
    # initialize our angular app
    module('app')
    module('partials')

  beforeEach inject((_$rootScope_, _$compile_, _$timeout_, $httpBackend, _$templateCache_, _Flow_, _utils_) ->
    $rootScope = _$rootScope_.$new()
    $compile = _$compile_
    $timeout = _$timeout_
    $http = $httpBackend
    $templateCache = _$templateCache_
    Flow = _Flow_
    utils = _utils_
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

      Flow.flow = utils.clone(getJSONFixture('favorites.json').flows[0])
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

  describe 'SMS directive', ->

    scope = null
    ele = null

    beforeEach ->
      Flow.flow = utils.clone(getJSONFixture('favorites.json').flows[0])
      scope = $rootScope.$new()

      # create an element for our directive and compile it
      ele = angular.element("<div ng-form><span sms='action.msg' data-message='message'/></div>")

    it 'should show sms widget', ->

      scope.action = Flow.flow.action_sets[0].actions[0]

      ele = $compile(ele)(scope)
      scope.$digest()
      $timeout.flush()

      html = ele.html()

      result = scope.$$childHead
      expect(html).toContain(132) # 132 characters for the counter
      expect(html).toContain(" \\/ 1") # of one message

      expect(result.showCounter).toBe(true)
      expect(result.characters).toBe(132)
      expect(result.messages).toBe(1)
      expect(result.sms).toEqual({base:'What is your favorite color?'})
      expect(result.message).toEqual('What is your favorite color?')

    it 'should do fail gracefully with null messages', ->

      # now try with a null message
      scope.action = Flow.flow.action_sets[0].actions[0]
      scope.action['msg']['base'] = null

      ele = $compile(ele)(scope)
      scope.$digest()
      $timeout.flush()

      result = scope.$$childHead
      expect(result.characters).toBe(160)
      expect(result.messages).toBe(0)
      expect(result.message).not.toBe(undefined)
      expect(result.message).not.toBe(null)

  describe 'Validate Type', ->

    it 'should validate numbers and variables for numeric rules', ->
      ele = angular.element("<ng-form><input ng-model='rule.test._base' name='operand' ng-required='rule.category._base' ng-change='updateCategory(rule)' type='text' class='operand' validate-type='[[rule._config.type]]' /></ng-form>")
      scope = $rootScope.$new()

      config =
        type:'eq'
        name:'Equal to'
        verbose_name: 'has a number equal to'
        operands: 1
        localized: true

      scope.rule =
        _config: config
        test: {_base:'12'}
        type: 'eq'
        category: {_base: 'Age'}


      $compile(ele)(scope)
      scope.$digest()
      $timeout.flush()

      expect(ele.html()).toMatch(/ng-valid-validate-type/)

      scope.rule =
        _config: config
        test: {_base:'@contact.age'}
        type: 'eq'
        category: {_base: 'Age'}


      $compile(ele)(scope)
      scope.$digest()
      $timeout.flush()

      expect(ele.html()).toMatch(/ng-valid-validate-type/)

      # should not match words
      scope.rule =
        _config: config
        test: {_base:'old'}
        type: 'eq'
        category: {_base: 'Age'}


      $compile(ele)(scope)
      scope.$digest()
      $timeout.flush()

      expect(ele.html()).toMatch(/ng-invalid-validate-type/)


  describe 'USSD directive', ->

    scope = null
    ele = null
    html = null
    result = null

    beforeEach ->
      Flow.flow = utils.clone(getJSONFixture('ussd_example.json').flows[0])
      scope = $rootScope.$new()

    compileElement = ->
      scope.ruleset = []
      scope.ruleset.rules = []

      ele = $compile(ele)(scope)
      scope.$digest()
      $timeout.flush()

      html = ele.html()
      result = ele.children().scope()

    it 'should have USSD flow type', ->
      expect(Flow.flow.flow_type).toBe('U')

    it 'should create a USSD Menu widget', ->
      # ussd="0" is for USSD Menu functionality
      ele = angular.element("<div ng-form><span class='wait-ussd' ussd='0'/></div>")

      compileElement()

      expect(html).toContain('Add menu:')

      expect(result.USSD_MENU).toBeTruthy()
      expect(result.USSD_RESPONSE).toBeFalsy()

      # USSD menu directive creates a default menu point for the widget, that initially takes up some characters
      expect(result.characters).toBe(178)

    it 'should create a USSD response widget', ->
      # ussd="1" is for USSD Response functionality
      ele = angular.element("<div ng-form><span class='wait-ussd' ussd='1'/></div>")

      compileElement()

      expect(result.USSD_MENU).toBeFalsy()
      expect(result.USSD_RESPONSE).toBeTruthy()

      # no menus here, the default textarea is initially empty hence should be the max length of a USSD message
      expect(result.characters).toBe(182)

    it 'should call countCharacters for every menu change', ->

      ele = angular.element("<div ng-form><span class='wait-ussd' ussd='0'/></div>")

      compileElement()

      spyOn(result, 'countCharacters')

      testItem =
        uuid: uuid()
        option: ""
        label:
          base: ""
        category:
          _autoName: true
          _base: ""

      result.updateMenu(testItem, 0)

      expect(result.countCharacters).toHaveBeenCalled();