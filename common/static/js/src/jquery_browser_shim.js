// jQuery 1.9 removed $.browser, and jquery-migrate 3 (unlike 1.x) does not restore it.
// Vendor plugins still read it (qtip, ba-bbq, markitup, flot.pie, bootstrap typeahead), only to
// detect IE, old Opera or Safari: an empty object makes all of those checks false instead of throwing.
(function($) {
    'use strict';

    $.browser = $.browser || {};
}(jQuery));
