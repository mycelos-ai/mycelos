/**
 * Shared Tailwind CSS configuration for all Mycelos pages.
 *
 * This file is loaded AFTER the Tailwind CDN script and sets
 * the custom theme (colors, fonts, radii) once — no duplication
 * across HTML pages.
 *
 * Usage in HTML <head>:
 *   <script src="https://cdn.tailwindcss.com?plugins=forms"></script>
 *   <script src="/shared/tailwind-config.js"></script>
 */
tailwind.config = {
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
