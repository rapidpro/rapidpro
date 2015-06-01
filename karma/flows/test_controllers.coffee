describe 'Controllers:', ->

  beforeEach ->
    # initialize our angular app
    module 'app'

  $rootScope = null
  $compile = null
  $scope = null
  $modal = null
  $log = null
  window.mutable = true


  beforeEach inject((_$rootScope_, _$compile_, _$log_, _$modal_) ->
      $rootScope = _$rootScope_.$new()
      $scope = $rootScope.$new()
      $modal = _$modal_
      $scope.$parent = $rootScope

      $scope.$parent.flow =
        base_language: 'eng'

      $scope.$parent.language = { iso_code: 'eng' }

      $compile = _$compile_
      $log = _$log_
    )

  # TODO: FlowController does more than it should. It should not have knowledge of
  # of jsplumb connection objects and should lean more on services.
  describe 'FlowController', ->

    flowController = null

    beforeEach inject(($controller) ->
      flowController = $controller 'FlowController',
        $scope: $scope
        $rootScope: $rootScope
        $log: $log
    )






