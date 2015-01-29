app = angular.module('temba.directives', ['temba.services'])

#============================================================================
# All nodes in the flow, directive to manage drag and drop
#============================================================================
app.directive "node",[ "Plumb", "Flow", "DragHelper", "utils", "$timeout", "$log", (Plumb, Flow, DragHelper, utils, $timeout, $log)->

  link = (scope, element, attrs) ->

    if window.mutable
      # jsplumb can drop on to us
      Plumb.makeTarget(element, attrs.dropScope)

      jsPlumb.draggable element,
        containment: "parent",
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
          else

            scope.node.x = element[0].offsetLeft
            scope.node.y = element[0].offsetTop

            Flow.determineFlowStart()

            # reset our dragging flag after our current event loop
            $timeout ->
              window.dragging = false
              Flow.markDirty()
            , 0

          Plumb.repaint()

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

    Flow.checkTerminal(scope.actionset)

    scope.$evalAsync ->
      Plumb.updateConnection(scope.actionset)

    scope.addAction = ->

    scope.$watch (->scope.actionset._terminal), (terminal) ->

      terminal = false if not terminal?

      source = $('#' + scope.actionset.uuid + ' .source')
      jsPlumb.setSourceEnabled(source, not terminal)

      if terminal
        source.addClass('terminal')
        jsPlumb.detachAllConnections(source)
      else
        source.removeClass('terminal')

  return {
    link: link
    scope:
      actionset: '='
  }
]

# update translations when actions or language
app.directive "action", [ "Plumb", "Flow", (Plumb, Flow) ->
  link = (scope, element, attrs) ->

    scope.updateTranslationStatus = (action, baseLanguage, currentLanguage) ->
      action._missingTranslation = false
      # grab the appropriate translated version

      if scope.$root.flow.base_language
        if action.type in ['send', 'reply', 'say']
          action._translation = action.msg[currentLanguage.iso_code]

          # translated recording for IVR
          if action.recording
            action._translation_recording = action.recording[currentLanguage.iso_code]
            if action._translation_recording
              action._translation_recording = window.recordingURL + action._translation_recording


          if action._translation is undefined
            action._translation = action.msg[baseLanguage]
            action._missingTranslation = true
          else
            action._missingTranslation = false
        else
          action._translation = null
          action._missingTranslation = false
      else
        action._translation = action.msg
        action._translation_recording = action.recording

        if action._translation_recording
          action._translation_recording = window.recordingURL + action._translation_recording


      Plumb.repaint(element.parents('.node').find('.source'))

    scope.$watch (->scope.action.dirty), (current) ->
      if current
        scope.action.dirty = false
        scope.updateTranslationStatus(scope.action, scope.$root.flow.base_language, scope.$root.language)

    scope.$watch (->scope.action), ->
        scope.updateTranslationStatus(scope.action, scope.$root.flow.base_language, scope.$root.language)

    scope.$watch (->scope.$root.language), ->
      scope.updateTranslationStatus(scope.action, scope.$root.flow.base_language, scope.$root.language)

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

#============================================================================
# Directives for rules
#============================================================================
app.directive "ruleset", [ "Plumb", "Flow", "$log", (Plumb, Flow, $log) ->
  link = (scope, element, attrs) ->

    Flow.replaceRuleset(scope.ruleset, false)

    scope.updateTranslationStatus = (ruleset, baseLanguage, currentLanguage) ->
      for category in ruleset._categories

        category._missingTranslation = false
        if category.name
          if scope.$root.flow.base_language
            category._translation = category.name[currentLanguage.iso_code]

            if category._translation is undefined
              category._translation = category.name[baseLanguage]
              category._missingTranslation = true
            else
              category._missingTranslation = false

          else
            category._translation = category.name

      Plumb.repaint(element)

    scope.$watch (->scope.ruleset), ->
      scope.updateTranslationStatus(scope.ruleset, scope.$root.flow.base_language, scope.$root.language)
      scope.$evalAsync ->
        Plumb.updateConnections(scope.ruleset)

    scope.$watch (->scope.$root.language), ->
      scope.updateTranslationStatus(scope.ruleset, scope.$root.flow.base_language, scope.$root.language)

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
    if window.mutable
      Plumb.makeSource(element, attrs.dropScope)

      # don't allow connections to be dragged from connected sources
      if scope.action_set
        scope.$watch (->scope.action_set.destination), (destination) ->
          scope.$evalAsync ->
            Plumb.setSourceEnabled(element, !destination?)

      else if scope.category
        scope.$watch (->scope.category.target), (target) ->
          scope.$evalAsync ->
            Plumb.setSourceEnabled(element, !target?)

  return {
    link: link
    restrict: 'C'
  }
]

