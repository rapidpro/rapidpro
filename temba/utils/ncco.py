import json


class NCCOException(Exception):
    pass


class Response(object):

    def __init__(self, **kwargs):
        self.document = []

    def __str__(self):
        return json.dumps(self.document)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def join(self, response):
        self.document = response.document + self.document
        return self

    def say(self, text, **kwargs):
        self.document.append(dict(action='talk', text=str(text)))
        return self

    def play(self, url=None, digits=None, **kwargs):
        if url is None and digits is None:
            raise NCCOException("Please specify either a url or digits to play.", )

        result = dict()
        if url:
            result['action'] = 'stream'
            result['streamUrl'] = [url]

        elif digits:
            result['action'] = 'talk'
            result['text'] = digits
        self.document.append(result)
        return self

    def pause(self, **kwargs):
        return self

    def redirect(self, url=None, **kwargs):
        result = dict(action='input', maxDigits=1, submitOnHash=True, timeOut=1,
                      eventUrl=["%s?input_redirect=1" % url])

        self.document.append(result)
        return self

    def hangup(self, **kwargs):
        return self

    def reject(self, reason=None, **kwargs):
        self.hangup()
        return self

    def gather(self, **kwargs):

        result = dict(action='input')

        if kwargs.get('action', False):
            method = kwargs.get('method', 'post')
            result['eventMethod'] = method
            result['eventUrl'] = [kwargs.get('action')]

        result['submitOnHash'] = kwargs.get('finishOnKey', '#') == '#'

        if kwargs.get('numDigits', False):
            result['maxDigits'] = str(kwargs.get('numDigits'))

        if kwargs.get('timeout', False):
            result['timeOut'] = str(kwargs.get('timeout'))

        self.document.append(result)
        return self

    def record(self, **kwargs):
        result = dict(format='wav', endOnSilence='4', beepStart=True, action='record')

        if kwargs.get('maxLength', False):
            result['timeOut'] = str(kwargs.get('maxLength'))

        if kwargs.get('action', False):
            method = kwargs.get('method', 'post')
            result['eventMethod'] = method
            result['eventUrl'] = [kwargs.get('action')]

        self.document.append(result)
        result = dict(action='input', maxDigits=1, submitOnHash=True, timeOut=1,
                      eventUrl=["%s?save_media=1" % kwargs.get('action')])

        self.document.append(result)

        return self
