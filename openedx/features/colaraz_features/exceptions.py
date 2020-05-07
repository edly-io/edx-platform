"""
Exceptions Custom to colaraz.
"""


class HttpBadRequest(Exception):
    def __init__(self, message='', *args, **kwargs):
        self.message = message
