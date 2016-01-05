fetchPJAXContent = (url, container, options) ->
  type = 'GET'
  data = undefined
  if options
    if 'postData' of options
      type = 'POST'
      data = options['postData']
  headers = 'X-PJAX': true
  if options and 'headers' of options
    for key of options['headers']
      `key = key`
      headers[key] = options['headers'][key]
  $.ajax
    headers: headers
    type: type
    url: url
    data: data
    success: (data, status, jqXHR) ->
      if 'followRedirects' of options and options['followRedirects'] == true
        redirect = jqXHR.getResponseHeader('REDIRECT')
        if redirect
          window.document.location.href = redirect
          return
      noPJAX = $(container).data('no-pjax')
      if options
        if !('forceReload' of options) or 'forceReload' of options and !options['forceReload']
          if noPJAX or 'shouldIgnore' of options and options['shouldIgnore'](data)
            if 'onIgnore' of options
              options['onIgnore'] jqXHR
            return
      $(container).html data
      if options
        if 'onSuccess' of options and options['onSuccess']
          options['onSuccess']()
      return
  return