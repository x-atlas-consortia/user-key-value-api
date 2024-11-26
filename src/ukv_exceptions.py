class UKVConfigurationException(Exception):
    """Exception raised when problems loading the service configuration are encountered."""
    def __init__(self, message="There we problems loading the configuration for the service. See logs."):
        self.message = message
        super().__init__(self.message)

class UKVKeyFormatException(Exception):
    """Exception raised when a string presented as a Key is not correctly formatted."""
    def __init__(self, message="The key is not properly formatted. See logs."):
        self.message = message
        super().__init__(self.message)

class UKVKeyNotFoundException(Exception):
    """Exception raised when a valid Key is not found for the User."""
    def __init__(self, message="Key not found for this user. See logs."):
        self.message = message
        super().__init__(self.message)

class UKVValueFormatException(Exception):
    """Exception raised when a string presented as a Value is not correctly formatted as JSON."""
    def __init__(self, message="The value is not properly formatted JSON. See logs."):
        self.message = message
        super().__init__(self.message)

class UKVRequestFormatException(Exception):
    """Exception raised when the Request format is not supported by the service."""
    def __init__(self, message="The Request is not supported by this service. See logs."):
        self.message = message
        super().__init__(self.message)

class UKVDataStoreQueryException(Exception):
    """Exception raised when the service fails to work with a data store like MySQL."""
    def __init__(self, message="There was a problem accessing the data. See logs."):
        self.message = message
        super().__init__(self.message)

class UKVWorkerException(Exception):
    """Exception raised when a worker class used by the service fails."""
    def __init__(self, message="There was an internal problem with this service. See logs."):
        self.message = message
        super().__init__(self.message)