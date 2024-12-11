# Exceptions used internally by the service, typically for anticipated exceptions.
# Knowledge of Flask, HTTP codes, and formatting of the Response should be
# closer to the endpoing @app.route() methods rather than throughout service.
class UKVConfigurationException(Exception):
    """Exception raised when problems loading the service configuration are encountered."""
    def __init__(self, message='There were problems loading the configuration for the service.'):
        self.message = message
        super().__init__(self.message)

class UKVKeyFormatException(Exception):
    """Exception raised when a string presented as a Key is not correctly formatted."""
    def __init__(self, message='The key is not properly formatted.'):
        self.message = message
        super().__init__(self.message)

class UKVKeyNotFoundException(Exception):
    """Exception raised when a valid Key is not found for the User."""
    def __init__(self, message='Key not found for this user.'):
        self.message = message
        super().__init__(self.message)

class UKVValueFormatException(Exception):
    """Exception raised when a string presented as a Value is not correctly formatted as JSON."""
    def __init__(self, message='The value is not properly formatted JSON.'):
        self.message = message
        super().__init__(self.message)

class UKVRequestFormatException(Exception):
    """Exception raised when the Request format is not supported by the service."""
    def __init__(self, message='The Request is not supported by this service.'):
        self.message = message
        super().__init__(self.message)

class UKVDataStoreQueryException(Exception):
    """Exception raised when the service fails to work with a data store like MySQL."""
    def __init__(self, message='There was a problem accessing the data.'):
        self.message = message
        super().__init__(self.message)

class UKVWorkerException(Exception):
    """Exception raised when a worker class used by the service fails."""
    def __init__(self, message='There was an internal problem with this service.'):
        self.message = message
        super().__init__(self.message)

class UKVBadKeyListException(Exception):
    def __init__(self, message='Invalid keys specified in the JSON list.', data='{}'):
        super().__init__(message)
        self.data = data

class UKVRequestedKeysNotFoundException(Exception):
    def __init__(self, message='Keys specified in the JSON list were not found.', data='{}'):
        super().__init__(message)
        self.data = data
