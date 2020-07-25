showSection = (section) ->
  ie = section.parents("html").hasClass("ie")

  # readonly sections shouldn't open
  return  if section.data("readonly")

  # fixed sections don't animate
  if ie or section.data("action") == 'fixed'
    section.find(".formax-form").show()
    section.find(".formax-icon").css
      "font-size": "80px"
      width: "80px"
      height: "80px"
  else
    section[0].classList.add("open")

###
Manually contract an expandable section
###
hideSection = (section) ->

  # fixed sections can't be hidden
  return if section.data("action") == 'fixed'

  ie = section.parents("html").hasClass("ie")
  if ie
    section.find(".formax-summary").show()
    section.find(".formax-form").hide()
  else
    section[0].classList.remove("open")



###
Fetches new data for the given expandable section.
Note this will take care of binding all dynamic functions.
###
window.fetchData = (section) ->
  if section.data("href")
    url = section.data('href')
    fetchPJAXContent url, "#" + section.attr("id") + " > .formax-container",
      headers:
        "X-FORMAX": true

      onSuccess: ->
        section.data "loaded", true
        _initializeForm section
        if section.data("fixed")
          showSection section
        else
          _bindToggle section.find(".formax-icon")
        section.show()

  else
    section.data "loaded", true

########################################################

_initializeForm = (section) ->

  action = section.data('action')

  # set our form up
  form = section.find("form")

  if action == 'formax' or action == 'redirect' or action == 'open'

    buttonName = section.data("button")
    buttonName = gettext("Save") unless buttonName

    form.off("submit").on "submit", _submitFormax
    unless section.data("nobutton")
      form.append "<input type=\"submit\" class=\"button-primary\" value=\"" + buttonName + "\"/>"
      form.find(".form-actions").remove()
    form.find(".submit-button").on "click", ->
      $(this).addClass("disabled").attr "enabled", false

    onLoad = section.data("onload")
    eval_(onLoad)() if onLoad
    _bindToggle section.find(".formax-summary")  unless section.data("fixed")

    if action == 'open'
      showSection(section)
      window.scrollTo(0, section.offset().top)

  if action == 'fixed'
    # fixed forms post to their original service
    form.attr "action", section.data("href")


_submitFormax = (e) ->
  e.preventDefault()
  form = $(this)
  section = form.parents(".formax-section")
  followRedirects = section.data("action") == 'redirect'

  fetchPJAXContent section.data("href"), "#" + section.attr("id") + " > .formax-container",
    postData: form.serialize()
    headers:
      "X-FORMAX": true

    followRedirects: followRedirects
    onSuccess: ->
      _initializeForm section
      formax_form = section.find(".formax-form")
      if formax_form.hasClass("errors")
        section.find(".formax-summary").hide()
        formax_form.show()
      else
        hideSection section unless section.data("action") == 'fixed'

      # dependents is a standard selector, anything that jQuery
      # accepts will be run through our formax initializer
      dependents = section.data("dependents")
      if dependents
        $("#id-" + dependents).each ->
          fetchData $(this)

_bindToggle = (bindTo) ->
  section = bindTo.parents(".formax-section")
  action = section.data('action')
  if action =='fixed'
    showSection(section)
  else if action == 'formax' or action == 'redirect' or action == 'open'
    bindTo.off("click").on "click", ->
      section = $(this)
      section = bindTo.parents(".formax-section") unless not bindTo.tagName is "formax"

      $(".formax > .formax-section").each ->
        hideSection $(this)  unless $(this).attr("id") is section.attr("id")

      if section[0].classList.contains("open")
        hideSection(section)
      else
        showSection(section)
  else if action == 'link'
    bindTo.off("click").on "click", ->
      document.location.href = section.data('href')

$ ->
  $('.formax-section .formax-summary').each ->
    section = $(this)
    _bindToggle(section)

  $('.formax .formax-section').each ->
    section = $(this)
    _initializeForm(section)

  $('.formax-section .formax-icon').each ->
    section = $(this)
    _bindToggle(section)
