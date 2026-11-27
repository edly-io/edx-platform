// Behaviour jQuery 1.9 removed that jquery-migrate 1 restored and jquery-migrate 3 does not.
// Loaded right after jquery-migrate in the LMS and Studio base_vendor_js.
(function($) {
    'use strict';

    // $.browser: vendor plugins still read it (qtip, ba-bbq, markitup, flot.pie) only to detect IE,
    // old Opera or Safari. An empty object makes those checks false instead of throwing.
    $.browser = $.browser || {};

    // .attr('value'): jQuery 1.9+ reads and writes only the HTML attribute, which the browser stops
    // showing once the field has been edited (by the user or by .val()). The timepicker sets the
    // picked time this way, and Studio always .val()s those fields first. Ported from jquery-migrate
    // 1.4.1: also read and write the live .value, except on <button>, which keeps the attribute.
    $.attrHooks.value = {
        get: function(elem, name) {
            if (elem.nodeName.toLowerCase() === 'button') {
                return null;
            }
            return name in elem ? elem.value : null;
        },
        set: function(elem, value) {
            if (elem.nodeName.toLowerCase() !== 'button') {
                elem.value = value;
            }
            // No return value, so jQuery also sets the attribute.
        }
    };
}(jQuery));
