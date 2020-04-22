"""
Authenitcation and Social Auth Pipeline methods for Colaraz's customizations
"""

def store_id_token(request, response, user=None, *args, **kwargs):
    """
    This method is used in SOCIAL_AUTH_PIPELINE. It stores 'id_token' from the User's
    data sent by Auth Provider to request's 'session' object.
    """
    
    if user and response.has_key('id_token'):
        request.session['id_token'] = response['id_token']
