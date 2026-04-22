/**
 * OAuth setup helpers — shared across connectors that use the
 * `oauth_browser` setup flow. Everything here is state-less; callers
 * bring their own Alpine reactive state.
 */
(function () {
  'use strict';

  const OAUTH_URL_PATTERNS = [
    /https:\/\/accounts\.google\.com\/o\/oauth2\/[^\s"'<>]+/g,
    /https:\/\/login\.microsoftonline\.com\/[^\s"'<>]+\/oauth2\/[^\s"'<>]+/g,
    // add more providers here as new recipes land
  ];

  /**
   * Scan a chunk of stdout for an OAuth consent URL. Returns the URL
   * or null. First hit wins — most upstream tools print exactly one.
   */
  function findOAuthUrl(text) {
    for (const pat of OAUTH_URL_PATTERNS) {
      const match = text.match(pat);
      if (match && match[0]) return match[0];
    }
    return null;
  }

  /**
   * Open a WebSocket to the given path and wire up per-frame callbacks.
   * Returns an object with .send(frame) and .close().
   */
  function openOAuthStream(wsPath, { onStdout, onStderr, onDone, onError }) {
    const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = scheme + '//' + window.location.host + wsPath;
    const ws = new WebSocket(url);
    ws.onmessage = (event) => {
      let frame;
      try { frame = JSON.parse(event.data); } catch { return; }
      if (frame.type === 'stdout') onStdout && onStdout(frame.data);
      else if (frame.type === 'stderr') onStderr && onStderr(frame.data);
      else if (frame.type === 'done') onDone && onDone(frame.exit_code);
    };
    ws.onerror = (e) => onError && onError(e);
    return {
      send(frame) {
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(frame));
      },
      close() {
        try { ws.close(); } catch (_) {}
      },
    };
  }

  window.MycelosOAuthSetup = { findOAuthUrl, openOAuthStream };
})();
