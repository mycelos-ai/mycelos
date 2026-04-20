/**
 * Mycelos API Client
 *
 * Provides fetch wrappers for GET, POST, DELETE and SSE streaming.
 * All paths are relative (same origin — Gateway serves both API and frontend).
 */

/**
 * Label for a session row anywhere in the UI. Prefers the explicit title;
 * if none, formats the start time ("Apr 11, 16:04"). Only falls back to a
 * truncated ID if no timestamp is available either. Used by the Recent
 * Sessions sidebar and the admin session inspector.
 */
window.sessionLabel = function (session) {
  if (!session) return "";
  const title = (session.title || "").trim();
  if (title) return title;
  const ts = session.created_at || session.started_at || session.timestamp;
  if (ts) {
    try {
      const d = new Date(ts);
      if (!isNaN(d.getTime())) {
        return d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
          + ", "
          + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
      }
    } catch (e) { /* fall through */ }
  }
  const id = session.id || session.session_id || "";
  return id ? "Session " + id.substring(0, 8) : "Session";
};

const MycelosAPI = {

  /**
   * GET request returning parsed JSON.
   * @param {string} path - API path (e.g. "/api/sessions")
   * @returns {Promise<any>}
   */
  async get(path) {
    const res = await fetch(path, {
      headers: { 'Accept': 'application/json' },
    });
    if (!res.ok) {
      const err = await _parseError(res);
      throw new Error(err);
    }
    return res.json();
  },

  /**
   * POST request with JSON body, returning parsed JSON.
   * @param {string} path - API path
   * @param {any} data - Request body (will be JSON.stringify'd)
   * @returns {Promise<any>}
   */
  async post(path, data) {
    const res = await fetch(path, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      },
      body: JSON.stringify(data),
    });
    if (!res.ok) {
      const err = await _parseError(res);
      throw new Error(err);
    }
    return res.json();
  },

  /**
   * PUT request with JSON body, returning parsed JSON.
   */
  async put(path, data) {
    const res = await fetch(path, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      },
      body: JSON.stringify(data),
    });
    if (!res.ok) {
      const err = await _parseError(res);
      throw new Error(err);
    }
    return res.json();
  },

  /**
   * PATCH request with JSON body, returning parsed JSON.
   */
  async patch(path, data) {
    const res = await fetch(path, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      },
      body: JSON.stringify(data),
    });
    if (!res.ok) {
      const err = await _parseError(res);
      throw new Error(err);
    }
    const text = await res.text();
    return text ? JSON.parse(text) : {};
  },

  /**
   * Generic request wrapper used when the method is dynamic.
   */
  async request(method, path, data) {
    const upper = (method || 'GET').toUpperCase();
    if (upper === 'GET') return this.get(path);
    if (upper === 'POST') return this.post(path, data);
    if (upper === 'PATCH') return this.patch(path, data);
    if (upper === 'DELETE') return this.delete(path);
    throw new Error('Unsupported method: ' + method);
  },

  /**
   * DELETE request returning parsed JSON (or empty).
   * @param {string} path - API path
   * @returns {Promise<any>}
   */
  async delete(path) {
    const res = await fetch(path, {
      method: 'DELETE',
      headers: { 'Accept': 'application/json' },
    });
    if (!res.ok) {
      const err = await _parseError(res);
      throw new Error(err);
    }
    // Some DELETE endpoints return empty body
    const text = await res.text();
    return text ? JSON.parse(text) : {};
  },

  /**
   * SSE streaming via fetch (POST with streaming response).
   * Handles the Mycelos Gateway SSE format: "event: <type>\ndata: <json>\n\n"
   *
   * @param {string} path - API path (e.g. "/api/chat")
   * @param {any} data - POST body
   * @param {function} onEvent - Callback: (eventType, eventData) => void
   * @returns {Promise<void>} Resolves when stream ends
   */
  async stream(path, data, onEvent) {
    const res = await fetch(path, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
      },
      body: JSON.stringify(data),
    });

    if (!res.ok) {
      const err = await _parseError(res);
      throw new Error(err);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let currentEvent = 'message';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      // Keep incomplete last line in buffer
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim();
        } else if (line.startsWith('data: ')) {
          const raw = line.slice(6);
          let parsed;
          try {
            parsed = JSON.parse(raw);
          } catch {
            parsed = raw;
          }
          onEvent(currentEvent, parsed);
          currentEvent = 'message';
        }
        // Empty line resets — already handled by split
      }
    }

    // Process any remaining buffer
    if (buffer.trim()) {
      const remaining = buffer.split('\n');
      for (const line of remaining) {
        if (line.startsWith('data: ')) {
          const raw = line.slice(6);
          let parsed;
          try {
            parsed = JSON.parse(raw);
          } catch {
            parsed = raw;
          }
          onEvent(currentEvent, parsed);
        }
      }
    }
  },
};


/**
 * Parse error response body into a human-readable string.
 * @param {Response} res
 * @returns {Promise<string>}
 */
async function _parseError(res) {
  try {
    const body = await res.json();
    return body.error || body.detail || `HTTP ${res.status}`;
  } catch {
    return `HTTP ${res.status}: ${res.statusText}`;
  }
}

window.sidebarData = function () {
  return {
    agents: [],
    activeRuns: [],
    scheduledRuns: [],
    reminders: [],
    recentSessions: [],
    security: null,
    async loadSidebar() {
      // Load security status from health endpoint (for network access warning)
      try {
        const health = await MycelosAPI.get("/api/health");
        this.security = health?.security || null;
      } catch (e) {
        this.security = null;
      }

      // Single source of truth: backend returns only conversational agents.
      try {
        const raw = await MycelosAPI.get("/api/agents/conversational");
        this.agents = Array.isArray(raw) ? raw : [];
      } catch (e) {
        this.agents = [];
      }

      try {
        this.activeRuns = await MycelosAPI.get("/api/workflow-runs?status=active");
      } catch (e) {
        this.activeRuns = [];
      }

      try {
        this.scheduledRuns = await MycelosAPI.get("/api/workflow-runs/scheduled");
      } catch (e) {
        this.scheduledRuns = [];
      }

      try {
        this.reminders = await MycelosAPI.get("/api/reminders/upcoming");
      } catch (e) {
        this.reminders = [];
      }

      try {
        const s = await MycelosAPI.get("/api/sessions");
        const list = Array.isArray(s) ? s : (s.sessions || []);
        this.recentSessions = list.slice(0, 10);
      } catch (e) {
        this.recentSessions = [];
      }
    },

    sessionLabel(session) { return window.sessionLabel(session); },

    startNewChat() {
      if (window.location.pathname.endsWith("/pages/chat.html")) {
        window.dispatchEvent(new CustomEvent("mycelos:new-chat"));
      } else {
        window.location.href = "/pages/chat.html?new=1";
      }
    },

    selectAgentAndStart(agentId) {
      if (window.location.pathname.endsWith("/pages/chat.html")) {
        window.dispatchEvent(new CustomEvent("mycelos:new-chat", { detail: { agentId } }));
      } else {
        window.location.href = "/pages/chat.html?new=1&agent=" + encodeURIComponent(agentId);
      }
    },

    resumeWorkflowRun(runId) {
      if (window.location.pathname.endsWith("/pages/chat.html")) {
        window.dispatchEvent(new CustomEvent("mycelos:resume-run", { detail: { runId } }));
      } else {
        window.location.href = "/pages/chat.html?resume_run=" + encodeURIComponent(runId);
      }
    },

    openSession(sessionId) {
      if (window.location.pathname.endsWith("/pages/chat.html")) {
        window.dispatchEvent(new CustomEvent("mycelos:open-session", { detail: { sessionId } }));
      } else {
        window.location.href = "/pages/chat.html?session=" + encodeURIComponent(sessionId);
      }
    },

    openScheduledWorkflow(workflowId) {
      window.location.href = "/pages/workflows.html?workflow=" + encodeURIComponent(workflowId);
    },

    openReminder(notePath) {
      window.location.href = "/pages/knowledge.html?note=" + encodeURIComponent(notePath);
    },
  };
};

/**
 * Report the browser's IANA timezone to the backend so the LLM prompt
 * shows "now" in the user's local time instead of the container's UTC.
 *
 * Runs once per tz-change — stored in localStorage so we don't hit the
 * API on every page load. Fire-and-forget; a failure never blocks the UI.
 */
(function syncUserTimezone() {
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (!tz) return;
    if (localStorage.getItem("mycelos.user.timezone") === tz) return;
    fetch("/api/memory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope: "system", key: "user.timezone", value: tz }),
      credentials: "same-origin",
    }).then((r) => {
      if (r.ok) localStorage.setItem("mycelos.user.timezone", tz);
    }).catch(() => {});
  } catch (e) {
    // Intl unsupported on very old browsers — skip.
  }
})();
