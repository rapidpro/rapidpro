describe 'Temba:', ->

  describe 'Modax:', ->

    it 'should store the original submission button text', ->
      fixture = """
        <div id='modal-template' class='active-modal'>
          <div class='modal-body'>
            <div class='fetched-content'></div>
          </div>
          <div class='modal-footer'>
            <button class='btn btn-primary primary'>Ok</button>
          </div>
        </div>
      """

      document.body.insertAdjacentHTML('afterbegin', fixture)
      modax = new Modax('Modax Title', '/')
      modax.show()
      expect(modax.submitText).toBe('Ok')
