/**
 * Mycelos Layout — Alpine.js initialization and fragment loading.
 *
 * Determines the active page from the URL, loads shared HTML fragments
 * (sidebar, header, mobile-nav) into their containers, and provides
 * global Alpine.js state.
 */

/**
 * Detect the active page name from the current URL path.
 * Maps /pages/chat.html -> "chat", /pages/dashboard.html -> "dashboard", etc.
 * Falls back to "chat" for the root or unknown paths.
 */
function detectActivePage() {
  const path = window.location.pathname;
  const match = path.match(/\/pages\/(\w+)\.html/);
  if (match) return match[1];
  // Root redirects to chat
  return 'chat';
}

/**
 * Load an HTML fragment into a container element.
 * @param {string} url - Path to the HTML fragment
 * @param {string} containerId - ID of the target container element
 */
async function loadFragment(url, containerId) {
  const container = document.getElementById(containerId);
  if (!container) return;

  try {
    const res = await fetch(url);
    if (res.ok) {
      const html = await res.text();
      container.innerHTML = html;
      // innerHTML does NOT execute <script> tags — re-create them manually
      // so Quick Capture and similar plain-JS fragments wire up.
      container.querySelectorAll('script').forEach((oldScript) => {
        const newScript = document.createElement('script');
        for (const attr of oldScript.attributes) {
          newScript.setAttribute(attr.name, attr.value);
        }
        newScript.textContent = oldScript.textContent;
        oldScript.parentNode.replaceChild(newScript, oldScript);
      });
    } else {
      console.warn('Failed to load fragment:', url, res.status);
    }
  } catch (err) {
    console.warn('Error loading fragment:', url, err);
  }
}

/**
 * Main Alpine.js component for the Mycelos app shell.
 * Every page wraps its body with x-data="mycelosApp()".
 */
function mycelosApp() {
  return {
    activePage: detectActivePage(),
    userName: 'User',
    sidebarOpen: false,

    async init() {
      // Load shared layout fragments in parallel
      await Promise.all([
        loadFragment('/shared/sidebar.html', 'sidebar'),
        loadFragment('/shared/header.html', 'header'),
        loadFragment('/shared/mobile-nav.html', 'mobile-nav'),
        loadFragment('/shared/quick-capture.html', 'quick-capture'),
      ]);

      // Re-initialize Alpine.js on the injected fragments so that
      // x-bind, x-text, :class etc. become reactive. Quick Capture is
      // plain JS (no Alpine), so it is deliberately excluded here.
      this.$nextTick(() => {
        const containers = ['sidebar', 'header', 'mobile-nav'];
        containers.forEach(id => {
          const el = document.getElementById(id);
          if (el && el.firstElementChild) {
            Alpine.initTree(el);
          }
        });
      });
    },
  };
}
