from temba.utils import json


class NCCOException(Exception):
    pass


class NCCOResponse(object):
    def __init__(self, **kwargs):
        self.document = []

    def __str__(self):

        object_len = len(self.document)
        for idx in range(object_len):
            action_dict = self.document[idx]

            if action_dict["action"] in ["talk", "stream"]:
                if idx == object_len - 1:
                    self.document[idx]["bargeIn"] = False
                elif idx <= object_len - 2:
                    next_action_dict = self.document[idx + 1]
                    if next_action_dict["action"] != "input":
                        self.document[idx]["bargeIn"] = False

        return json.dumps(self.document)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def join(self, response):
        self.document = response.document + self.document
        return self

    def say(self, text, **kwargs):
        self.document.append(dict(action="talk", text=str(text), bargeIn=True))
        return self

    def play(self, url=None, digits=None, **kwargs):
        if url is None and digits is None:
            raise NCCOException("Please specify either a url or digits to play.")

        result = dict()
        if url:
            result["action"] = "stream"
            result["streamUrl"] = [url]
            result["bargeIn"] = True

        elif digits:
            result["bargeIn"] = True
            result["action"] = "talk"
            result["text"] = digits
        self.document.append(result)
        return self

    def pause(self, **kwargs):
        return self

    def redirect(self, url=None, **kwargs):
        result = dict(
            action="input",
            maxDigits=1,
            timeOut=1,
            eventUrl=["%s%sinput_redirect=1" % (url, "?" if "?" not in url else "&")],
        )

        self.document.append(result)
        return self

    def hangup(self, **kwargs):
        return self

    def reject(self, reason=None, **kwargs):
        self.hangup()
        return self

    def gather(self, **kwargs):

        result = dict(action="input")

        if kwargs.get("action", False):
            method = kwargs.get("method", "post")
            result["eventMethod"] = method
            result["eventUrl"] = [kwargs.get("action")]

        result["submitOnHash"] = kwargs.get("finish_on_key", "#") == "#"

        if kwargs.get("num_digits", False):
            result["maxDigits"] = int(str(kwargs.get("num_digits")))

        if kwargs.get("timeout", False):
            result["timeOut"] = int(str(kwargs.get("timeout")))

        self.document.append(result)
        return self

    def record(self, **kwargs):
        result = dict(format="wav", endOnSilence=4, endOnKey="#", beepStart=True, action="record")

        if kwargs.get("max_length", False):
            result["timeOut"] = int(str(kwargs.get("max_length")))

        if kwargs.get("action", False):
            method = kwargs.get("method", "post")
            result["eventMethod"] = method
            result["eventUrl"] = [kwargs.get("action")]

        self.document.append(result)
        result = dict(
            action="input",
            maxDigits=1,
            timeOut=1,
            eventUrl=[
                "%s%ssave_media=1" % (kwargs.get("action"), "?" if "?" not in str(kwargs.get("action")) else "&")
            ],
        )

        self.document.append(result)

        return self
