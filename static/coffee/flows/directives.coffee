app = angular.module('temba.directives', ['temba.services'])

#============================================================================
# All nodes in the flow, directive to manage drag and drop
#============================================================================
app.directive "node",[ "Plumb", "Flow", "DragHelper", "utils", "$timeout", "$log", (Plumb, Flow, DragHelper, utils, $timeout, $log)->

  link = (scope, element, attrs) ->

    if window.mutable

      # jsplumb can drop on to us
      Plumb.makeTarget(attrs.id, attrs.dropScope)

      $timeout ->
        jsPlumb.draggable attrs.id,
          containment: true,
          cancel: '.source',
          start: ->
            DragHelper.hide()
            element.data('previous', element.offset())
            element.parents('#flow').addClass('dragging')
            element.addClass('dragging')
            window.dragging = true
          drag: ->

            utils.checkCollisions(element)

            # make sure our connections drag with us
            $(this).find("._jsPlumb_endpoint_anchor_").each (i, e) ->
              if $(e).hasClass("connect")
                Plumb.repaint $(e).parent()
              else
                Plumb.repaint $(e)
              return

          stop: ->
            element.parents('#flow').removeClass('dragging')
            element.removeClass('dragging')

            if element.hasClass('collision') and element.data('previous')
              element.offset(element.data('previous'))
              element.data('previous', null)
              element.removeClass('collision')
              Plumb.repaint()

            else

              scope.node.x = element[0].offsetLeft
              scope.node.y = element[0].offsetTop

              Flow.determineFlowStart()
              Plumb.setPageHeight()

              # reset our dragging flag after our current event loop
              $timeout ->
                window.dragging = false
                Flow.markDirty()
              , 0
      ,0

      scope.$on '$destroy', ->
        Plumb.removeElement(scope.node.uuid)

  return {
    restrict: "A"
    scope: {
      node: '='
    }
    link:link
  }
]

#============================================================================
# Directive for sticky notes
#============================================================================
# manage connections when actionset changes
app.directive "note", [ "$timeout", "$log", "Flow", ($timeout, $log, Flow) ->
  link = (scope, element, attrs) ->

    element.css('left', scope.note.x + 'px').css('top', scope.note.y + 'px')
    element.draggable
      containment: 'parent',
      stop: ->
        scope.note.x = element[0].offsetLeft
        scope.note.y = element[0].offsetTop
        $timeout ->
          Flow.markDirty()
        ,0

    scope.$watch 'note.title', (current, prev) ->
      if current != prev
        Flow.markDirty()

    scope.$watch 'note.body', (current, prev) ->
      if current != prev
        Flow.markDirty()

  return {
    restrict: "A"
    scope: {
      note: '='
    }
    link:link
  }
]
#============================================================================
# Directives for actions
#============================================================================
# manage connections when actionset changes
app.directive "actionset", [ "$timeout", "$log", "Plumb", "Flow", ($timeout, $log, Plumb, Flow) ->

  link = (scope, element, attrs) ->

    Plumb.updateConnection(scope.actionset)
    Flow.checkTerminal(scope.actionset)

    scope.addAction = ->

    scope.$watch (->scope.actionset._terminal), (terminal) ->

      terminal = false if not terminal?

      source = $('#' + scope.actionset.uuid + ' .source')

      if terminal
        source.addClass('terminal')
        Flow.updateDestination(scope.actionset.uuid, null)
      else
        source.removeClass('terminal')

      $timeout ->
        jsPlumb.setSourceEnabled(scope.actionset.uuid + '_source', not terminal)
      ,0

  return {
    link: link
    scope:
      actionset: '='
  }
]

# update translations when actions or language
app.directive "action", [ "Plumb", "Flow", "$log", (Plumb, Flow, $log) ->
  link = (scope, element, attrs) ->

    scope.updateTranslationStatus = (action, baseLanguage, currentLanguage) ->

      action._missingTranslation = false

      # grab the appropriate translated version
      iso_code = Flow.flow.base_language
      if currentLanguage
        iso_code = currentLanguage.iso_code

      if action.type in ['send', 'reply', 'say', 'end_ussd']
        action._translation = action.msg[iso_code]

        # translated recording for IVR
        if action.recording
          action._translation_recording = action.recording[iso_code]
          if action._translation_recording
            action._translation_recording = window.mediaURL + action._translation_recording

        # break out our media if we have some
        action._media = null
        action._attachURL = null

        if action.media and action.media[iso_code]
          parts = action.media[iso_code].split(/:(.+)/)

          if parts.length >= 2
            mime_parts = parts[0].split('/')
            if mime_parts.length > 1
              action._media =
                mime: parts[0]
                url:  window.mediaURL + parts[1]
                type: mime_parts[0]
            else
              action._attachURL = parts[1]
              action._attachType = mime_parts[0]

        if action._translation is undefined
          action._translation = action.msg[baseLanguage]
          action._missingTranslation = true
        else
          action._missingTranslation = false
      else
        action._translation = null
        action._missingTranslation = false

      Flow.updateTranslationStats()

      Plumb.repaint(element.parents('.node').find('.source'))

    scope.$watch (->scope.action.dirty), (current) ->
      if current
        scope.action.dirty = false
        scope.updateTranslationStatus(scope.action, Flow.flow.base_language, Flow.language)

    scope.$watch (->scope.action), ->
        scope.updateTranslationStatus(scope.action, Flow.flow.base_language, Flow.language)

    scope.$watch (->Flow.language), ->
      scope.updateTranslationStatus(scope.action, Flow.flow.base_language, Flow.language)


  return {
    restrict: "A"
    link: link
    scope:
      action: '=action'
  }
]

# display the name of the action with an optional icon
app.directive "actionName", [ "Flow", (Flow) ->
  link = (scope, element, attrs) ->
    scope.$watch (->scope.ngModel), ->
      if scope.ngModel
        actionConfig = Flow.getActionConfig(scope.ngModel)
        scope.name = actionConfig.name
        if attrs['icon'] == "show"
          scope.icon = actionConfig.icon
  return {
    template: '<span class="icon [[icon]]"></span><span>[[name]]</span>'
    restrict: "C"
    link: link
    scope: {
      ngModel: '='
    }
  }
]

# display the name of the action with an optional icon
app.directive "rulesetName", [ "Flow", (Flow) ->
  link = (scope, element, attrs) ->
    scope.$watch (->scope.ngModel), ->
      if scope.ngModel
        rulesetConfig = Flow.getRulesetConfig(scope.ngModel)
        scope.name = rulesetConfig.name
        if attrs['icon'] == "show"
          scope.icon = rulesetConfig.icon
  return {
    template: '<span class="icon [[icon]]"></span><span>[[name]]</span>'
    restrict: "C"
    link: link
    scope: {
      ngModel: '='
    }
  }
]

#============================================================================
# Directives for rules
#============================================================================
app.directive "ruleset", [ "Plumb", "Flow", "$log", (Plumb, Flow, $log) ->
  link = (scope, element, attrs) ->

    # this derives our categories
    Flow.replaceRuleset(scope.ruleset, false)

    scope.updateTranslationStatus = (ruleset, baseLanguage, currentLanguage) ->

      iso_code = baseLanguage
      if currentLanguage
        iso_code = currentLanguage.iso_code

      for category in ruleset._categories

        category._missingTranslation = false
        if category.name
          if baseLanguage
            category._translation = category.name[iso_code]

            if category._translation is undefined
              category._translation = category.name[baseLanguage]
              category._missingTranslation = true
            else
              category._missingTranslation = false

          else
            category._translation = category.name

      # USSD translations
      if Flow.flow.flow_type == 'U'

        # USSD message translation
        ruleset.config._ussd_translation = ruleset.config.ussd_message[iso_code]
        if ruleset.config._ussd_translation is undefined or ruleset.config._ussd_translation == ""
          ruleset.config._ussd_translation = ruleset.config.ussd_message[baseLanguage]
          ruleset.config._missingTranslation = true
        else
          ruleset.config._missingTranslation = false

        # USSD menu translation
        if ruleset.ruleset_type == "wait_menu"
          for item in ruleset.rules
            item._missingTranslation = false
            if item.label
              item._translation = item.label[iso_code]
              if item._translation is undefined or item._translation == ""
                item._translation = item.label[baseLanguage]
                item._missingTranslation = true

      Flow.updateTranslationStats()
      Plumb.repaint(element)

    scope.$watch (->scope.ruleset), ->
      scope.updateTranslationStatus(scope.ruleset, Flow.flow.base_language, Flow.language)
      Plumb.updateConnections(scope.ruleset)

    scope.$watch (->Flow.language), ->
      scope.updateTranslationStatus(scope.ruleset, Flow.flow.base_language, Flow.language)

  return {
    restrict: "A"
    link: link
    scope:
      ruleset: '=ruleset'
  }
]

# display the verbose name for the operator
app.directive "operatorName", [ "Flow", (Flow) ->
  link = (scope, element, attrs) ->
    scope.$watch (->scope.ngModel), ->
      opConfig = Flow.getOperatorConfig(scope.ngModel.type)
      scope.verbose_name = opConfig.verbose_name

  return {
    template: '<span>[[verbose_name]]</span>'
    restrict: "C"
    link: link
    scope: {
      ngModel: '='
    }
  }
]

# turn an element into a jsplumb source
app.directive "source", [ 'Plumb', '$log', (Plumb, $log) ->
  link = (scope, element, attrs) ->
    if not attrs.id or not attrs.dropScope then return

    if window.mutable
      Plumb.makeSource(attrs.id, attrs.dropScope)

    scope.$on '$destroy', ->
      if jsPlumb.isSource(attrs.id)
        Plumb.removeElement(attrs.id)

  return {
    link: link
    restrict: 'C'
  }
]
