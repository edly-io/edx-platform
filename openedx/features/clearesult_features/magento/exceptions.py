"""
Magento-related exceptions.
"""


class InvalidMagentoResponseError(Exception):
    """
    Exception raised when an Magento API response is invalid.
    """
    pass


class MissingMagentoUserKey(Exception):
    """
    Exception raised when client unable to get costomer token from Magento.
    """
    pass
