"""
Custom form fields for colaraz.
"""
from django import forms

from openedx.features.colaraz_features.helpers import validate_course_id


class MultipleChoiceCourseIdField(forms.MultipleChoiceField):
    """
    Custom multi select course-id field.
    """
    def __init__(self, is_dynamic=False, *args, **kwargs):
        """
        `is_dynamic` is used when this field is used as multi-course-id input field and options list is empty
        and you want to skip the validations.
        """
        self.is_dynamic = is_dynamic
        super(MultipleChoiceCourseIdField, self).__init__(*args, **kwargs)

    def to_python(self, value):
        course_ids = super(MultipleChoiceCourseIdField, self).to_python(value)
        return [validate_course_id(course_id) for course_id in course_ids]

    def validate(self, value):
        """
        Skip validations if this field is used as multi-course-id input field via the `is_dynamic` flag.
        """
        if self.is_dynamic:
            # Skip validations
            pass
        else:
            super(MultipleChoiceCourseIdField, self).validate(value)
