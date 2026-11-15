/**
 * Sanitized HTML for every `x-html` / `innerHTML` sink in the Web UI.
 *
 * Notes, chat replies and workflow results contain text that came from
 * outside: imported documents, web pages, e-mails, LLM output. Rendering
 * that as raw HTML lets an `<img onerror=…>` in a note run script with the
 * user's session (stored XSS). Every page therefore renders Markdown and
 * any other HTML through this module, which runs DOMPurify last.
 *
 * Fail-closed: when DOMPurify is not loaded, `sanitize()` returns the
 * input as escaped text instead of HTML.
 *
 * Requires /shared/vendor/purify-*.min.js (and marked for `markdown()`).
 */
(function () {
  'use strict';

  function escapeHtml(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  /** Turn HTML entities back into plain text (e.g. "&amp;" → "&").
   *  A <textarea> parses its content as text, so no markup is created. */
  function decodeEntities(text) {
    const el = document.createElement('textarea');
    el.innerHTML = String(text == null ? '' : text);
    return el.value;
  }

  const PURIFY_CONFIG = {
    // Keep data-* attributes (data-cmd, data-note, …) — DOMPurify allows
    // them by default; links may open in a new tab.
    ADD_ATTR: ['target'],
    // No forms or embedded documents in rendered content.
    FORBID_TAGS: ['style', 'form', 'iframe', 'object', 'embed', 'base', 'meta', 'link'],
    FORBID_ATTR: ['formaction', 'action', 'srcdoc'],
  };

  let hookInstalled = false;
  function installHook() {
    if (hookInstalled || typeof DOMPurify === 'undefined') return;
    // A link that opens a new tab must not get a handle on this window.
    DOMPurify.addHook('afterSanitizeAttributes', function (node) {
      if (node.tagName === 'A' && node.getAttribute('target')) {
        node.setAttribute('target', '_blank');
        node.setAttribute('rel', 'noopener noreferrer');
      }
    });
    hookInstalled = true;
  }

  function sanitize(html) {
    if (html == null || html === '') return '';
    if (typeof DOMPurify === 'undefined' || typeof DOMPurify.sanitize !== 'function') {
      return escapeHtml(html).replace(/\n/g, '<br>');
    }
    installHook();
    return DOMPurify.sanitize(String(html), PURIFY_CONFIG);
  }

  /** Markdown → HTML without sanitizing. Only for callers that post-process
   *  the HTML and then pass the final string through `sanitize()`. */
  function markdownUnsafe(text) {
    if (!text) return '';
    if (typeof marked !== 'undefined' && typeof marked.parse === 'function') {
      try {
        return marked.parse(String(text));
      } catch (e) {
        /* fall through to plain text */
      }
    }
    return escapeHtml(text).replace(/\n/g, '<br>');
  }

  function markdown(text) {
    return sanitize(markdownUnsafe(text));
  }

  window.MycelosSafeHtml = Object.freeze({
    escape: escapeHtml,
    decodeEntities: decodeEntities,
    sanitize: sanitize,
    markdown: markdown,
    markdownUnsafe: markdownUnsafe,
  });
})();
