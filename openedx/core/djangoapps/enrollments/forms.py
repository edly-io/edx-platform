"""
Forms for validating user input to the Course Enrollment related views.
"""


from django.core.exceptions import ValidationError
from django.forms import CharField, Form
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.user_authn.views.registration_form import validate_username


class CourseEnrollmentsApiListForm(Form):
    """
    A form that validates the query string parameters for the CourseEnrollmentsApiListView.

    ADR 0033 – OEP-68 parameter naming standardization:
    - ``course_key`` is the preferred parameter name; ``course_id`` is accepted
      as a deprecated alias (BC strategy §1).  When both are present,
      ``course_key`` wins.
    - ``course_keys`` is the preferred parameter name; ``course_ids`` is
      accepted as a deprecated alias (same precedence rule).
    Internally the cleaned_data continues to expose ``course_id`` /
    ``course_ids`` so call sites do not need to change.  Use
    :meth:`legacy_param_aliases_used` to detect when the deprecated names were
    sent by the client (used to emit the ``Deprecation`` HTTP header).
    """
    MAX_INPUT_COUNT = 100
    # Legacy / OEP-68 alias pairs: (legacy, preferred).
    _LEGACY_PARAM_ALIASES = (
        ("course_id", "course_key"),
        ("course_ids", "course_keys"),
    )

    username = CharField(required=False)
    course_id = CharField(required=False)
    course_key = CharField(required=False)
    course_ids = CharField(required=False)
    course_keys = CharField(required=False)
    email = CharField(required=False)

    def __init__(self, query_params, *args, **kwargs):
        # Capture the raw param names supplied on the wire, *before* Django's
        # form layer resolves aliases, so :meth:`legacy_param_aliases_used`
        # can later report exactly which legacy names were used.
        try:
            raw_keys = set(query_params.keys())
        except AttributeError:
            raw_keys = set()
        self._raw_param_names = raw_keys

        # Coalesce OEP-68 preferred names into the legacy fields so the
        # downstream view code keeps reading ``course_id`` / ``course_ids``
        # without changes.  Preferred wins when both are sent.
        if hasattr(query_params, "copy"):
            data = query_params.copy()
        else:
            data = dict(query_params)
        for legacy_name, preferred_name in self._LEGACY_PARAM_ALIASES:
            preferred_value = data.get(preferred_name)
            if preferred_value:
                data[legacy_name] = preferred_value

        super().__init__(data, *args, **kwargs)

    def legacy_param_aliases_used(self):
        """
        Return the list of legacy (OEP-68-violating) parameter names actually
        present in the request, in declaration order.

        Used by the view layer to emit the ADR 0033 ``Deprecation`` header.
        """
        return [
            legacy for legacy, _preferred in self._LEGACY_PARAM_ALIASES
            if legacy in self._raw_param_names
        ]

    def clean_course_id(self):
        """
        Validate and return a course ID.
        """
        course_id = self.cleaned_data.get('course_id')
        if course_id:
            try:
                return CourseKey.from_string(course_id)
            except InvalidKeyError:
                raise ValidationError(f"'{course_id}' is not a valid course id.")  # lint-amnesty, pylint: disable=raise-missing-from
        return course_id

    def clean_username(self):
        """
        Validate a string of comma-separated usernames and return a list of usernames.
        """
        usernames_csv_string = self.cleaned_data.get('username')
        if usernames_csv_string:
            usernames = usernames_csv_string.split(',')
            if len(usernames) > self.MAX_INPUT_COUNT:
                raise ValidationError(
                    "Too many usernames in a single request - {}. A maximum of {} is allowed".format(
                        len(usernames),
                        self.MAX_INPUT_COUNT,
                    )
                )
            for username in usernames:
                validate_username(username)
            return usernames
        return usernames_csv_string

    def clean_course_ids(self):
        """
        Validate a string of comma-separated course IDs and return a list of course IDs.
        """
        course_ids_csv_string = self.cleaned_data.get('course_ids')
        if course_ids_csv_string:
            course_ids = course_ids_csv_string.split(',')
            if len(course_ids) > self.MAX_INPUT_COUNT:
                raise ValidationError(
                    "Too many course_ids in a single request - {}. A maximum of {} is allowed".format(
                        len(course_ids),
                        self.MAX_INPUT_COUNT,
                    )
                )
            return course_ids

        return course_ids_csv_string

    def clean_email(self):
        """
        Validate a string of comma-separated emails and return a list of emails.
        """
        emails_csv_string = self.cleaned_data.get('email')
        if emails_csv_string:
            emails = emails_csv_string.split(',')
            if len(emails) > self.MAX_INPUT_COUNT:
                raise ValidationError(
                    "Too many emails in a single request - {}. A maximum of {} is allowed".format(
                        len(emails),
                        self.MAX_INPUT_COUNT,
                    )
                )
            return emails
        return emails_csv_string
