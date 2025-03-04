"""
Views for user sites API
"""
import urllib.parse

from rest_framework import permissions, viewsets
from rest_framework.response import Response
from rest_framework.authentication import SessionAuthentication
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.db.models import Case, IntegerField, When, Value
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication

from openedx.features.edly.api.serializers import MutiSiteAccessSerializer
from openedx.core.lib.api.authentication import BearerAuthentication
from openedx.features.edly.models import EdlySubOrganization, EdlyMultiSiteAccess
from openedx.features.edly.utils import get_edly_sub_org_from_request
from openedx.features.edly.api.v1.helper import get_users_for_site


class MultisitesViewset(viewsets.ViewSet):
    """
    **Use Case**

        Get information about the current user's linked sites using email.

    **Example Request**

        GET /api/v1/user_link_sites/?email=<edx%40example.com>

    **Response Values**

        If the request is successful, the request returns an HTTP 200 "OK" response.

        The HTTP 200 response has the following values.

        * list of sub-organization linked with your email

    """
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [BearerAuthentication, SessionAuthentication]

    def list(self, request, *args, **kwargs):
        """
        Returns a list of Site linked with the user email
        """
        email = request.GET.get("email", "")
        if email:
            queryset = EdlyMultiSiteAccess.objects.filter(user__email=urllib.parse.unquote(email))
        else:
            sub_org = get_edly_sub_org_from_request(request)
            queryset = EdlyMultiSiteAccess.objects.filter(
                        user=request.user,
                        sub_org__edly_organization=sub_org.edly_organization,
                    ).annotate(
                        priority=Case(
                            When(sub_org=sub_org, then=Value(0)),
                            default=Value(1),
                            output_field=IntegerField(),
                        )
                    ).order_by("priority")

        serializer = MutiSiteAccessSerializer(queryset, many=True)
        return Response(serializer.data)


class EdlySiteUsersViewSet(viewsets.ViewSet):
    """
    **Use Case**

        Get information about users in the current site.

    **Example Request**

        GET /api/v1/site_users/

    **Response Values**

        If the request is successful, the request returns an HTTP 200 "OK" response.

        The HTTP 200 response has the following values.

        * list of users in the current site
    """
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [BearerAuthentication, SessionAuthentication]

    def list(self, request, *args, **kwargs):
        """
        Returns a list of users in the current site
        """
        sub_org = EdlySubOrganization.objects.filter(slug=request.GET.get('sub_org', ''))
        if not sub_org.exists(): 
            return Response({"error": "Sub Organization not found", 'results':[]}, status=400)

        sub_org = sub_org.first()
        users = get_users_for_site(sub_org).filter(site_count=1)
        return Response({'results':users, 'success':True}, status=200)  


class EdlySiteDeletionViewSet(viewsets.ViewSet):
    """Deletion of current site and linked user."""
    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = [JwtAuthentication, SessionAuthentication, BearerAuthentication]

    def post(self, request):
        """
        POST /api/v1/delete_site/
        """
        try:
            site_domain = request.data.get('delete_site_url', '')
            site = Site.objects.get(domain=site_domain)
            call_command('delete_cloud_site', site=site.domain)
            return Response({'success':'LMS site deletion was successful'}, status=200)
        except Exception as e:
            return Response({'error':str(e), 'success':False}, status=400)
