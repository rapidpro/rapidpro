# backfill IE* with trim, rtrim, ltrim and strip
if not String::trim?
  String::trim = -> @replace /^\s+|\s+$/g, ""
if not String::rtrim?
  String::rtrim = -> @replace /^\s+/, ""
if not String::ltrim?
  String::ltrim = -> @replace /\s+$/, ""
if not String::strip?
  String::strip = -> @replace /^\s+|\s+$/g, ""

TAB = 9
ENTER = 13

filters = [
  { name:'title_case', display:'changes to title case'},
  { name:'capitalize', display:'capitalizes the first letter'},
  { name:'first_word', display:'takes only the first word'}
  { name:'remove_first_word', display:'takes everything after the first word'}
  { name:'upper_case', display:'upper cases all letters'}
  { name:'lower_case', display:'lower cases all letters'}
  { name:'read_digits', display:'reads back a number in a friendly way'}
]

findMatches = (query, data, start, lastIdx, prependChar = undefined) ->

  matched = {}
  results = []

  for option in data
    if option.name.indexOf(query) == 0
      nextDot = option.name.indexOf('.', lastIdx + 1)
      if nextDot == -1

        if prependChar
          name = start + prependChar + option.name
        else
          name = option.name

        display = option.display
      else
        name = ""
        suffix = option.name.substring(lastIdx+1, nextDot)
        if start.length > 0 and start != suffix
          name = start + "."
        name += suffix

        if name.indexOf(query) != 0
          continue

        display = null

      if name not of matched
        matched[name] = name
        results.push({ name: name, display: display })

  return results


@useFontCheckbox = (selector, displayLabel=false) ->

  checkboxes = $(selector)

  checkboxes.each ->
    input = $(this)
    controlGroup = input.parents('.form-group')
    label = controlGroup.children("label").text()
    help = input.parent().children(".help-block")

    html = "<div class='form-group font-checkbox'>"
    html += "<label"
    if !displayLabel
      html += " id='checkbox-label'"
    html += " class='control-label' for='"
    html += input.prop('id')
    html += "'>"
    html += label
    html += "</label>"
    html += "<div class='controls field-input"
    if input.prop('checked')
      html += " checked"
    html += "'>"
    html += "<div class='hidden-input hide'>"
    html += "<input name='" + input.prop('name') + "' id='" + input.prop('id') + "' type='" + input.prop('type') + "' "
    if input.prop('checked')
      html += " checked"
    html += "/>"
    html += "</div>"
    html += "<div class='glyph notif-checkbox'></div><div></div>"
    if help
      if !displayLabel
        html += "<div class='help-block'><label for='"
        html += input.prop('id')
        html += "'>" + help.text() + "</label></div>"
      else
        html += "<p class='help-block'>" + help.text() + "</p>"
    html += "</div></div>"

    controlGroup.replaceWith(html)

  ele = $(".font-checkbox")

  glyphCheck = ele.children('.controls').children('.glyph.notif-checkbox')
  glyphCheck.on 'click', ->
    cell = $(this).parent('.field-input')
    ipt = cell.children().children("input[type='checkbox']")

    if ipt.prop('checked')
      cell.removeClass 'checked'
      ipt.prop('checked', false)
    else
      cell.addClass 'checked'
      ipt.prop('checked', true)

  chkBox = ele.find("input[type=checkbox]")
  chkBox.on 'change', ->
    cell = ele.find('.field-input')

    if $(this).prop('checked')
      cell.addClass 'checked'
    else
      cell.removeClass 'checked'


@select2div = (selector, width="350px", placeholder=null, add_prefix=null) ->
  ele = $(selector)
  children = ele.children('option')
  options = []
  selected = null
  for child in children
    option = { id: child.value, text: child.label }
    if child.selected
      selected = option
    options.push(option)

  ele.replaceWith("<input width='" + width + "' name='" + ele.attr('name') + "' style='width:" + width + "' id='" + ele.attr('id') + "'/>")
  ele = $(selector)

  if add_prefix
    ele.select2
      name: name
      data: options
      placeholder: placeholder
      query: (query) ->
        data = { results: [] }
        for d in this['data']
          if d.text.toLowerCase().indexOf(query.term.toLowerCase().strip()) != -1
            data.results.push({ id:d.id, text: d.text });
        if data.results.length == 0 and query.term.strip().length > 0
          data.results.push({id:'[_NEW_]' + query.term, text: add_prefix + query.term});
        query.callback(data)
      createSearchChoice: (term, data) -> return data
  else
    ele.select2
      minimumResultsForSearch: 99
      data: options
      placeholder: placeholder

  if selected
    ele.data('select2').data(selected)

###


@initAtMessageText = (selector, completions=null) ->
  completions = window.message_completions unless completions

  $(selector).atwho
    at: "@"
    limit: 15
    insert_space: false
    max_len: 100
    data: completions
    callbacks:
      before_insert: (value, item, selectionEvent) ->

        # see if there's more data to filter on
        data = this.settings['@']['data']
        hasMore = false
        for option in data
          if option.name.indexOf(value) == 0 and option.name != value
            hasMore = true
            break

        if selectionEvent.keyCode == TAB and hasMore
          value += '.'
        else
          value += ' '
        return value

      filter: (query, data, search_key) ->

        q = query.toLowerCase()
        lastIdx = q.lastIndexOf('.')
        start = q.substring(0, lastIdx)

        results = findMatches(q, data, start, lastIdx)

        if results.length > 0
          return results

        flag = "@"
        flag = "(?:^|\\s)" + flag.replace(/[\-\[\]\/\{\}\(\)\*\+\?\\\^\$\|]/g, "\\$&")
        regexp = new RegExp("([A-Za-z0-9_+-.]*\\|)([A-Za-z0-9_+-.]*)", "gi")
        match = regexp.exec(q)

        if match

          # check that we should even be matching
          name = q.substring(0, q.indexOf('|'))
          found = false
          for d in data
            if d.name == name
              found = true
              break

          if not found
            return results

          filterQuery = match[2]
          lastIdx = q.lastIndexOf('|') + 1
          start = q.substring(0, lastIdx - 1)
          filterQuery = q.substring(lastIdx)
          results = findMatches(filterQuery, filters, start , q.lastIndexOf('|'), '|')

        return results


      tpl_eval: (tpl, map) ->

        if not map.display
          tpl = "<li data-value='${name}'>${name}</li>"
        try
          return tpl.replace /\$\{([^\}]*)\}/g, (tag, key, pos) -> map[key]
        catch error
          return ""

      highlighter: (li, query) ->
        return li

      matcher: (flag, subtext) ->
        flag = "(?:^|\\s)" + flag.replace(/[\-\[\]\/\{\}\(\)\*\+\?\\\^\$\|]/g, "\\$&")
        regexp = new RegExp(flag + "([A-Za-z0-9_+-.\\|]*)$|" + flag + "([^\\x00-\\xff]*)$", "gi")
        match = regexp.exec(subtext)
        if match
          match[2] or match[1]
        else
          null

    tpl: "<li data-value='${name}'>${name} (<span>${display}</span>)</li>"
###


# -------------------------------------------------------------------
# Our basic modal, with just message and an OK button to dismiss it
# -------------------------------------------------------------------
class @Modal
  constructor: (@title, @message) ->
    modal = @
    @autoDismiss = true
    @ele = $('#modal-template').clone()
    @ele.data('object', @)
    @ele.attr('id', 'active-modal')
    @keyboard = true
    modalClose = @ele.find('.close')
    modalClose.on('click', -> modal.dismiss())

  setIcon: (@icon) ->
    @ele.find('.icon').addClass('glyph').addClass(@icon)

  setPrimaryButton: (buttonName=gettext('Ok')) ->
    primary = @ele.find('.primary')
    primary.text(buttonName)

  setTertiaryButton: (buttonName='Options', handler) ->
    tertiary = @ele.find('.tertiary')
    tertiary.text(buttonName)
    tertiary.on 'click', handler
    tertiary.show()

  show: ->
    modal = @

    @ele.on 'hidden', ->
      if modal.listeners and modal.listeners.onDismiss
        modal.listeners.onDismiss(modal)

    @ele.find('#modal-title').html(@title)
    if @message
      @ele.find('#modal-message').html(@message)
    else
      @ele.find('#modal-message').hide()
    @ele.modal
      show: true
      backdrop: 'static'
      keyboard: @keyboard


  addListener: (event, listener) ->
    @listeners[event] = listener

  setListeners: (@listeners, @autoDismiss=true) ->
    modal = @
    primary = @ele.find('.primary')

    if @listeners.onPrimary
      primary.off('click').on 'click', ->
        if modal.listeners.onBeforePrimary
          if modal.listeners.onBeforePrimary(modal)
            return
        modal.listeners.onPrimary(modal)

        if modal.autoDismiss
          modal.dismiss()

    else
      if modal.autoDismiss
        primary.on 'click', -> modal.dismiss()

  setMessage: (@message) ->

  dismiss: ->
    @ele.modal('hide')
    @ele.remove()

  addClass: (className) ->
    @ele.addClass(className)

  focusFirstInput: ->
    @ele.find("input,textarea").filter(':first').focus()

# -------------------------------------------------------------------
# A button that can be populated with a JQuery element and has
# a primary and secondary button
# -------------------------------------------------------------------
class @ConfirmationModal extends @Modal
  constructor: (title, message) ->
    super(title, message)
    modal = @
    secondary = @ele.find('.secondary')
    secondary.on('click', -> modal.dismiss())
    secondary.show()

  hideSecondaryButton: ->
    @ele.find('.secondary').hide()

  setForm: (form) ->
    @ele.find('.modal-body .form').append(form)

  getForm: ->
    return @ele.find('.modal-body .form').children(0)

  show: ->
    super()
    @focusFirstInput()

  setListeners: (listeners, autoDismiss=true) ->
    super(listeners, autoDismiss)
    modal = @
    if modal.listeners.onSecondary
      secondary = @ele.find('.secondary')
      secondary.on 'click', ->
        modal.listeners.onSecondary(modal)

# -------------------------------------------------------------------
# A modal populated with a PJAX element
# -------------------------------------------------------------------
class @Modax extends @ConfirmationModal
    constructor: (title, @url) ->
      super(title, null)
      modal = @

      @ele.find('.primary').on('click', ->
        modal.submit()
      )

    setRedirectOnSuccess: (@redirectOnSuccess) ->

    setListeners: (listeners, autoDismiss=false) ->
      super(listeners, autoDismiss)

    show: ->
      super()
      @ele.find('.loader').show()

      modal = @
      modal.submitText = modal.ele.find('.primary').text()

      fetchPJAXContent(@url, "#active-modal .fetched-content",
        onSuccess: ->
          modal.ele.find('.loader').hide()

          # if the form comes back with a save button defer to that
          submitText = $(".form-group button[type='submit']").text()
          if submitText
            modal.submitText = submitText

          modal.ele.find(".primary").text(modal.submitText)
          modal.focusFirstInput()
          if modal.listeners and modal.listeners.onFormLoaded
            modal.listeners.onFormLoaded()

          modal.wireEnter()
          prepareOmnibox()
      )

    # trap ENTER on the form, use our modal submit
    wireEnter: ->
      modal = @
      modal.ele.find("form").on('keydown', (e) ->
        if e.keyCode == ENTER
          modal.submit()
          return false
        )
 
    submit: ->
      modal = @
      modal.ele.find('.primary').text(gettext("Processing..")).addClass("disabled")
      postData = modal.ele.find('form').serialize();
      fetchPJAXContent(@url, '#active-modal .fetched-content',
        postData: postData
        shouldIgnore: (data) ->
          ignore = /success-script/i.test(data)
          return ignore

        onIgnore: (xhr) ->
          if not modal.redirectOnSuccess
            modal.ele.find(".primary").removeClass("disabled").text(modal.submitText)

          if modal.listeners
            if modal.listeners.onCompleted
              modal.listeners.onCompleted(xhr)

            # this is actually the success case for Modax modals
            if modal.listeners.onSuccess
              modal.listeners.onSuccess(xhr)

          if modal.redirectOnSuccess
            modal.ele.find('.fetched-content').hide()
            modal.ele.find('.loader').show()

            redirect = xhr.getResponseHeader("Temba-Success")
            if redirect
              document.location.href = redirect
            else
              modal.dismiss()
          else
            modal.dismiss()

        onSuccess: ->
          modal.ele.find(".primary").removeClass("disabled").text(modal.submitText)
          if modal.listeners and modal.listeners.onCompleted
              modal.listeners.onCompleted()
          else
            modal.wireEnter()
            modal.focusFirstInput()
      )

$ ->
  $('.uv-send-message').click ->
    UserVoice.push(['show', {}]);
