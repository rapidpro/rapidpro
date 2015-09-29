class window.AutoComplete

  KEY_LEFT = 37
  KEY_RIGHT = 39

  constructor: (@variables=[], @functions=[]) ->
    @parser = new window.excellent.Parser('@', ['channel', 'contact', 'date', 'extra', 'flow', 'step']);
    @completions = @variables.concat(@functions)
    ac = this

    @config =
      at: "@"
      insertBackPos: 1
      data: @variables
      searchKey: "name"
      insertTpl: '@${name}'
      startWithSpace: true
      displayTpl: "<li><div class='custom-atwho-display'><div class='option-name'>${name}</div><small class='option-display'>${display}</small></div></li>"
      limit: 100
      maxLen: 100
      suffix: ""
      callbacks:

        highlighter: (li, query) -> return li

        matcher: (flag, subtext) ->
          return ac.parser.expressionContext(subtext)

        filter: (query, data, searchKey) ->

          if query and query[0] is '('
            data = ac.completions

          subQuery = ac.parseQuery(query)
          lastIdx = subQuery.lastIndexOf('.')
          start = subQuery.substring(0, lastIdx)
          results = ac.findCompletions(subQuery, data, start, lastIdx)

          return results

        sorter: (query, items, searchKey) ->

          lastOptFunctions =
            'name': '('
            'display': "Functions"

          if not query
            items.push(lastOptFunctions)
            return items

          subQuery = ac.parseQuery(query);

          _results = []
          for item in items
            item.atwho_order = new String(item[searchKey]).toLowerCase().indexOf(subQuery.toLowerCase())
            _results.push item if item.atwho_order > -1

          if query.match(/[(.]/g) is null
            _results.push(lastOptFunctions)

          _results.sort (a,b) -> a.atwho_order - b.atwho_order

          return _results

        tplEval: (tpl, map, action) ->

          template = tpl;
          query = this.query.text
          subQuery = ac.parseQuery(query)

          if action is 'onInsert'
            if query and query[0] is '(' and query.length is 1 and subQuery is ""
              template = '@(${name}'
            else
              regexp = new RegExp(subQuery + "$")
              template = ('@' + query).replace(regexp, '${name}')

          try
            template = tpl(map) unless typeof tpl is 'string'

            if typeof map.example isnt "undefined" and action is "onDisplay"
              template = "<li><div class='custom-atwho-display'><div class='option-name'>${name}</div><div class='option-example'><div class='display-labels'>Example</div>${example}</div><div class='option-display'><div class='display-labels'>Summary</div>${display}</div></div></li>"

            template.replace /\$\{([^\}]*)\}/g, (tag, key, pos) -> map[key]
          catch error
            return ""

        beforeInsert: (value, item) ->

          completionChars = new RegExp("([A-Za-z_\d\.]*)$", 'gi')
          valueForName = ""
          match = completionChars.exec(value)
          if match
            valueForName = match[2] || match[1]

          hasMore = false
          for option in ac.variables
            hasMore = valueForName and option.name.indexOf(valueForName) is 0 and option.name isnt valueForName
            if hasMore
              break

          value += '.' if hasMore

          isFunction = false
          for option in ac.functions
            isFunction = valueForName and option.name.indexOf(valueForName) is 0 and option.name is valueForName
            if isFunction
              break

          value += '()' if isFunction

          if valueForName is "" and value is '@('
            value += ')'
          else if valueForName and not hasMore and not isFunction
            value += " "

          return value

  parseQuery: (query) ->
    if not query
      return query
    return @parser.autoCompleteContext(query) or ''

  findCompletions: (query, data, start, lastIdx, prependChar=undefined) ->

    matched = {}
    results = []

    for option in data
      if option.name.toLowerCase().indexOf(query.toLowerCase()) == 0
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

          if name.toLowerCase().indexOf(query.toLowerCase()) != 0
            continue

          display = null

        if name not of matched
          matched[name] = name

          matchingOption =
            name: name
            display: display

          for key in Object.keys(option)
            if key isnt 'name' and key isnt 'display'
              matchingOption[key] = option[key]

          results.push(matchingOption)

    return results


  bind: (selector, variables=null) ->

    if variables
      @completions = variables.concat(@functions)



    $inputor = $(selector).atwho(@config)
    $inputor.focus().atwho('run')

    # when an option is selected, insert the text and update the caret
    $inputor.on 'inserted.atwho', (atEvent, li, browserEvent) ->
      content = $inputor.val()
      caretPos = $inputor.caret 'pos'
      subtext = content.slice 0, caretPos
      if subtext.match(/\(\)$/) isnt null
        $inputor.caret('pos', subtext.length - 1)

    # do react to clicking inside expressions
    $inputor.off('click.atwhoInner').on 'click.atwhoInner', (e) ->
      $.noop()

    # check for possible inserts when a key is pressed
    $inputor.off('keyup.atwhoInner').on 'keyup.atwhoInner', (e) ->

      atwho = $inputor.data('atwho')

      if atwho
        app = atwho.setContextFor('@')
        view = app.controller()?.view

        switch e.keyCode
          when KEY_LEFT, KEY_RIGHT
            if view.visible()
              app.dispatch(e)
            return
          else
            app.onKeyup(e)

        content = $inputor.val()
        caretPos = $inputor.caret 'pos'
        subtext = content.slice(0, caretPos)
        if subtext.slice(-2) is '@('
          text = subtext + ')' + content.slice(caretPos + 1)
          $inputor.val(text)

        $inputor.caret('pos', caretPos)