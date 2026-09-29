/* Uber Learn XBlock bridge — injected only when is_learning_mfe=true */
(function () {
  'use strict';

  // -------------------------------------------------------------------------
  // Helpers
  // -------------------------------------------------------------------------

  var PARENT_ORIGIN = (function () {
    try {
      if (document.referrer) { return new URL(document.referrer).origin; }
    } catch (e) { /* ignore */ }
    return window.location.origin;
  }());

  function send(type, payload) {
    if (window === window.parent) { return; } // not in iframe — skip
    window.parent.postMessage(
      { type: type, version: 1, payload: payload !== undefined ? payload : null },
      PARENT_ORIGIN
    );
  }

  // Guard: fire plugin.completed at most once per page load to prevent duplicate signals.
  var completedSent = false;
  function sendCompleted(correct) {
    if (completedSent) { return; }
    completedSent = true;
    send('plugin.completed', { correct: correct });
  }

  // -------------------------------------------------------------------------
  // CAPA / Problem XBlock — hook via jQuery ajaxComplete
  //
  // CAPA submits to:
  //   /courses/{id}/xblock/{usage_key}/handler/xmodule_handler/problem_check
  //   /courses/{id}/xblock/{usage_key}/handler/xmodule_handler/problem_save
  //
  // The JSON response contains: { "success": "correct"|"incorrect"|"partially-correct" }
  // -------------------------------------------------------------------------

  function hookCapa() {
    if (typeof $ === 'undefined') { return; }
    $(document).on('ajaxComplete', function (event, xhr, settings) {
      if (!settings || !settings.url) { return; }
      var url = settings.url;
      if (url.indexOf('problem_check') === -1 && url.indexOf('problem_save') === -1) { return; }
      try {
        var data = JSON.parse(xhr.responseText);
        if (!data || typeof data.success === 'undefined') { return; }
        // success: 'correct' | 'partially-correct' | 'incorrect' | false | true
        var correct = (data.success === 'correct' || data.success === true);
        sendCompleted(correct);
      } catch (e) { /* non-JSON response */ }
    });
  }

  // -------------------------------------------------------------------------
  // DnD v2 XBlock — hook via jQuery ajaxComplete on do_attempt
  //
  // DnD v2 submits to:
  //   /courses/{id}/xblock/{usage_key}/handler/do_attempt
  //
  // Response: { "correct": true|false, "finished": true|false, ... }
  // We only fire plugin.completed when finished=true.
  // -------------------------------------------------------------------------

  function hookDragAndDrop() {
    if (typeof $ === 'undefined') { return; }
    $(document).on('ajaxComplete', function (event, xhr, settings) {
      if (!settings || !settings.url) { return; }
      if (settings.url.indexOf('do_attempt') === -1) { return; }
      try {
        var data = JSON.parse(xhr.responseText);
        if (!data || !data.finished) { return; }
        sendCompleted(Boolean(data.correct));
      } catch (e) { /* non-JSON */ }
    });
  }

  // -------------------------------------------------------------------------
  // Sortable XBlock — hook via jQuery ajaxComplete on check_answer
  //
  // Sortable submits to:
  //   /courses/{id}/xblock/{usage_key}/handler/check_answer
  //
  // Response: { "result": "correct"|"incorrect", "score": number }
  // -------------------------------------------------------------------------

  function hookSortable() {
    if (typeof $ === 'undefined') { return; }
    $(document).on('ajaxComplete', function (event, xhr, settings) {
      if (!settings || !settings.url) { return; }
      if (settings.url.indexOf('check_answer') === -1) { return; }
      try {
        var data = JSON.parse(xhr.responseText);
        if (!data || typeof data.result === 'undefined') { return; }
        var correct = (data.result === 'correct' || data.result === true);
        sendCompleted(correct);
      } catch (e) { /* non-JSON */ }
    });
  }

  // -------------------------------------------------------------------------
  // Video XBlock — hook into HTML5 video or videojs player
  // -------------------------------------------------------------------------

  function hookVideo() {
    // Native <video> ended event
    var vid = document.querySelector('video');
    if (vid) {
      vid.addEventListener('ended', function () {
        send('plugin.videoEnded', null);
      }, { once: true });
    }

    // videojs player (the LMS bundles videojs as window.videojs)
    if (window.videojs) {
      try {
        var getAllPlayers = window.videojs.getAllPlayers || window.videojs.getPlayers;
        var players = getAllPlayers ? Object.values(getAllPlayers()) : [];
        players.forEach(function (player) {
          if (player && typeof player.on === 'function') {
            player.on('ended', function () {
              send('plugin.videoEnded', null);
            });
          }
        });
      } catch (e) { /* ignore */ }
    }
  }

  // -------------------------------------------------------------------------
  // View-only XBlocks (HTML, Resource list)
  // Auto-complete after load if no interactive element is present.
  // -------------------------------------------------------------------------

  function autoCompleteViewOnly() {
    var hasProblem = document.querySelector('.problem-core, .problem, [data-block-type="problem"]');
    var hasVideo   = document.querySelector('video, .video, [data-block-type="video"]');
    var hasDnD     = document.querySelector('.drag-and-drop-v2, [data-block-type="drag-and-drop-v2"]');
    var hasSortable = document.querySelector('.sortable-xblock, [data-block-type="sortable"]');

    if (!hasProblem && !hasVideo && !hasDnD && !hasSortable) {
      // Pure read/resource content — mark complete immediately
      sendCompleted(null);
    }
  }

  // -------------------------------------------------------------------------
  // Listen for uber.continueClicked — let the MFE trigger problem submit
  // -------------------------------------------------------------------------

  window.addEventListener('message', function (event) {
    if (event.source !== window.parent) { return; }
    if (!event.data || event.data.type !== 'uber.continueClicked') { return; }
    // Try common submit button selectors for CAPA
    var submitBtn = document.querySelector(
      '[data-uber-submit], ' +
      '.submit.btn-brand, ' +
      'input[type="submit"].submit, ' +
      '.problem .action button.submit'
    );
    if (submitBtn) { submitBtn.click(); }
  });

  // -------------------------------------------------------------------------
  // Explicit API (for custom XBlocks that want manual control)
  // -------------------------------------------------------------------------

  window.uberLearn = {
    onVideoEnded:    function ()         { send('plugin.videoEnded', null); },
    onCompleted:     function (correct)  { sendCompleted(Boolean(correct)); },
    onViewCompleted: function ()         { sendCompleted(null); },
  };

  // -------------------------------------------------------------------------
  // Boot
  // -------------------------------------------------------------------------

  function init() {
    hookCapa();
    hookDragAndDrop();
    hookSortable();
    hookVideo();
    autoCompleteViewOnly();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

}());
