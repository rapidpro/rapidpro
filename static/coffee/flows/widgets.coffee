app = angular.module('temba.widgets', [])

#============================================================================
# Displaying USSD Menu with textarea, menu inputs and char counter
#============================================================================
app.directive "ussd", [ "$rootScope", "$log", "Flow", "utils", ($rootScope, $log, Flow, utils) ->
  MESSAGE_LENGTH = 182

  link = (scope, element, attrs) ->
    scope.USSD_MENU = parseInt(attrs.ussd) == 0
    scope.USSD_RESPONSE = parseInt(attrs.ussd) == 1

    scope.ruleset.ruleset_type = if scope.USSD_MENU then "wait_menu" else "wait_ussd"

    scope.ruleset.config ?= {}
    scope.ruleset.config.ussd_message ?= {}
    scope.ruleset.config.ussd_message[Flow.flow.base_language] ?= ""

    menu = null

    do refreshMenu = ->
      menu = scope.ruleset.rules.filter (rule) -> rule._config.show

    updateCategory = (item) ->
      if not item.category._autoName
        return

      words = item.label[Flow.flow.base_language].trim().split(/\b/)
      if words
        categoryName = words[0].toUpperCase()
        item.category._base = categoryName
        if categoryName.length > 1
          item.category._base = categoryName.charAt(0) + categoryName.substr(1).toLowerCase()

    do insertEmpty = ->
      refreshMenu()
      if scope.USSD_MENU
        # when we switch back from "wait_ussd", filter out arbitrary rules
        if (scope.ruleset.rules.filter (rule) -> not rule.label and rule._config.show)
          scope.ruleset.rules = scope.ruleset.rules.filter (rule) -> rule.label or (not rule.label and not rule._config.show)
          refreshMenu()

        if menu.length == 0 or menu[menu.length - 1].category?._base != ""
          scope.ruleset.rules.splice menu.length, 0,
            _config: Flow.getOperatorConfig("eq")
            uuid: uuid()
            label: {}
            test:
              type: 'eq'
              _base: if menu.length >= 9 then 0 else menu.length + 1
            category:
              _autoName: true
              _base: ""
      else
        if menu[menu.length - 1]?.category?._base == ""
          scope.ruleset.rules.splice(menu.length - 1, 1)

    scope.remove = (item, index) ->
      refreshMenu()
      scope.ruleset.rules.splice(index, 1)
      if index == 0 or index == menu.length - 1
        insertEmpty()

    scope.updateMenu = (item, index) ->
      refreshMenu()
      scope.countCharacters()
      updateCategory(item)
      if item.label[Flow.flow.base_language].length == 1 and index == menu.length - 1
        insertEmpty()

    do scope.countCharacters = ->
      sumMenuItems = (items) ->
        items
          .filter (rule) ->
            rule._config.show
          .reduce (prev, current) ->
            current.label[Flow.flow.base_language] ?= ""
            prev + current.test._base.toString().length + current.label[Flow.flow.base_language].length + 2 # 1 for space 1 for newline char
          ,0

      textLength = scope.ruleset.config.ussd_message[Flow.flow.base_language].length
      textMenuLength = (textLength + 1 + sumMenuItems(scope.ruleset.rules)) if scope.USSD_MENU
      $rootScope.characters = if scope.USSD_MENU then MESSAGE_LENGTH - textMenuLength else MESSAGE_LENGTH - textLength

  return {
    templateUrl: "/partials/ussd_directive"
    restrict: "A"
    link: link
    scope: true
  }
]

#============================================================================
# Simple directive for displaying a localized textarea with a char counter
#============================================================================
app.directive "sms", [ "$log", "Flow", ($log, Flow) ->
  link = (scope, element, attrs) ->

    scope.showCounter = true
    if attrs.showCounter?
      scope.showCounter = eval(attrs.showCounter)

    # find out how many sms messages this will be
    scope.countCharacters = ->
      if scope.message
        length = scope.message.length
        scope.messages = Math.ceil(length/160)
        scope.characters = scope.messages * 160 - length
      else
        scope.messages = 0
        scope.characters = 160

    # update our counter everytime the message changes
    scope.$watch (->scope.message), scope.countCharacters

    # determine the initial message based on the current language
    if scope.sms
      scope.message = scope.sms[Flow.flow.base_language]
      if not scope.message
        scope.message = ""

  return {
    templateUrl: "/partials/sms_directive"
    restrict: "A"
    link: link
    scope: {
      sms: '='
      message: '='
    }
  }
]

# auto completion widget
app.directive "autoComplete", ["$rootScope", "$timeout", "$http", "$log", "Flow", ($rootScope, $timeout, $http, $log, Flow) ->

  link = (scope, element, attrs)  ->
    new AutoComplete(Flow.completions, Flow.function_completions).bind(element)
  return {
    restrict: 'A'
    link: link
  }
]



makeSelect2Required = (scope, field, element) ->

  select2 = element.data('select2')
  data = select2.data()
  if data and not Array.isArray(data)
    data = [ data ]
  field['selected'] = data

  element.on 'change', (e) ->
    data = select2.data()
    if data and not Array.isArray(data)
      data = [ data ]

    field['selected'] = data
    scope.$evalAsync ->
      if field['selected'] and field['selected'].length > 0
        field.$setValidity("required", true)
      else
        field.$setValidity("required", false)
        return


# Ajax backed select2 widget
app.directive "selectServer", ["$timeout", "$http", ($timeout, $http) ->
  link = (scope, element, attrs, form)  ->

    # should we allow search
    minimumResultsForSearch = -1
    if attrs.search
      minimumResultsForSearch = 0

    element.select2
      placeholder: attrs.placeholder
      minimumResultsForSearch: minimumResultsForSearch
      ajax:
        url: attrs.selectServer
        dataType: "json"
        data: (term, page) ->
          search: term
          page: page
        results: (response, page, context) ->
          response

      escapeMarkup: (m) ->
        m

    if attrs.initId and attrs.initText
      element.data('select2').data({id:attrs.initId, text:attrs.initText})

    if attrs.required
      makeSelect2Required(scope, form[attrs['name']], element)

    $timeout ->
      element.trigger('change')
    , 0
  return {
    restrict: 'A'
    require: '^form'
    link: link
  }
]

# Vanilla conversion of a select box into select2
app.directive "select2", ["$timeout", ($timeout) ->
  link = (scope, element, attrs)  ->
    element.select2
      minimumResultsForSearch: -1
      placeholder: attrs.placeholder

    # trigger a change to show initial selection
    $timeout ->
      element.trigger('change')
    , 0

  return {
    restrict: 'AC'
    link: link
  }
]

app.directive "selectLabel", ["$timeout", "Flow", ($timeout, Flow) ->
  link = (scope, element, attrs, form) ->

    element.select2
      tags: Flow.labels
      multiple: true

    field = form[attrs['name']]
    select2 = element.data('select2')

    if scope.ngModel
      initLabels = []
      for label in scope.ngModel
        initLabels.push
          id: label.uuid
          text: label.name

      select2.data(initLabels)

    field['selected'] = select2.data()

    # select2 won't let us attach to search keypress so
    # we hook into the event right before the change is made
    element.on 'select2-selecting', (e) ->
      if e.val.length < 1
        e.preventDefault()
        return

      # labels can't start with @, strip it off if we get this far
      if e.val[0] == '@'
        element.select2("search", e.val.slice(1))
        e.preventDefault()

    element.on 'change', (e) ->
      field['selected'] = select2.data()
      if attrs.required
        if not field['selected'] or field['selected'].length == 0
          select2.container.find('.select2-choices').addClass('select2-required')
          scope.$apply ->
            field.$setValidity("required", false)
        else
          select2.container.find('.select2-choices').removeClass('select2-required')
          scope.$apply ->
            field.$setValidity("required", true)

    # trigger a change to show initial selection
    $timeout ->
      element.trigger('change')
    , 0

  return {
    require: '^form'
    restrict: 'A'
    link: link
    scope:
      ngModel: '='
  }
]

# Vanilla conversion of a select box into select2
app.directive "selectEmail", ["$timeout", ($timeout) ->
  link = (scope, element, attrs, form)  ->

    if scope.ngModel
      element.val(scope.ngModel.join())

    element.select2
      tags: []
      multiple: true
      selectOnBlur: true
      minimumInputLength: 1
      minimumResultsForSearch: -1

      formatInputTooShort: (term, minLength) ->
        return ""

      matcher: (term, text, opt) ->
        return text.toUpperCase().indexOf(term.toUpperCase()) == 0

      formatNoMatches: (term) ->
        return gettext("Enter a valid e-mail address or field")

      createSearchChoice: (term, data) ->
        if $(data).filter( -> @text.localeCompare(term) is 0).length is 0
          if /^@[a-zA-Z._]+|^[^@]+@([^@\.]+\.)+[^@\.]+$/.test(term)
            id: term
            text: term
          else
            null

    if attrs.required
      makeSelect2Required(scope, form[attrs['name']], element)

    # trigger a change to show initial selection
    $timeout ->
      element.trigger('change')
    , 0

  return {
    require: '^form'
    restrict: 'A'
    link: link
    scope:
      ngModel: '='
  }
]


#============================================================================
# Create a select2 control for predefined data
#============================================================================
app.directive "selectStatic", ['$timeout', ($timeout) ->
  link = (scope, element, attrs, form) ->

    staticData = JSON.parse(attrs.selectStatic)

    element.select2
      data: staticData
      minimumInputLength: 0
      query: (query) ->
        data = { results: [] }
        for d in this['data']
          if d.text
            if not query.term or  d.text.toLowerCase().indexOf(query.term.toLowerCase().strip()) != -1
              data.results.push({ id:d.id, text: d.text });

        # TODO: This should be configurable via the directive, for now only variable selection using this
        if query.term and data.results.length == 0 and query.term.strip().length > 0 and /^[a-zA-Z0-9-][a-zA-Z0-9- ]*$/.test(query.term.strip())
          data.results.push({id:'[_NEW_]' + query.term, text: gettext('Add new variable') + ': ' + query.term});
        query.callback(data)

      formatNoMatches: (term) ->
        return gettext("Enter a valid name, only letters, numbers, dashes and spaces are allowed")

      createSearchChoice: (term, data) ->
        return data


    field = form[attrs['name']]
    select2 = element.data('select2')

    initial = {}
    if attrs.key and attrs.text
      initial = {id:attrs.key, text: attrs.text}
      select2.data(initial)
    field['selected'] = select2.data()

    element.on 'change', (e) ->
      field['selected'] = select2.data()
      if attrs.required
        if not field['selected'] or field['selected'].length == 0
          select2.container.find('.select2-choices').addClass('select2-required')
          scope.$apply ->
            field.$setValidity("required", false)
        else
          select2.container.find('.select2-choices').removeClass('select2-required')
          scope.$apply ->
            field.$setValidity("required", true)

    $timeout ->
      element.trigger('change')
    , 0

  return {
    restrict: "A"
    require: "^form"
    link: link
  }
]


#============================================================================
# Directive for an omnibox
#============================================================================
app.directive "omnibox", [ "$timeout", "$log", "Flow", ($timeout, $log, Flow) ->

  omniRemap = (element, callback) ->
    callback()
    return

  omniArbitraryNumberOption = (term, data) ->
    return null  if anon_org
    if $(data).filter(->
      @text.localeCompare(term) is 0
    ).length is 0
      if not isNaN(parseFloat(term)) and isFinite(term)
        id: "n-" + term
        text: term

  omniFormatOmniboxSelection = (item) ->
    return ""  if item.length is 0
    omniFormatOmniboxItem(item)

  omniFormatOmniboxOption = (item, container, query) ->
    # support also numbers which are entered in the omnibox with a plus sign
    query.term = query.term.substring(1, query.length) if query.term[0] == "+"
    return omniFormatOmniboxItem(item)

  omniFormatOmniboxItem = (item) ->
    text = item.text
    if item.extra?
      text = item.text + " (" + item.extra + ")"
    clazz = ''

    if item.id.indexOf("g-") is 0
      clazz = 'omni-group'
    else if item.id.indexOf("c-") is 0
      clazz = 'omni-contact'
    else if item.id.indexOf("u-") is 0
      if item.scheme == 'tel'
        clazz = 'omni-tel'
      else if item.scheme == 'twitter'
        clazz = 'omni-twitter'

    return '<div class="omni-option ' + clazz + '">' + text + '</div>'

  arbitraryAddFunction = (term, data) ->
      if term.indexOf('@') != 0 and data.length == 0
        return { id: term, text: term }

  extraAndArbitraryAddFunction = (term, data) ->
      if /^@extra.(\w+)(\.\w+)*$/.test(term)
        return { id: term, text: term }
      else
        return arbitraryAddFunction(term, data)

  omnibox = (ele, options) ->

    data = []
    options = {}  if options is `undefined`

    if options.completions
      for idx of options.completions
        v = "@" + options.completions[idx].name.toLowerCase()
        data.push
          id: v
          text: v

    if options.types
      types = options.types
    else
      types = 'cg'

    if options.types == 'g'
      placeholder = gettext("Enter one or more contact groups")
    else
      placeholder = gettext("Recipients, enter contacts or groups")

    ele.attr "placeholder", placeholder
    q = ""

    # set our function to show and additional search choice
    if options.arbitraryAdd
      # allow using @extra variables
      if options.allowExtra
        options.createSearchChoice = extraAndArbitraryAddFunction
      else
        options.createSearchChoice = arbitraryAddFunction
    # allow arbitrary numbers if there is no custom create search choice and urns are allowed
    else if !options.createSearchChoice and types and types.indexOf('u') >= 0
      options.createSearchChoice = omniArbitraryNumberOption

    multiple = true
    multiple = options.multiple  unless options.multiple is `undefined`

    ele.removeClass("loading").select2
      placeholder: placeholder
      data: data
      allowClear: false
      initSelection: omniRemap
      selectOnBlur: false
      minimumInputLength: 0
      multiple: multiple
      createSearchChoice: options.createSearchChoice
      ajax:
        url: "/contact/omnibox/?types=" + types
        dataType: "json"
        data: (term, page, context) ->
          q = term
          search: term
          page: page

        results: (response, page, context) ->
          if data and q
            q = q.toLowerCase()
            if q.indexOf("@") is 0
              for idx of data
                variable = data[idx]
                response.results.unshift variable  if variable.id.indexOf(q) is 0
          return response

      escapeMarkup: (m) ->
        return m

      containerCssClass: "omnibox-select2"
      formatSelection: omniFormatOmniboxSelection
      formatResult: omniFormatOmniboxOption

  parseData = (data) ->
    groups = []
    contacts = []
    variables = []

    for item in data
      if item.id[0] == 'g'
        groups.push({id:item.id.slice(2), name:item.text})
      else if item.id[0] == 'c'
        contacts.push({id:item.id.slice(2), name:item.text})
      else if item.id[0] == '@'
        variables.push({id:item.id, name:item.id})
      else
        # New groups can be created
        groups.push(item.text)


    return {
      groups: groups
      contacts: contacts
      variables: variables
      total: groups.length + contacts.length + variables.length
    }


  link = (scope, element, attrs, form) ->

    options = {}
    if attrs.omnibox
      options = JSON.parse(attrs.omnibox)

    # pull our completions out of the scope if we're told to use them
    if options.completions
      options.completions = Flow.completions

    data = []
    if scope.groups
      for group in scope.groups
        if group.name
          data.push({ id:'g-' + group.uuid, text:group.name})
        else
          data.push({ id:group, text:group })

    if scope.contacts
      for contact in scope.contacts
        if contact.name
          data.push({ id:'c-' + contact.uuid, text:contact.name})
        else
          data.push({ id:contact, text:contact })

    if scope.variables
      for variable in scope.variables
        data.push
          id: variable.id
          text: variable.id

    # set initial data
    select2 = omnibox(element, options).data('select2')
    select2.data(data)

    field = form[attrs['name']]
    field['selected'] = parseData(data)

    element.on 'change', (e) ->
      field['selected'] = parseData(select2.data())
      if attrs.required
        if field['selected'].total == 0
          select2.container.find('.select2-choices').addClass('select2-required')
          scope.$apply ->
            field.$setValidity("required", false)
        else
          select2.container.find('.select2-choices').removeClass('select2-required')
          scope.$apply ->
            field.$setValidity("required", true)

    $timeout ->
      element.trigger('change')
    , 0

  return {
    restrict: "AC"
    require: "^form"
    scope: {
      groups: "="
      contacts: "="
      variables: "="
    }
    link:link
  }
]
