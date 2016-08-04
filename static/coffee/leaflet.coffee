
#============================================================================
# Configure our locations app
#============================================================================
app = angular.module("locations", ["monospaced.elastic"])

app.filter 'collapse', ->
  return (text) ->
    if text
      return text.replace(/\s*\n/g, ', ')
    return ''

#============================================================================
# We don't want to use {{ }} for template substitution as it collides with
# Django, use [[ ]] instead.
#============================================================================
app.config ($interpolateProvider) ->
  $interpolateProvider.startSymbol "[["
  $interpolateProvider.endSymbol "]]"

fadeStyle = (feature) ->
  weight: 1
  opacity: 1
  color: 'white'
  fillOpacity: 0.35
  fillColor: "#2387ca"

visibleStyle = (feature) ->
  weight: 1
  opacity: 1
  color: 'white'
  fillOpacity: 0.7
  fillColor: "#2387ca"

highlightStyle =
  weight: 3
  color: "white"
  fillOpacity: 1
  fillColor: "#2387ca"


app.directive "uniqueAlias", ->
  restrict: "A"
  require: "ngModel"
  scope:
    currentBoundary: '='
    boundaries: '='
    currentAliases: '='
  link: (scope, elem, attr, ngModel) ->
    ngModel.$parsers.unshift (value) ->

      valid = true
      value = value.toLowerCase()
      values = value.split('\n')

      # this is pretty inefficient, should cache some of this info for quicker comparisons
      if scope.currentBoundary
        scopeLevel = scope.currentBoundary.level

        for boundary in scope.boundaries
          if not valid
            break

          if boundary.osm_id != scope.currentBoundary.osm_id
            if boundary.name.toLowerCase() in values and boundary.level == scopeLevel
              valid = false

            if valid and boundary.aliases
                for alias in boundary.aliases.trim().split('\n')
                  if alias.toLowerCase() in values and boundary.level == scopeLevel
                    valid = false

            if not valid
              break

          if boundary.children?
            for child in boundary.children
              if child.osm_id != scope.currentBoundary.osm_id
                if child.name.toLowerCase() in values and child.level == scopeLevel
                  valid = false
                if valid and child.aliases
                  for alias in child.aliases.split('\n')
                    if alias.toLowerCase() in values and child.level == scopeLevel
                      valid = false

                if not valid
                  break

      ngModel.$setValidity "uniqueAlias", valid

      # if it's valid, return the value to the model
      result = if valid then value else undefined
      return result

    # add a formatter that will process each time the value is updated on the DOM element.
    ngModel.$formatters.unshift (value) ->
      ngModel.$setValidity "uniqueAlias", true
      return value
    return

#============================================================================
# Directive for building a leaflet off of a config object
#============================================================================
app.directive "leaflet", ["$http", ($http) ->
  link = (scope, element, attrs) ->

    scope.layerMap = {}

    scope.safeApply = (fn) ->
      phase = @$root.$$phase
      if phase is "$apply" or phase is "$digest"
        fn()  if fn and (typeof (fn) is "function")
      else
        @$apply fn

    # when state, district or ward change
    scope.$watchCollection ->
      [scope.stateBoundary, scope.districtBoundary, scope.wardBoundary]
    , (current, previous) ->

      scope.safeApply ->
        if current
          if scope.wardBoundary
            loadAdminLevel(scope.districtBoundary.osm_id, [scope.wardBoundary.osm_id])
          else if scope.districtBoundary
            loadAdminLevel(scope.stateBoundary.osm_id, [scope.districtBoundary.osm_id])
          else if scope.stateBoundary
            resetStates([scope.stateBoundary.osm_id])
          else
            resetStates([])
        else
          resetStates()

    scope.$watch ->
      scope.hoveredBoundary
    , (newHover, oldHover) ->
      if newHover
        highlightFeature(scope.layerMap[newHover.osm_id])
      if oldHover
        resetHighlight(scope.layerMap[oldHover.osm_id])

    highlightFeature = (layer) ->
      if layer

        layer.setStyle(highlightStyle)

        if !L.Browser.ie && !L.Browser.opera
          layer.bringToFront()

        if scope.info.update
          scope.info.update(layer.feature.properties)

    resetHighlight = (layer) ->

      if layer
        scope.states.resetStyle(layer)

      if scope.info.update
        scope.info.update()

    resetStates = (highlightOsmIds) ->
      console.log("resetStates: " + highlightOsmIds)

      if scope.districts and scope.map.hasLayer(scope.districts)
        scope.map.removeLayer(scope.districts)

      if scope.states and not scope.map.hasLayer(scope.states)
        scope.map.addLayer(scope.states)
        scope.states.setStyle(visibleStyle)

      if scope.states
        scope.map.fitBounds(scope.states.getBounds())

      scope.safeApply ->

        if scope.states
          for id, layer of scope.states._layers
            resetHighlight(layer)

          if highlightOsmIds
            for id in highlightOsmIds
              highlightFeature(scope.layerMap[id])


    loadAdminLevel = (osmId, highlightOsmIds) ->

      if not osmId
        return
      console.log("loadState(" + osmId + ")");
      scope.states.setStyle(fadeStyle)
      # target.setStyle(highlightStyle)

      scope.safeApply ->
        $http.get("/adminboundary/geometry/" + osmId + "/").success (data) ->
          if scope.districts
            scope.map.removeLayer(scope.districts)

          scope.districts = L.geoJson(data, { style: visibleStyle, onEachFeature: onEachFeature })
          scope.districts.addTo(scope.map)


          scope.map.fitBounds(scope.districts.getBounds())

          # scope.states.resetStyle(target)
          scope.map.removeLayer(scope.states)

          for id in highlightOsmIds
            highlightFeature(scope.layerMap[id])

    clickFeature = (e) ->
      scope.safeApply ->
        scope.selectedLayer = e.target.feature.properties

    onEachFeature = (feature, layer) ->
      scope.layerMap[feature.properties.osm_id] = layer
      layer.on
        mouseover: (e) ->
          scope.hoveredBoundary = e.target.feature.properties
          scope.safeApply()
        mouseout: (e) ->
          scope.hoveredBoundary = null
          scope.safeApply()

        click: clickFeature

    scope.map = L.map(element.context.id, {scrollWheelZoom: false, zoomControl: false}).setView([0, 1], 4)

    # turn off leaflet credits
    scope.map.attributionControl.setPrefix('')

    # this is our info box floating off in the upper right
    scope.info = L.control()

    if scope.showLabels
      scope.info.onAdd = (map) ->
        @_div = L.DomUtil.create('div', 'info')
        @update()
        return @_div

      scope.info.update = (props) ->
        if props?
          @_div.innerHTML = '<h2>' + props.name + '</h2>'
        else
          @_div.innerHTML = ""

      scope.info.addTo(scope.map)

    $http.get("/adminboundary/geometry/" + scope.osmId + "/").success (data) ->
      scope.states = L.geoJson(data, { style: visibleStyle, onEachFeature: onEachFeature })
      scope.map.fitBounds(scope.states.getBounds())
      scope.states.addTo(scope.map)

  return {
    restrict:"EA",
    scope : {
      osmId:"=osmId",
      hoveredBoundary:"="
      stateBoundary:"="
      wardBoundary:"="
      districtBoundary:"="
      selectedLayer:"="
    }
    transclude: true,
    replace: true,
    link: link
  }
]

#============================================================================
# Our boundary controller
#============================================================================
BoundaryController = ($scope, $http) ->

  $scope.saveButtonName = 'Save Changes'

  $scope.safeApply = (fn) ->
    phase = @$root.$$phase
    if phase is "$apply" or phase is "$digest"
      fn()  if fn and (typeof (fn) is "function")
    else
      @$apply fn

  $scope.safeApply ->
    $http.get("/adminboundary/boundaries/" + $scope.osmId + "/").success (boundaries) ->
      $scope.boundaries = boundaries


  $scope.$watch ->
    $scope.query
  , (query) ->
    if false
      console.log(query)

  # a layer was selected in a map, update current boundary
  $scope.$watch ->
    $scope.selectedLayer
  , (current, previous) ->
    $scope.safeApply ->
      if $scope.boundaries
        if current
          if current.level == 1
            for boundary in $scope.boundaries
              if boundary.osm_id == current.osm_id
                $scope.currentBoundary = boundary

          else if current.level == 2
            for boundary in $scope.boundaries
              for child in boundary.children
                if child.osm_id == current.osm_id
                  $scope.currentBoundary = child

          else if current.level == 3
            for boundary in $scope.boundaries
              for child in boundary.children
                for subChild in child.children
                  if subChild.osm_id == current.osm_id
                    $scope.currentBoundary = subChild

  # when the current boundary changes
  $scope.$watch ->
    $scope.currentBoundary
  , (boundary, previous) ->

    $scope.safeApply ->
      $scope.saveButtonName = 'Save Changes'
      if boundary
        if boundary.level == 1
          $scope.districtBoundary = null
          $scope.wardBoundary = null
          $scope.stateBoundary = boundary
        else if boundary.level == 2
          $scope.wardBoundary = null
          $scope.districtBoundary = boundary
        else if boundary.level == 3
          $scope.wardBoundary = boundary
        $scope.currentAliases = $scope.currentBoundary.aliases
      else
        $scope.stateBoundary = null
        $scope.districtBoundary = null
        $scope.wardBoundary = null

      $scope.aliasForm.$setPristine(true)


  $scope.search = (query) ->
    return (boundary) ->
      if $scope.query and $scope.query.length > 2
        return boundary.name.toLowerCase().indexOf($scope.query.toLowerCase()) > -1
      return true

  $scope.clickBoundary = (state, district, ward) ->
    $scope.safeApply ->
      $scope.stateBoundary = state
      $scope.districtBoundary = district
      $scope.wardBoundary = ward

      if ward
        $scope.currentBoundary = ward
      else if district
        $scope.currentBoundary = district
      else
        $scope.currentBoundary = state

  $scope.enterBoundary = (boundary) ->
    $scope.hoveredBoundary = boundary

  $scope.leaveBoundary = ->
    $scope.hoveredBoundary = null

  $scope.reset = ->
    $scope.safeApply ->
      $scope.query = ''
      $scope.stateBoundary = null
      $scope.districtBoundary = null
      $scope.wardBoundary = null

  $scope.saveAliases = ->
    $scope.safeApply ->
      # cleanse our aliases before saving them
      aliases = $scope.currentAliases.split('\n')
      new_aliases = ''
      delim = ''
      for alias in aliases
        if alias.strip().length > 0
          new_aliases += delim + alias
          delim = '\n'

      $scope.currentBoundary.aliases = new_aliases

      if $scope.currentBoundary.level == 3
        newMatch = ' ' + $scope.currentBoundary.name.toLowerCase() + ' ' + $scope.currentBoundary.aliases.toLowerCase()
        $scope.districtBoundary.match += newMatch
        newMatch = $scope.districtBoundary.name.toLowerCase() + ' ' + $scope.districtBoundary.aliases.toLowerCase() + newMatch
        $scope.stateBoundary.match += newMatch
        $scope.currentBoundary.match = $scope.stateBoundary.name.toLowerCase() + ' ' + $scope.stateBoundary.aliases.toLowerCase() + newMatch
      else if $scope.currentBoundary.level == 2
        # update our querying to include aliases
        newMatch = ' ' + $scope.currentBoundary.name.toLowerCase() + ' ' + $scope.currentBoundary.aliases.toLowerCase()
        if $scope.currentBoundary.children?
          for child in $scope.currentBoundary.children
            child.match += newMatch

        $scope.currentBoundary.match = $scope.stateBoundary.name.toLowerCase() + ' ' + $scope.stateBoundary.aliases.toLowerCase() + newMatch
        $scope.stateBoundary.match += newMatch
        # $scope.currentBoundary = $scope.stateBoundary
      else
        parentMatch = ' ' + $scope.currentBoundary.name.toLowerCase() + ' ' + $scope.currentBoundary.aliases.toLowerCase()
        childMatch = ''
        subChildMatch = ''
        if $scope.currentBoundary.children?
          for child in $scope.currentBoundary.children
            child.match += parentMatch
            childMatch += ' ' + child.name.toLowerCase()
            if child.aliases
              childMatch += ' ' + child.aliases.toLowerCase()

            if child.children
              for subChild in child.children
                subChild.match += childMatch
                subChildMatch += '' + subChild.name.toLowerCase()
                if subChildMatch.aliases
                  subChildMatch += '' + subChild.aliases.toLowerCase()

        $scope.currentBoundary.match = parentMatch + childMatch + subChildMatch
        #$scope.currentBoundary = null

      $scope.aliasForm.$setPristine(true)
      $scope.saveButtonName = 'Saving..'

      $http.post("/adminboundary/boundaries/" + $scope.osmId + "/", JSON.stringify($scope.boundaries)).success (boundaries) ->
        $scope.saveButtonName = 'Saved!'
      .error ->
          $scope.saveButtonName = 'Failed'
          $scope.savingError = true



app.controller "BoundaryController", BoundaryController

