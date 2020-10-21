from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from lms.djangoapps.mobile_api.decorators import mobile_view
from openedx.features.edly.api.serializers import UserSiteSerializer


@mobile_view()
class UserSitesViewSet(viewsets.ViewSet):
    permission_classes = (IsAuthenticated,)
    serializer = UserSiteSerializer

    def list(self, request, *args, **kwargs):
        user = request.user
        edly_sub_orgs_of_user = user.edly_profile.edly_sub_organizations

        context = {
            'request': request,
        }

        user_sites = []
        for edly_sub_org_of_user in edly_sub_orgs_of_user.all():
            context['edly_sub_org_of_user'] = edly_sub_org_of_user
            serializer = self.serializer({}, context=context)
            user_sites.append(
                serializer.data
            )

        return Response(user_sites)
