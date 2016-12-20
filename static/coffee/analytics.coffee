
updateFieldOptions = ->
  scope = angular.element($("#scope")).scope()
  window.filtered = []
  for field in field_data
    found = false
    for f in scope.fields
      if f.id == field.id
        found = true
        break
    if not found
      filtered.push(field)

ENTITY_MAP =
  "&": "&amp;"
  "<": "&lt;"
  ">": "&gt;"
  '"': '&quot;'
  "'": '&#39;'
  "/": '&#x2F;'

escapeHtml = (input) ->
  String(input).replace(/[&<>"'\/]/g, (s) -> ENTITY_MAP[s])

#============================================================================
# Select2 Initialization for our AddField dropdown
#============================================================================
$ ->
  # debug field
  #angular.element($("#scope")).scope().addField 160

  scope = angular.element($("#scope")).scope()
  window.field_data = []

  scope.safeApply ->
    if currentReport
      scope.currentReport = currentReport
      scope.showReport(currentReport)

    for group in groups
      scope.addGroup(group)

    flows.sort (a, b) ->
      return b.stats.contacts - a.stats.contacts

    for flow in flows
      scope.addFlow(flow)

      flow_match = ""
      rules = []

      for rule in flow.rules
        rule['type'] = 'rule'
        rule['match'] = flow.text.toLowerCase()
        flow_match += rule.text.toLowerCase() + " "
        rules.push(rule)

      flow['type'] = 'flow'
      flow['match'] = flow_match
      field_data.push(flow)

      for rule in rules
        field_data.push(rule)

    for report in reports
      scope.addReport(report)

    $("#field-selector").select2(
      placeholder: gettext("Add a field")
      data: ->
        if window.filtered
          return { results: window.filtered }
        else
          return { results: field_data }

      dropdownAutoWidth: true

      formatResult: (obj, container, query) ->
        if obj.type == 'flow'
          text = "<div class='field-flow'>" + obj.text + "</div>"
          if obj.rules.length > 1
            text += "<div class='field-count'>" + gettext("Add all ") + obj.rules.length + gettext(" fields") + "</div>"
          return text

        text = "<div class='field-rule'>" + obj.text + "</div>"

      escapeMarkup: (m) -> m
      matcher: (term, text, opt) ->
        matched = text.toLowerCase().indexOf(term.toLowerCase()) >= 0 or opt.match.indexOf(term.toLowerCase()) >= 0
        return matched
    ).on "change", (evt) ->
      selection = evt.added
      if selection.type is "flow"
        scope.addFlowFields(selection)
      else
        scope.addField(selection.id, selection.stats.contacts, selection.text, true)
      $(this).select2 "val", ""

#============================================================================
# Our field controller
#============================================================================
FieldController = ($scope, $http) ->

  $scope.safeApply = (fn) ->
    phase = @$root.$$phase
    if phase is "$apply" or phase is "$digest"
      fn()  if fn and (typeof (fn) is "function")
    else
      @$apply fn

  $scope.chartTypes = [
    type: "bar"
    icon: "icon-bars-3"
  ,
    type: "pie"
    icon: "icon-pie-2"
  ,
    type: "column"
    icon: "icon-bars-3"
  ,
    type: "donut"
    icon: "icon-spinner"
  ]
  $scope.dirty = false
  $scope.fields = []
  $scope.filters = []
  $scope.segments = []
  $scope.reports = []
  $scope.flows = []
  $scope.groups = []
  $scope.currentGroupSegment = null
  $scope.lastGroupSegment = null
  $scope.currentReport = null
  $scope.renameButtonText = "Rename"

  $scope.markDirty = () ->
    $scope.dirty = true
    $scope.renameButtonText = "Save"

  $scope.unmarkDirty = () ->
    $scope.dirty = false
    $scope.renameButtonText = "Rename"

  $scope.addReport = (report) ->
    report.text = report.text.strip()
    $scope.reports.push(report)

  $scope.addFlow = (flow) ->
    flow.text = flow.text.strip()
    $scope.flows.push(flow)

  $scope.addGroup = (group) ->
    group.name = group.name.strip()
    group.isActive = false
    $scope.groups.push(group)

  $scope.addFlowFields = (flow) ->

    idx = 0
    types = ['bar', 'donut', 'column']

    # sort our rules so the ones with more
    # interesting data show up on the top
    flow.rules.sort (a,b) ->
      categories = b.stats.categories - a.stats.categories
      if categories != 0
        return categories
      else
        return b.stats.contacts - a.stats.contacts

    smallCharts = 0
    for rule in flow.rules

      # cycle through our chart types
      chartType = types[idx % types.length]
      chartSize = 1
      showDataTable = false
      showChoropleth = false

      # see if we are forcing this chart to be small
      # regardless of its category count
      if smallCharts > 0
        smallCharts--

      # if it's got a lot of categories, make it a big chart
      # and make sure the next two charts are small
      else if rule.stats.categories >= 3
        chartType = 'column'
        chartSize = 2
        smallCharts = 2

        if idx > 0
          showDataTable = true

      $scope.addField(rule.id, rule.contacts, rule.text, null, chartSize, chartType, showDataTable, showChoropleth, idx * 350)

      idx++

  $scope.addFilter = (field, savedFilter=null) ->

    if savedFilter and savedFilter.isGroupFilter
      $scope.addGroupFilter(savedFilter)
      return

    categories = []
    for category in field.categories
      filterCategory =
        label: category.label
        contacts: category.contacts
        isFilter: true

      if savedFilter
        filterCategory.isFilter = false
        for savedCategory in savedFilter.categories
          if savedCategory.label == filterCategory.label
            filterCategory.isFilter = savedCategory.isFilter

      categories.push(filterCategory)

    # create our new filter object
    filter =
      fieldId: field.id
      isActive: true
      label: field.label
      categories: categories
      isGroupFilter: false
      showAllContacts: false

    if savedFilter
      filter.isActive = savedFilter.isActive
      filter.showAllContacts = savedFilter.showAllContacts

    $scope.filters.push(filter)
    $scope.updateChartTotals()

    if savedFilter
      $scope.unmarkDirty()

  $scope.addGroupFilter = (savedFilter=null) ->
    filterContactGroups = []
    for group in $scope.groups
      filterCategory =
        label: group.name
        id: group.id
        count: group.count
        isFilter: false

      if savedFilter
        filter.isActive = false
        for savedCategory in savedFilter.categories
          if savedCategory.label == filterCategory.label
            filterCategory.isActive = savedCategory.isActive

      filterContactGroups.push(filterCategory)

    groupFilter =
      isActive: true
      label: "Contact Groups"
      categories: filterContactGroups
      isGroupFilter: true
      showAllContacts: true

    if savedFilter
      groupFilter.isActive = savedFilter.isActive
      groupFilter.showAllContacts = savedFilter.showAllContacts

    $scope.filters.push(groupFilter)
    $scope.updateChartTotals()

    if savedFilter
      $scope.unmarkDirty()

  $scope.setFieldData = (data) ->
    for field in $scope.fields
      if field.id == data.id
        for own k,v of data
          field[k] = v

        field.isLoaded = true
        return field

    $scope.fields.push(data)
    return data

  $scope.addField = (id, contacts, label, visible=null, chartSize=2, chartType='bar', showDataTable=false, showChoropleth=false, delay = 0,  savedFilters=null, savedSegments=null) ->
    # if this field already exists, ignore it
    for field in $scope.fields
      if field.id == id
        return

    field =
      isLoaded: false
      isVisible: contacts == 0
      label: label
      id: id

    if visible?
      field.isVisible = visible

    $scope.fields.push(field)

    setTimeout(
      ->
        updateFieldOptions()

        $scope.safeApply ->
          $http.get("/ruleset/results/" + id + "/?_format=json").success (results) ->
            data =
              label: results.label
              id: results.id
              categories: results.results[0].categories
              open_ended: results.results[0].open_ended

            total = 0

            categories_with_contacts = 0
            for category of data.categories
              total += category.count
              if category.count > 0
                categories_with_contacts++

            if data.open_ended
              total = results.results[0].set

            # force single chart data to a bar
            if categories_with_contacts == 1
              chartType = 'bar'

            else if data.categories.length > 20
              chartType = 'donut'

            data.total = total
            data.chartType = chartType
            data.isVisible = true
            if visible?
              data.isVisible = visible

            data.chartSize = chartSize
            data.showDataTable = showDataTable
            data.showChoropleth = showChoropleth
            data.table = null

            data.chart =
              segments:[]
              categories:[]
              chartType:chartType
              total: 0

            data = $scope.setFieldData(data)
            $scope.updateChart(data)

            if showDataTable and data.chart
              data.table = data.chart

            if savedFilters
              for filterConfig in savedFilters
                if filterConfig.fieldId == id
                  $scope.addFilter(field, filterConfig)

            if savedSegments
              for segmentConfig in savedSegments
                if segmentConfig.fieldId == id
                  $scope.addSegment(field, segmentConfig)

      , delay)

  $scope.getField = (id) ->
    for field in $scope.fields
      if field.id == id
        field

  $scope.setChartSize = (field, newSize) ->
    field.chartSize = newSize
    field.chart.chartSize = newSize
    $scope.markDirty()

  $scope.setChartType = (field, newType) ->
    if newType == field.chartType
      field.chartType = 'hidden'
      field.chart.chartType = 'hidden'

      if not field.showDataTable
        $scope.toggleDataTable(field)
    else
      field.chartType = newType
      field.chart.chartType = newType
    $scope.markDirty()

  $scope.remove = (field) ->
    idx = $scope.fields.indexOf(field)
    $scope.fields.splice(idx, 1)
    updateFieldOptions()
    $scope.markDirty()

  $scope.removeFilter = (filter) ->
    idx = $scope.filters.indexOf(filter)
    $scope.filters.splice(idx, 1)

    $scope.updateChartTotals()

  $scope.toggleChoropleth = (field) ->
    field.showChoropleth = !field.showChoropleth

    if field.chartType == 'hidden' and not field.showChoropleth and not field.showDataTable
        $scope.setChartType(field, 'bar')

    $scope.markDirty()

  $scope.toggleDataTable = (field) ->
    field.showDataTable = !field.showDataTable
    if field.showDataTable
      field.table = field.chart
    else
      field.table = null
      if field.chartType == 'hidden' and not field.showChoropleth
        $scope.setChartType(field, 'bar')
    $scope.markDirty()

  $scope.toggleCategorySegment = (evt, segment, categoryLabel) ->
    evt.stopPropagation()

    # set which active count we are so we can label it with
    # the same color as the chart
    colors = Highcharts.getOptions().colors

    activeIdx = 0
    for category in segment.categories
      if category.label == categoryLabel
        category.isSegment = !category.isSegment

      if category.isSegment
        category.chartColor = colors[activeIdx++ % colors.length]
      else
        category.chartColor = null

    # If we have a segment by Contact groups allow only up to
    # two last clicked contact groups to be segmenting the data
    if segment.isGroupSegment
      for category in segment.categories
        if category.label == categoryLabel
          if category != $scope.currentGroupSegment
            if category == $scope.lastGroupSegment
              $scope.lastGroupSegment = null
              category.isSegment = false
            else
              $scope.lastGroupSegment = $scope.currentGroupSegment
              $scope.currentGroupSegment = category
          else
            $scope.currentGroupSegment = $scope.lastGroupSegment
            $scope.lastGroupSegment = null
            category.isSegment = false
        else
          category.isSegment = false

      if $scope.currentGroupSegment
        $scope.currentGroupSegment.isSegment = true
      if $scope.lastGroupSegment
        $scope.lastGroupSegment.isSegment = true

    $scope.updateChartTotals()

  $scope.removeSegment = (segment) ->
    idx = $scope.segments.indexOf(segment)
    $scope.segments.splice(idx, 1)

    $scope.updateChartTotals()

  $scope.addSegment = (field, savedSegment=null) ->
    # disable any other segments
    if !savedSegment
      for segment in $scope.segments
        segment.isSegment = false
    else
      if savedSegment.isGroupSegment
        $scope.addGroupSegment(savedSegment)
        return

    segmentCategories = []

    colors = Highcharts.getOptions().colors
    for category in field.categories
      segmentCategory =
        label: category.label
        isSegment: true
        color: colors[(segmentCategories.length) % colors.length]

      if savedSegment
        for savedCategory in savedSegment.categories
          if segmentCategory.label == savedCategory.label
            segmentCategory.isSegment = savedCategory.isSegment
            segmentCategory.color = savedCategory.color

      segmentCategories.push(segmentCategory)

    # create our new segment object
    newSegment =
      fieldId: field.id
      isSegment: true
      isGroupSegment: false
      label:field.label
      categories: segmentCategories

    if savedSegment
      newSegment.isSegment = savedSegment.isSegment

    $scope.segments.push(newSegment)
    $scope.updateChartTotals()

    if savedSegment
      $scope.unmarkDirty()

  $scope.addGroupSegment = (savedSegment=null) ->
    #disable any other segments
    if !savedSegment
      for segment in $scope.segments
        segment.isSegment = false

    segmentContactGroups = []
    colors = Highcharts.getOptions().colors
    for group in $scope.groups
      segmentCategory =
        label: group.name
        id: group.id
        count: group.count
        isSegment: false
        color: colors[(segmentContactGroups.length) % colors.length]

      if !savedSegment
        if !$scope.lastGroupSegment
          $scope.lastGroupSegment = segmentCategory
          $scope.lastGroupSegment.isSegment = true
        else
          if !$scope.currentGroupSegment and segmentCategory != $scope.lastGroupCategory
            $scope.currentGroupSegment = segmentCategory
            $scope.currentGroupSegment.isSegment = true
      else
        for savedCategory in savedSegment.categories
          if segmentCategory.label == savedCategory.label
            segmentCategory.isSegment = savedCategory.isSegment
            segmentCategory.color = savedCategory.color

      segmentContactGroups.push(segmentCategory)

    groupSegment =
      isSegment: true
      isGroupSegment: true
      label: "Contact Groups"
      categories: segmentContactGroups

    if savedSegment
      groupSegment.isSegment = savedSegment.isSegment

    $scope.segments.push(groupSegment)
    $scope.updateChartTotals()

    if savedSegment
      $scope.unmarkDirty()

  $scope.updateChartTotals = () ->
    for field in $scope.fields
      if field.isLoaded
        $scope.updateChart(field)
    $scope.markDirty()

  $scope.updateChart = (field) ->
    filter_args = []

    for filter in $scope.filters
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

    segment_arg = null
    for segment in $scope.segments
      if segment.isSegment

        # segmenting by one or more groups
        if segment.isGroupSegment
          groups = []
          for group in segment.categories
            if group.isSegment
              groups.push(group.id)

          segment_arg = {groups: groups}


        # segmenting by a ruleset
        else
          segment_arg = {ruleset: segment.fieldId}

          categories = []
          for category in segment.categories
            if category.isSegment
              categories.push(category.label)

              if category.id
                categories.push(category.id)

          segment_arg.categories = categories

        break

    url_params = "filters=" + encodeURIComponent(JSON.stringify(filter_args))
    url_params += "&segment=" + encodeURIComponent(JSON.stringify(segment_arg))

    $http.get("/ruleset/results/" + field.id + "/?" + url_params).success (results) ->
      chart = field.chart
      chart.chartType = field.chartType
      chart.open_ended = results.results[0].open_ended
      chart.total = 0
      chart.segments = []

      for segment in results.results
        segmentCategories = []
        segmentTotal = 0

        for category in segment.categories
          chartCategory =
            label: category.label

          chartCategory.count = category.count
          chart.total += category.count
          segmentTotal += category.count
          if results.results[0].open_ended
            chart.total = results.results[0].set
            segmentTotal = results.results[0].set

          segmentCategories.push(chartCategory)

        chart.segments.push
          label: segment.label
          total: segmentTotal
          categories: segmentCategories

      field.total = chart.total

      if results.results[0].open_ended
        field.total = results.results[0].set

      if field.table
        field.table = chart

  $scope.toggleFilter = (filter) ->
    filter.isActive = !filter.isActive
    $scope.updateChartTotals()

  $scope.toggleSegment = (segment) ->

    if not segment.isSegment
      for seg in $scope.segments
        seg.isSegment = false

    segment.isSegment = !segment.isSegment
    $scope.updateChartTotals()


  $scope.toggleCategoryFilter = (evt, filter, categoryLabel) ->
    evt.stopPropagation()
    activeIdx = 0

    for category in filter.categories
      if category.label == categoryLabel
        category.isFilter = !category.isFilter

    if filter.isGroupFilter
      filter.showAllContacts = false
      $scope.adjustAllContactCheck(filter)

    $scope.updateChartTotals()

  $scope.toggleVisibility = (field) ->
    field.isVisible = not field.isVisible
    $scope.markDirty()

  $scope.adjustAllContactCheck = (filter) ->
    if !filter.isGroupFilter
      return
    check = true
    for category in filter.categories
      check = check && !category.isFilter
    filter.showAllContacts = check

  $scope.activateAllContacts = (evt, filter) ->
    evt.stopPropagation()

    if !filter.isGroupFilter
      return
    filter.showAllContacts = true
    for category in filter.categories
      category.isFilter = false
    $scope.updateChartTotals()

  $scope.showReport = (report) ->
    savedConfig = JSON.parse(report.config)
    $scope.currentReport = report

    $scope.fields = []
    $scope.filters = []
    $scope.segments = []
    $scope.currentGroupSegment = null
    $scope.lastGroupSegment = null

    i = 0
    for field in savedConfig.fields
      $scope.addField(field.id, 1, field.label, field.isVisible, field.chartSize, field.chartType, field.showDataTable, field.showChoropleth, i * 350, savedConfig.filters, savedConfig.segments)
      i++
    $scope.unmarkDirty()


  $scope.goToReadReport = (report) ->
    path = "/report/read/" + report.id + "/"
    window.location.replace(path)

  $scope.showSaveReportModal = (updateReport=null) ->
    modal = new ConfirmationModal($('.creation > .title').html(), $('.creation > .body').html())

    listeners =
      onPrimary: ->
        title = $('#active-modal #id_title').val().strip()
        description = $('#active-modal #id_description').val().strip()

        $('#active-modal #id_title').parent().parent().removeClass "error"
        $('#active-modal #id_description').parent().parent().removeClass "error"

        updateId = null
        if updateReport
          updateId = updateReport.id

        $scope.saveReport(modal, title, description, updateId)

    modal.setListeners(listeners, false)

    modal.setPrimaryButton(gettext('Save Report'))
    modal.show()

    if updateReport
      $('#active-modal #id_title').val(updateReport.text)
      $('#active-modal #id_description').val(updateReport.description)


  $scope.checkForm = (title, description) ->
    noError = true
    if title is "" or title.length > 64
      $('#active-modal #id_title').parent().parent().addClass "error"
      noError = false
    if description is ""
      $('#active-modal #id_description').parent().parent().addClass "error"
      noError = false
    noError

  $scope.saveReport = (modal, title, description, updateId=null) ->
    if !$scope.checkForm(title, description)
      return

    fields = []
    filters = []
    segments = []

    for field in $scope.fields
      fields.push
        chartSize: field.chartSize
        chartType: field.chartType
        isVisible: field.isVisible
        id: field.id
        label: field.label
        showDataTable: field.showDataTable
        showChoropleth: field.showChoropleth

    for filter in $scope.filters
      categories = []
      for category in filter.categories
        categories.push
          isFilter: category.isFilter
          label: category.label

      filterObj =
        fieldId: filter.fieldId
        isActive: filter.isActive
        isGroupFilter: filter.isGroupFilter
        label: filter.label
        showAllcontacts: filter.showAllContacts
        categories: categories

      filters.push(filterObj)

    for segment in $scope.segments
      categories = []
      for category in segment.categories
        categories.push
          isSegment: category.isSegment
          label: category.label
          color: category.color

      segmentObj =
        fieldId: segment.fieldId
        isSegment: segment.isSegment
        isGroupSegment: segment.isGroupSegment
        label: segment.label
        categories: categories

      segments.push(segmentObj)

    config =
      fields: fields
      filters: filters
      segments: segments

    $.post(saveReportURL, JSON.stringify({title: title, description: description, config: config, id: updateId})).done (data) ->
      if data.status == "success"
        modal.dismiss()
        if updateId
          idx = null
          for rep in $scope.reports
            if rep.id == updateId
              idx = $scope.reports.indexOf(rep)
          if idx?
            $scope.reports.splice(idx, 1)
        $scope.reports.push(data.report)
        $scope.currentReport = data.report
        $scope.safeApply()
        $scope.unmarkDirty()


# hook in our Field Controller
app.controller "FieldController", FieldController

#============================================================================
# Directive for showing a chart of the data for this field
#============================================================================

positionDataLabel = (settings) ->
  # loop over every point(bar or column)
  for series in settings.series
    for point in series.data

      if settings.chart.type == 'bar'
        # position label inside the bar by default
        point.dataLabels.x = -15
        point.dataLabels.align = "right"

        if point.y <= 15
          # since the bar is short put the label outside
          point.dataLabels.x = 30
          point.dataLabels.color = "#232"

      if settings.chart.type == 'column'
        # position the label inside the column by default
        point.dataLabels.y = 25

        if point.y <= 15
          # since the column is short put the label outside
          point.dataLabels.y = -10
          point.dataLabels.color = "#232"

  # explicitly return null
  null

app.directive "datatable", ->
  restrict: "E"
  template: "<div></div>"
  scope:
    config: "=config"

  transclude: true
  replace: true
  link: (scope, element, attrs) ->
    # Update when chart data changes
    scope.$watch ( ->
      scope.config
    ), ((config) ->
      if config
        text = "<table class='datatable table'>"

        cat_segment = config.segments[0]

        if config.segments.length > 1
          text += "<tr><td></td>"

          for segment, segment_idx in config.segments
            clazz = 'datatable-segment'
            if segment_idx % 2 == 1
              clazz += ' datatable-segment-odd'

            text += "<td colspan=2 class='" + clazz + "'>" + escapeHtml(segment.label) + "</td>"

          text += "</tr>"

        for category in cat_segment.categories
          text += "<tr><td class='datatable-label'>" + escapeHtml(category.label) + "</td>"

          for segment, segment_idx in config.segments
            clazz = 'datatable-value'
            if segment_idx % 2 == 1
              clazz += ' datatable-segment-odd'

            category_count = 0
            for cat in segment.categories
              if cat.label == category.label
                category_count = cat.count
                break

            text += "<td class='" + clazz + "'>" + category_count + "</td>"

            percent = 0
            if segment.total > 0
              percent = Math.round(category_count * 100 / segment.total)
            text += "<td class='" + clazz + "'>" + percent + "%</td>"

          text += "</tr>"

        text += "</table>"

        element.html(text)
      else
        element.text("")
    ), true

#============================================================================
# Directive for building a highchart off of a config object
#============================================================================
app.directive "chart", ->
  restrict: "E"
  template: "<div></div>"
  scope:
    config: "=config"

  transclude: true
  replace: true
  link: (scope, element, attrs) ->
    chartDefaults =
      chart:
        renderTo: element[0]
        type: attrs.type or null
        width: attrs.width or null
        marginTop: 0
        title: {
          text: null
        }

      legend:
        enabled: false

      plotOptions:
        bar:
          colorByPoint: true
          shadow: false

        column:
          colorByPoint: true
          shadow: false

        pie:
          allowPointSelect: true
          cursor: "pointer"
          size: "70%"
          minSize: "70%"

          dataLabels:
            enabled: true
            color: "#888"
            connectorColor: "#888"
            style:
              textShadow: "none"
            formatter: ->
              if @point.percentage <= 0
                return null
              else
                return "<b>" + @point.name + "</b> " + Math.round(@point.percentage) + "%"

      yAxis:
        min: 0
        max: 100
        allowDecimals: false
        labels:
          enabled: false

        title:
          text: null

      credits:
        enabled: false

      title:
        text: null

      labels:
        items: []

      tooltip:
        formatter: ->
          "<b>" + escapeHtml(@x) + "</b> - " + @point.count + gettext(" of ") + @point.total + gettext(" responses")

      xAxis:
        labels:
          style:
            fontWeight: "200"

        categories: []

    # Update when chart data changes
    scope.$watch ( ->
      scope.config
    ), ((config) ->
      return unless config

      if config.chartType == 'hidden'
        element.hide()
      else
        element.show()

      # make a copy of our settings
      deepCopy = true
      newSettings = {}
      $.extend deepCopy, newSettings, chartDefaults

      chartType = config.chartType
      open_ended = config.open_ended
      newSettings.chart.type = chartType
      newSettings.series = []
      series = newSettings.series

      chartHeight = 125

      if config.segments.length > 1
        newSettings.plotOptions.bar.colorByPoint = false
        newSettings.plotOptions.column.colorByPoint = false

        newSettings.tooltip.formatter = ->
          return "<b>" + escapeHtml(@series.name) + " - " + @x + "</b> - " + @point.count + gettext(" of ") + @point.total + gettext(" responses")

      for segment, segment_idx in config.segments
        # donuts are the same as pies but they get an inner size
        data = []
        categories = []
        if chartType is "pie" or chartType is "donut"
          chartHeight = 300
          i = 0

          while i < segment.categories.length
            category = segment.categories[i]
            data.push [escapeHtml(category.label), category.count]
            i++
          pieSeries =
            name: escapeHtml(segment.label)
            data: data

          series.push pieSeries

          if chartType is "donut"
            newSettings.chart.type = "pie"
            pieSeries.innerSize = "35%"

          paneWidth = 100 / (config.segments.length * 2)
          pieSeries.center = [paneWidth + 2 * paneWidth * segment_idx + "%", "50%"]

          if config.segments.length == 1
            newSettings.tooltip.formatter = ->
              "<b>" + @key + "</b> - " + @y + gettext(" of ") + @point.total + gettext(" responses")
          else
            newSettings.tooltip.formatter = ->
              return "<b>" + @series.name + " - " + @key + "</b> - " + @y + gettext(" of ") + @point.total + gettext(" responses")

          if open_ended
            if config.segments.length == 1
              newSettings.tooltip.formatter = ->
                "<b>" + @key + "</b> - " + gettext(" mentioned ") + @y + gettext(" times in all responses ")
            else
              newSettings.tooltip.formatter = ->
                return "<b>" + @series.name + " - " + @key + "</b> - " + gettext(" mentioned ") + @y + gettext(" times in all responses ")


        else if chartType is "bar" or chartType is "column"
          i = 0

          while i < segment.categories.length
            pointLabels = {}
            pointLabels.enabled = true
            pointLabels.color = "#fff"
            pointLabels.x = 0
            pointLabels.y = 0
            pointLabels.format = "{point.y}%"
        
            category = segment.categories[i]
            percent = 0
            percent = parseInt(category.count * 100 / segment.total)  if segment.total > 0

            data.push
              y: percent
              count: category.count
              total: segment.total
              label: category.label
              dataLabels: pointLabels

            categories.push category.label
            i++

          if chartType is "bar"
            chartHeight = segment.categories.length * 80 * config.segments.length

            # half size charts need a min height so they play nice
            if config.chartSize == 1
              chartHeight = 300

            newSettings.chart.marginLeft = 150
            series.push
              name: segment.label
              data: data

          else
            chartHeight = 300
            series.push
              name: segment.label
              data: data

          newSettings.xAxis.categories = categories

      newSettings.chart.height = chartHeight

      # build our series from our categories
      chart = new Highcharts.Chart(newSettings, positionDataLabel(newSettings))
    ), true
