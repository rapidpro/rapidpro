describe 'Matcher:', ->
  expected = null

  it 'should match "" after flag', ->
    expected = matcher("@", "some texts before @")
    expect(expected).toBe("")

