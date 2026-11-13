/**
 * Tailwind configuration for the Mycelos Web UI.
 *
 * The UI used to load Tailwind from cdn.tailwindcss.com and compile classes
 * in the browser. Mycelos often runs on a home network without internet
 * access, so the stylesheet is now compiled ahead of time into
 * src/mycelos/frontend/shared/vendor/tailwind.css and committed.
 *
 * Rebuild after adding or changing Tailwind classes:
 *   scripts/build-tailwind.sh
 * CI fails when the committed CSS is out of date.
 */
const path = require("path");

const frontend = path.join(__dirname, "../../src/mycelos/frontend");

module.exports = {
  content: [
    path.join(frontend, "index.html"),
    path.join(frontend, "pages/**/*.html"),
    path.join(frontend, "shared/**/*.{html,js}"),
    // Third-party bundles are not ours; scanning them only adds noise.
    "!" + path.join(frontend, "shared/vendor/**"),
    "!" + path.join(frontend, "shared/mermaid.min.js"),
  ],
  plugins: [require("@tailwindcss/forms")],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        "surface-container": "var(--surface-container)",
        "surface-container-high": "var(--surface-container-high)",
        "surface-dim": "var(--surface-dim)",
        "surface-container-lowest": "var(--surface-container-lowest)",
        "surface-container-low": "var(--surface-container-low)",
        "surface-variant": "var(--surface-variant)",
        "surface-container-highest": "var(--surface-container-highest)",
        "surface-bright": "var(--surface-bright)",
        "secondary-container": "var(--secondary-container)",
        "outline-variant": "var(--outline-variant)",
        "on-surface-variant": "var(--on-surface-variant)",
        "on-surface": "var(--on-surface)",
        "on-background": "var(--on-background)",
        "background": "var(--background)",
        "surface": "var(--surface)",
        "primary": "var(--primary)",
        "primary-dim": "var(--primary-dim)",
        "primary-container": "var(--primary-container)",
        "secondary": "var(--secondary)",
        "tertiary": "var(--tertiary)",
        "tertiary-container": "var(--tertiary-container)",
        "error": "var(--error)",
        "on-primary": "var(--on-primary)",
        "on-primary-fixed": "var(--on-primary-fixed)",
        "on-secondary-container": "var(--on-secondary-container)",
        "outline": "var(--outline)",
        "surface-tint": "var(--surface-tint)",
      },
      fontFamily: {
        "headline": ["system-ui", "sans-serif"],
        "body": ["system-ui", "sans-serif"],
        "label": ["system-ui", "sans-serif"],
      },
      borderRadius: {
        "DEFAULT": "0.25rem",
        "lg": "0.5rem",
        "xl": "0.75rem",
        "full": "9999px",
      },
    },
  },
};
