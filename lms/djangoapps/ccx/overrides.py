"""
API related to providing field overrides for individual students.  This is used
by the individual custom courses feature.
"""
import json
import logging

from django.db import transaction, IntegrityError

from courseware.field_overrides import FieldOverrideProvider  # pylint: disable=import-error
from opaque_keys.edx.keys import CourseKey, UsageKey
from ccx_keys.locator import CCXLocator, CCXBlockUsageLocator

from .models import CcxMembership, CcxFieldOverride, CustomCourseForEdX


log = logging.getLogger(__name__)


class CustomCoursesForEdxOverrideProvider(FieldOverrideProvider):
    """
    A concrete implementation of
    :class:`~courseware.field_overrides.FieldOverrideProvider` which allows for
    overrides to be made on a per user basis.
    """
    def get(self, block, name, default):
        """
        Just call the get_override_for_ccx method if there is a ccx
        """
        # The incoming block might be a CourseKey instance of some type, a
        # UsageKey instance of some type, or it might be something that has a
        # location attribute.  That location attribute will be a UsageKey
        ccx = course_id = None
        identifier = getattr(block, 'id', None)
        if isinstance(identifier, CourseKey):
            course_id = block.id
        elif isinstance(identifier, UsageKey):
            course_id = block.id.course_key
        elif hasattr(block, 'location'):
            course_id = block.location.course_key
        else:
            msg = "Unable to get course id when calculating ccx overide for block type {}"
            log.error(msg.format(type(block)))
        if course_id is not None:
            ccx = get_current_ccx(course_id)
        if ccx:
            return get_override_for_ccx(ccx, block, name, default)
        return default


def get_current_ccx(course_id):
    """
    Return the ccx that is active for this course.
    """
    # ensure that the ID passed in is a CourseKey instance of some type.
    if isinstance(course_id, CourseKey):
        course_key = course_id
    else:
        course_key = CourseKey.from_string(course_id)

    ccx = None
    if isinstance(course_key, CCXLocator):
        ccx_id = course_key.ccx
        ccx = CustomCourseForEdX.objects.get(pk=ccx_id)
    return ccx


def get_override_for_ccx(ccx, block, name, default=None):
    """
    Gets the value of the overridden field for the `ccx`.  `block` and `name`
    specify the block and the name of the field.  If the field is not
    overridden for the given ccx, returns `default`.
    """
    if not hasattr(block, '_ccx_overrides'):
        block._ccx_overrides = {}  # pylint: disable=protected-access
    overrides = block._ccx_overrides.get(ccx.id)  # pylint: disable=protected-access
    if overrides is None:
        overrides = _get_overrides_for_ccx(ccx, block)
        block._ccx_overrides[ccx.id] = overrides  # pylint: disable=protected-access
    return overrides.get(name, default)


def _get_overrides_for_ccx(ccx, block):
    """
    Returns a dictionary mapping field name to overriden value for any
    overrides set on this block for this CCX.
    """
    overrides = {}
    # block as passed in may have a location specific to a CCX, we must strip
    # that for this query
    location = block.location
    if isinstance(block.location, CCXBlockUsageLocator):
        location = block.location.to_block_locator()
    query = CcxFieldOverride.objects.filter(
        ccx=ccx,
        location=location
    )
    for override in query:
        field = block.fields[override.field]
        value = field.from_json(json.loads(override.value))
        overrides[override.field] = value
    return overrides


@transaction.commit_on_success
def override_field_for_ccx(ccx, block, name, value):
    """
    Overrides a field for the `ccx`.  `block` and `name` specify the block
    and the name of the field on that block to override.  `value` is the
    value to set for the given field.
    """
    field = block.fields[name]
    value = json.dumps(field.to_json(value))
    try:
        override = CcxFieldOverride.objects.create(
            ccx=ccx,
            location=block.location,
            field=name,
            value=value)
    except IntegrityError:
        transaction.commit()
        override = CcxFieldOverride.objects.get(
            ccx=ccx,
            location=block.location,
            field=name)
        override.value = value
    override.save()
    if hasattr(block, '_ccx_overrides'):
        del block._ccx_overrides[ccx.id]  # pylint: disable=protected-access


def clear_override_for_ccx(ccx, block, name):
    """
    Clears a previously set field override for the `ccx`.  `block` and `name`
    specify the block and the name of the field on that block to clear.
    This function is idempotent--if no override is set, nothing action is
    performed.
    """
    try:
        CcxFieldOverride.objects.get(
            ccx=ccx,
            location=block.location,
            field=name).delete()

        if hasattr(block, '_ccx_overrides'):
            del block._ccx_overrides[ccx.id]  # pylint: disable=protected-access

    except CcxFieldOverride.DoesNotExist:
        pass
