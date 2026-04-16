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
 * @param {string} key - Dot-separated key, e.g. "sidebar.knowledge"
 * @returns {string}
 */
function t(key) {
  const parts = key.split('.');
  let node = _translations;
  for (const part of parts) {
    if (node == null || typeof node !== 'object') return key;
    node = node[part];
  }
  return typeof node === 'string' ? node : key;
}

// Expose as global function — works in all Alpine expressions and plain JS
window.t = t;

// Expose the active language for use by speech recognition etc.
window._mycelos_lang = _lang;
