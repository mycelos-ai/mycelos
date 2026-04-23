/**
 * OAuth setup helpers. The flow is entirely HTTP-driven now — no
 * WebSocket, no subprocess stream. Keeps a small helper for reading
 * query params on page load so the connectors page can react to
 * ?connected=<recipe_id> and ?oauth_error=<msg>.
 */
(function () {
  'use strict';

  function readOAuthQueryParams() {
    const params = new URLSearchParams(window.location.search);
    return {
      connected: params.get('connected'),
      error: params.get('oauth_error'),
    };
  }

  /**
   * Remove the OAuth-result params from the URL without reloading.
   * Called by the page after it has consumed the result so that a
   * reload doesn't re-trigger the success/error panel.
   */
  function clearOAuthQueryParams() {
    const url = new URL(window.location.href);
    url.searchParams.delete('connected');
    url.searchParams.delete('oauth_error');
    window.history.replaceState({}, '', url.toString());
  }

  window.MycelosOAuthSetup = { readOAuthQueryParams, clearOAuthQueryParams };
})();
