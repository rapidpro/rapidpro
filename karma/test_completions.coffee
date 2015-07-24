describe 'Matcher:', ->

  it 'should match "" after flag', ->
    matched = matcher "@", "some texts before @"
    expect(matched).toBe("")

  it 'should not match escaped texts', ->
    matched = matcher "@", "some texts before @@contact"
    expect(matched).toBe(null)

  it 'should match variable after flag', ->
    matched = matcher "@", "some texts before @contact"
    expect(matched).toBe("contact")

  it 'should match variables with dot', ->
    matched = matcher "@", "some texts before @contact.born"
    expect(matched).toBe("contact.born")

  it 'should match variables with dot as big as possible', ->
    matched = matcher "@", "some texts before @contact.born.where.location"
    expect(matched).toBe("contact.born.where.location")

  it 'should not match space if we have a space at the end', ->
    matched = matcher "@", "some texts before @contact "
    expect(matched).toBe(null)

  it 'should not match space if if last word does not have flag', ->
    matched = matcher "@", "some texts before @contact contact"
    expect(matched).toBe(null)

  it 'should match functions', ->
    matched = matcher "@", "some texts before @(SUM"
    expect(matched).toBe("(SUM")

  it 'should not match escaped functions', ->
    matched = matcher "@", "some texts before @@(SUM"
    expect(matched).toBe(null)

  it 'should match all the function', ->
    matched = matcher "@", "some texts before @(SUM()"
    expect(matched).toBe("(SUM()")

  it 'should match the function as long as possible', ->
    matched = matcher "@", "some texts before @(SUM(contact.age, step.value"
    expect(matched).toBe("(SUM(contact.age, step.value")

  it 'should match the function as long as possible, may commas, underscores', ->
    matched = matcher "@", "some texts before @(SUM(contact.age, step.value, date.now_time"
    expect(matched).toBe("(SUM(contact.age, step.value, date.now_time")

  it 'should match the function as long as possible', ->
    matched = matcher "@", "some texts before @(SUM(contact.age, step.value))))"
    expect(matched).toBe("(SUM(contact.age, step.value))))")

  it 'should not match if space after last )', ->
    matched = matcher "@", "some texts before @(SUM(contact.age, step.value)))) "
    expect(matched).toBe(null)

describe 'find context query', ->

  it 'should return if not query', ->
    ctxtQuery = findContextQuery ''
    expect(ctxtQuery).toBe('')

    ctxtQuery = findContextQuery null
    expect(ctxtQuery).toBe(null)

    ctxtQuery = findContextQuery undefined
    expect(ctxtQuery).toBe(undefined)

  it 'ignore first ( and return empty string', ->
    ctxtQuery = findContextQuery '('
    expect(ctxtQuery).toBe("")

  it 'should be the same for variables', ->
    ctxtQuery = findContextQuery "contact"
    expect(ctxtQuery).toBe('contact')

    ctxtQuery = findContextQuery "contact.age"
    expect(ctxtQuery).toBe('contact.age')

    ctxtQuery = findContextQuery "contact.added_on"
    expect(ctxtQuery).toBe('contact.added_on')

  it 'no ( for function only', ->
    ctxtQuery = findContextQuery "(SUM"
    expect(ctxtQuery).toBe('SUM')

  it 'should give the last variable', ->
    ctxtQuery = findContextQuery "(SUM(contact.date_added"
    expect(ctxtQuery).toBe('contact.date_added')

  it 'should return function after comma', ->
    ctxtQuery = findContextQuery "(SUM(contact.date_added,"
    expect(ctxtQuery).toBe('SUM')

  it 'should return empty string after comma followed by space', ->
    ctxtQuery = findContextQuery "(SUM(contact.date_added,  "
    expect(ctxtQuery).toBe('')

  it 'should ignore function out of balanced paratheses', ->
    ctxtQuery = findContextQuery "(SUM(contact.date_added, step)"
    expect(ctxtQuery).toBe('SUM')

    ctxtQuery = findContextQuery "(SUM(contact.date_added, ABS(step.value)"
    expect(ctxtQuery).toBe('ABS')

    ctxtQuery = findContextQuery "(SUM(contact.date_added, ABS(step.value))"
    expect(ctxtQuery).toBe('SUM')

  it 'should not include previous (', ->
    ctxtQuery = findContextQuery "(contact.age"
    expect(ctxtQuery).toBe('contact.age')

describe 'Find matches:', ->
  data = [{"name":"contact", "display":"Contact Name"}, {"name":"channel", "display":"Channel Name"},
          {"name":"contact.age", "display":"Contact Age"}, {"name":"contact.name", "display":"Contact Name"},
          {"name":"contact.urn.path.tel.number", "display":"Contact URN tel number"},
          {"name":"channel.address", "display":"Channel Address"}]

  it 'should give unique first parts for empty string query', ->
    results = findMatches '', data, '', -1
    expected = [{"name":"contact", "display":"Contact Name"}, {"name":"channel", "display":"Channel Name"}]
    expect(results).toEqual(expected)

  it 'should match first parts filtered', ->
    results = findMatches 'c', data, '', -1
    expected = [{"name":"contact", "display":"Contact Name"}, {"name":"channel", "display":"Channel Name"}]
    expect(results).toEqual(expected)

    results = findMatches 'con', data, '', -1
    expected = [{"name":"contact", "display":"Contact Name"}]
    expect(results).toEqual(expected)

  it 'should start showing second ; only show display if name is full', ->
    results = findMatches 'contact.', data, 'contact', 7
    expected = [
        {"name":"contact.age", "display":"Contact Age"}, {"name":"contact.name", "display":"Contact Name"},
        {"name":"contact.urn", "display":null}]
    expect(results).toEqual(expected)

  it 'should start showing thirt parts...; only show display if name is full', ->
    results = findMatches 'contact.urn.pa', data, 'contact.urn', 11
    expected = [{"name":"contact.urn.path", "display":null}]
    expect(results).toEqual(expected)


describe 'Sorter:', ->

  it 'should include "Functions" for null query', ->
    items = []
    results = sorter null, items, 'name'
    expect(results).toEqual([{'name': '(', 'display': 'Functions'}])

  it 'should include "Functions" for empty query', ->
    items = []
    results = sorter "", items, 'name'
    expect(results).toEqual([{'name': '(', 'display':'Functions'}])

  it 'should include "Functions" for null query', ->
    items = [{'name':'contact.addition', 'display':'Contact Addition'},
             {'name':'econtact.added', 'display':'e-Contact Added'}]
    results = sorter null, items, 'name'
    expect(results).toEqual([{'name': 'contact.addition', 'display': 'Contact Addition'},
                             {'name': 'econtact.added', 'display': 'e-Contact Added'},
                             {'name': '(', 'display': 'Functions'}])

  it 'should include "Functions" for empty query', ->
    items = [{'name':'contact.addition', 'display':'Contact Addition'},
             {'name':'econtact.added', 'display':'e-Contact Added'}]
    results = sorter "", items, 'name'
    expect(results).toEqual([{'name':'contact.addition', 'display':'Contact Addition'},
                             {'name':'econtact.added', 'display':'e-Contact Added'},
                             {'name': '(', 'display':'Functions'}])

  it 'should include "Functions" for query that do not have a dot', ->
    items = [{'name':'contact.addition', 'display':'Contact Addition'},
             {'name':'econtact.added', 'display':'e-Contact Added'}]
    results = sorter "contact", items, 'name'
    expect(results).toEqual([{'name':'contact.addition', 'display':'Contact Addition', 'atwho_order': 0},
                               {'name':'econtact.added', 'display':'e-Contact Added', 'atwho_order': 1},
                               {'name': '(', 'display':'Functions'}])
  it 'should not include "Functions" for query that have a dot', ->
    items = [{'name':'contact.addition', 'display':'Contact Addition'},
             {'name':'econtact.added', 'display':'e-Contact Added'}]
    results = sorter "contact.add", items, 'name'
    expect(results).toEqual([{'name':'contact.addition', 'display':'Contact Addition', 'atwho_order':0},
                             {'name':'econtact.added', 'display':'e-Contact Added', 'atwho_order':1}])


describe 'Filter:', ->
  data = [{"name":"contact", "display":"Contact Name"}, {"name":"channel", "display":"Channel Name"},
          {"name":"contact.age", "display":"Contact Age"}, {"name":"contact.name", "display":"Contact Name"},
          {"name":"contact.urn.path.tel.number", "display":"Contact URN tel number"},
          {"name":"channel.address", "display":"Channel Address"}]

  it 'should filter using findMatches', ->
    spyOn(window, "findMatches").and.callThrough()
    results = filter "", data, 'name'
    expected = [{"name":"contact", "display":"Contact Name"}, {"name":"channel", "display":"Channel Name"}]
    expect(results).toEqual(expected)
    expect(window.findMatches).toHaveBeenCalledWith("", data, "", -1)

    results = filter "con", data, 'name'
    expected = [{"name":"contact", "display":"Contact Name"}]
    expect(results).toEqual(expected)
    expect(window.findMatches).toHaveBeenCalledWith("con", data, "", -1)

    results = filter "contact.a", data, 'name'
    expected = [{"name":"contact.age", "display":"Contact Age"}]
    expect(results).toEqual(expected)
    expect(window.findMatches).toHaveBeenCalledWith("contact.a", data, "contact", 7)


describe 'beforeInsert:', ->

  beforeEach ->
    window.variables = [
      {"display": "New Contact", "name": "new_contact"}
      {"display": "Contact Name", "name": "contact"}
      {"display": "Contact Name", "name": "contact.name"}
      {"display": "Contact First Name", "name": "contact.first_name"}
    ]

    window.functions =[
      {"display": "Display Returns the sum of all arguments", "description": "Description: Returns the sum of all arguments", "name": "SUM", "hint": "Hint: Returns the sum of all arguments", "example": "SUM(args)", "arguments": [{"name": "args", "hint": "Hint for :-:args:-: arg"}]}
      {"display": "Display: Defines a time value", "description": "Description: Defines a time value", "name": "TIME", "hint": "Hint: Defines a time value", "example": "TIME(hours, minutes, seconds)","arguments": [{"name": "hours", "hint": "Hint for :-:hours:-: arg"}, {"name": "minutes","hint": "Hint for :-:minutes:-: arg"}]}
    ]


  it 'should append a space for variables without more option', ->
    value = beforeInsert "@new_contact"
    expect(value).toBe("@new_contact ")

  it 'should append a dot if value is from variables and we have more options', ->
    value = beforeInsert '@contact', []
    expect(value).toBe("@contact.")

  it 'should balance parantheses', ->
    value = beforeInsert "@(", []
    expect(value).toBe("@()")

  it 'should append parantheses for function', ->
    value = beforeInsert "@(SUM", []
    expect(value).toBe("@(SUM()")

describe 'tplEval:', ->
  beforeEach ->
    window.query =
      text: null

  describe 'onInsert', ->
    it 'should insert ( before name of map in the inserted text if query is just ( with length 1', ->
      window.query.text = "("
      inserted = tplval('@{name}', {'name': "SUM"}, "onInsert")
      expect(inserted).toBe('@(SUM')

    it 'should not insert ( before name inserted if query has length more than 1', ->
      window.query.text ="(SUM(con"

      inserted = tplval('@{name}', {'name': "contact.name"}, "onInsert")
      expect(inserted).toBe('@(SUM(contact.name')

      inserted = tplval('@{name}', {'name': "new_contact"}, "onInsert")
      expect(inserted).toBe('@(SUM(new_contact')

    it 'should use the default tpl', ->
      window.query.text = "cont"

      inserted = tplval('@{name}', {'name': "contact.name"}, "onInsert")
      expect(inserted).toBe('@contact.name')

      window.query.text = '(SUM(contact.name, ste'
      inserted = tplval('@{name}', {'name': "step.value"}, "onInsert")
      expect(inserted).toBe('@(SUM(contact.name, step.value')

  describe 'onDisplay', ->
    it 'should use the li', ->
      window.query.text = "cont"
      displayed = tplval('<li>${name}<small>${display}</small></li>', {'name':'contact.name', 'display':'Contact Name'}, 'onDisplay')
      expect(displayed).toBe('<li>contact.name<small>Contact Name</small></li>')

    it 'should switch template if we have example in map', ->
      window.query.text = "(SUM(contact,"
      displayed = tplval('<li>{name}<small>{display}</small></li>', {'name':'SUM', 'example':'SUM(A, B)', 'hint':'hint for sum', 'display':'SUM of numbers'}, 'onDisplay')
      expect(displayed).toBe("<li><h5>SUM</h5><div>SUM(A, B)</div><div>hint for sum</div><div>BLABLABLABAL</div></li>")
