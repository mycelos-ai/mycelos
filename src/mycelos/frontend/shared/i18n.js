/**
 * Mycelos i18n — loads translations from /api/i18n and provides t() globally.
 *
 * Usage in Alpine templates:  x-text="t('sidebar.knowledge')"
 * Usage in plain JS:          t('common.error')
 *
 * Translations are loaded SYNCHRONOUSLY at script parse time so they are
 * guaranteed to be available before Alpine processes any x-text directives.
 */

let _translations = {};
let _lang = 'en';

// Load translations synchronously — blocks page render briefly but guarantees
// that t() returns real translations (not raw keys) from the very first call.
try {
  const xhr = new XMLHttpRequest();
  xhr.open('GET', '/api/i18n', false);  // synchronous
  xhr.send();
  if (xhr.status === 200) {
    const data = JSON.parse(xhr.responseText);
    _translations = data.translations || {};
    _lang = data.lang || 'en';
  }
} catch (err) {
  console.warn('Failed to load translations:', err);
}

/**
 * Resolve a dot-separated key against the translations object.
 * Returns the key itself if not found (makes missing translations visible).
 *
 * Variables use the same {name} syntax as the YAML files and the Python t().
 *
 * @param {string} key - Dot-separated key, e.g. "sidebar.knowledge"
 * @param {Object} [vars] - Values for {name} placeholders
 * @returns {string}
 */
function t(key, vars) {
  const parts = key.split('.');
  let node = _translations;
  for (const part of parts) {
    if (node == null || typeof node !== 'object') return key;
    node = node[part];
  }
  if (typeof node !== 'string') return key;
  if (!vars) return node;
  return node.replace(/\{(\w+)\}/g, (match, name) => (name in vars ? String(vars[name]) : match));
}

// Expose as global function — works in all Alpine expressions and plain JS
window.t = t;

/**
 * Translate static markup: data-i18n sets the text, data-i18n-aria the
 * aria-label and data-i18n-title the title. Values are full keys below
 * "web", e.g. data-i18n="settings_page.title". Runs once on DOMContentLoaded
 * for pages without Alpine; call it again for markup added later.
 */
function applyTranslations(root = document) {
  root.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
  root.querySelectorAll('[data-i18n-aria]').forEach(el => { el.setAttribute('aria-label', t(el.dataset.i18nAria)); });
  root.querySelectorAll('[data-i18n-title]').forEach(el => { el.setAttribute('title', t(el.dataset.i18nTitle)); });
}
window.applyTranslations = applyTranslations;
if (typeof document !== 'undefined') {
  const run = () => { document.documentElement.lang = _lang; applyTranslations(); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run);
  else run();
}

// Expose the active language for use by speech recognition etc.
window._mycelos_lang = _lang;
