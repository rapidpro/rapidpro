
visibleStyle = (feature) ->
  fillColor: feature.properties.color
  weight: 1
  opacity: 1
  color: 'white'
  fillOpacity: 1

highlightStyle = (feature) ->
  fillColor: feature.properties.color
  opacity: 1
  weight: 5
  color: feature.properties.borderColor
  fillOpacity: 1

calculateColor = (breaks, scores) ->
  score = scores.score
  if scores.count == 0
    return 'rgb(200, 200, 200)'

  colors = ['rgb(165,0,38)','rgb(215,48,39)','rgb(244,109,67)','rgb(253,174,97)','rgb(254,224,139)','rgb(255,255,191)','rgb(217,239,139)','rgb(166,217,106)','rgb(102,189,99)','rgb(26,152,80)','rgb(0,104,55)']
  for i in [0..breaks.length]
    if score <= breaks[i]
      return colors[i]

#============================================================================
# Directive for building a leaflet off of a config object
#============================================================================
app.directive "choropleth", ["$http", "$log", ($http, $log) ->
  link = (scope, element, attrs) ->

    scope.legend = null
    scope.map = null
    scope.info = null
    scope.districtId = null

    scope.safeApply = (fn) ->
      phase = @$root.$$phase
      if phase is "$apply" or phase is "$digest"
        fn()  if fn and (typeof (fn) is "function")
      else
        @$apply fn

    scope.$watchCollection ->
      [scope.chartSize, scope.showChoropleth]
    , (newSize, oldSize) ->
      scope.map.invalidateSize()

    scope.$watch ->
      scope.$parent.filters
    , (oldFilters, newFilters) ->
      if scope.features
        scope.map.removeLayer(scope.features)

      scope.loadFeature(scope.ruleId, scope.osmId, true)
    , true

    scope.map = L.map(element.context.id, {scrollWheelZoom: false, zoomControl: false, touchZoom: false, trackResize: true,  dragging: false})
    scope.map.setView([0, 1], 4)

    # turn off leaflet credits
    scope.map.attributionControl.setPrefix('')

    scope.map.on 'resize', (e) ->
      if scope.states
        scope.map.fitBounds(scope.states.getBounds())

    # this is our info box floating off in the upper right
    scope.info = L.control()

    scope.colors = ['rgb(165,0,38)','rgb(215,48,39)','rgb(244,109,67)','rgb(253,174,97)','rgb(254,224,139)','rgb(255,255,191)','rgb(217,239,139)','rgb(166,217,106)','rgb(102,189,99)','rgb(26,152,80)','rgb(0,104,55)']

    scope.info.onAdd = (map) ->
      @_div = L.DomUtil.create('div', 'info')
      @update()
      return @_div

    scope.info.update = (scores) ->
      if scores?
        html = '<div class="summary">'
        html += '<div class="title">' + scores.name + '</div>'
        html += '<div class="total">' + scores.count + ' responses</div>'
        html += '<div class="categories">'
        for result, idx in scores.results
          html += '<div class="category category-' + idx + '">'
          html += '<span class="pct">' + result.percentage + '<span class="unit">%</unit></span><span class="count">' + result.count  + '</span><span class="label">' + result.label + '</span>'
          html += '</div>'
        html += '</div>'
        html += '</div>'

        @_div.innerHTML = html
      else
        @_div.innerHTML = ""

    scope.info.addTo(scope.map)

    showStates = ->
      if scope.features
        scope.map.removeLayer(scope.features)

      scope.features = scope.states
      scope.map.fitBounds(scope.features.getBounds())
      scope.features.addTo(scope.map)

    clickFeature = (e) ->
      if e.target.feature
        if e.target.feature.zoomable
          # reset the highlight style
          scope.features.resetStyle(e.target)

          geojson = L.geoJson(e.target.toGeoJSON())
          scope.map.fitBounds(geojson.getBounds())
          scope.loadFeature(scope.ruleId, e.target.feature.properties.osm_id)
        else
          scope.totals = scope.state_totals
          showStates()

    onEachFeature = (feature, layer) ->
      layer.on
        mouseover: (e) ->
          highlightFeature(e.target)
        mouseout: (e) ->
          resetHighlight(e.target)
        click: clickFeature

    highlightFeature = (layer) ->
      if layer
        scope.info.update(layer.feature.properties.scores)
        layer.setStyle(highlightStyle(layer.feature))
        if !L.Browser.ie && !L.Browser.opera
          layer.bringToFront()

    resetHighlight = (layer) ->
      scope.info.update(scope.totals)

      if layer
        scope.features.resetStyle(layer)

    updateLegend = (map) ->
      div = L.DomUtil.create("div", "info legend")
      if scope.legend and scope.scores and scope.breaks and scope.categories
        # loop through our density intervals and generate a label with a colored square for each interval
        i = 0

        while i < scope.breaks.length
          idx = scope.breaks.length - i - 1

          lower = if idx > 0 then scope.breaks[idx-1] else 0
          upper = scope.breaks[idx]

          if lower < .5 and upper < .5
            category = scope.categories[1]
            upper = Math.round((1 - upper) * 100)

            div.innerHTML += "<i style=\"background:" + scope.colors[idx] + "\"></i> " + upper + "% " + category + "<br/>"

          else if lower > .5 and upper > .5
            category = scope.categories[0]
            lower = Math.round(lower * 100)

            div.innerHTML += "<i style=\"background:" + scope.colors[idx] + "\"></i> " + lower + "% " + category + "<br/>"
          else
            div.innerHTML += "<i style=\"background:" + scope.colors[i] + "\"></i>Even<br/>"
          i++

      return div

    scope.loadFeature = (ruleId, osmId, states=false) ->

      filter_args = []

      for filter in scope.$parent.filters
        # group filter
        if filter.isGroupFilter
          groups = []
          for group in filter.categories
            if group.isFilter
              groups.push(group.id)

          filter_arg = {groups: groups}

        # ruleset filter
        else
          filter_arg = {ruleset: filter.fieldId}
          categories = []
          for category in filter.categories
            if category.isFilter
              categories.push(category.label)

          filter_arg.categories = categories

        filter_args.push(filter_arg)

      url_params = "filters=" + encodeURIComponent(JSON.stringify(filter_args))

      $http.get('/ruleset/choropleth/' + ruleId + '/?_format=json&boundary=' + osmId + '&' + url_params).success (scoreData) ->

        scope.totals = scoreData.totals
        scope.scores = scoreData.scores
        scope.breaks = scoreData.breaks
        scope.categories = scoreData.categories

        scope.info.update(scope.totals)

        if states
          scope.state_scores = scope.scores
          scope.state_totals = scope.totals

        if not scope.legend
          scope.legend = L.control(position: "bottomleft")
          scope.legend.onAdd = updateLegend
          scope.legend.addTo(scope.map)

        $http.get("/adminboundary/geometry/" + osmId + "/").success (geoData) ->
          # add all our scores to our properties
          for feature in geoData.features
            feature.properties.scores = scoreData.scores[feature.properties.osm_id]
            feature.properties.color = calculateColor(scope.breaks, feature.properties.scores)
            feature.properties.borderColor = 'white'

          # remove any existing features
          if scope.features
            scope.map.removeLayer(scope.features)

          # add these features to our map
          scope.features = L.geoJson(geoData, { style: visibleStyle, onEachFeature: onEachFeature })
          scope.map.fitBounds(scope.features.getBounds())
          scope.features.addTo(scope.map)

          if states
            scope.states = scope.features

    scope.loadFeature(scope.ruleId, scope.osmId, true)

  return {
    restrict:"EA",
    scope : {
      osmId:"=osmId",
      ruleId:"=ruleId",
      chartSize:"=chartSize",
      showChoropleth:"=showChoropleth"
    }
    transclude: true,
    replace: true,
    link: link
  }
]
