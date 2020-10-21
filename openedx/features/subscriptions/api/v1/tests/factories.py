from datetime import date
import factory
from factory import SelfAttribute, Sequence, SubFactory
from factory.fuzzy import FuzzyChoice, FuzzyDate, FuzzyInteger
from factory.django import DjangoModelFactory

from openedx.features.subscriptions.models import UserSubscription
from student.tests.factories import UserFactory


class UserSubscriptionFactory(DjangoModelFactory):
    """
    Factory class for "UserSubscription" model.
    """
    class Meta(object):
        model = UserSubscription

    user = SubFactory(UserFactory)
    subscription_id = FuzzyInteger(1, 10)
    max_allowed_courses = FuzzyInteger(1, 10)
    expiration_date = FuzzyDate(start_date=date.today(), end_date=date.today())
    subscription_type = UserSubscription.LIMITED_ACCESS

    @factory.post_generation
    def course_enrollments(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted:
            for course_enrollment in extracted:
                self.course_enrollments.add(course_enrollment)
