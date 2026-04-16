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
        "surface-container": "#171a1f",
        "surface-container-high": "#1d2025",
        "surface-dim": "#0c0e12",
        "surface-container-lowest": "#000000",
        "surface-container-low": "#111318",
        "surface-variant": "#23262c",
        "surface-container-highest": "#23262c",
        "surface-bright": "#292c33",
        "secondary-container": "#00658c",
        "outline-variant": "#46484d",
        "on-surface-variant": "#aaabb0",
        "on-surface": "#f6f6fc",
        "on-background": "#f6f6fc",
        "background": "#0c0e12",
        "surface": "#0c0e12",
        "primary": "#6ad6ff",
        "primary-dim": "#00bcec",
        "primary-container": "#00cbfe",
        "secondary": "#23bcfe",
        "tertiary": "#879dff",
        "tertiary-container": "#778ff7",
        "error": "#ff716c",
        "on-primary": "#00485c",
        "on-primary-fixed": "#002733",
        "on-secondary-container": "#f2f8ff",
        "outline": "#74757a",
        "surface-tint": "#6ad6ff",
      },
      fontFamily: {
        "headline": ["Space Grotesk"],
        "body": ["Manrope"],
        "label": ["Inter"],
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
