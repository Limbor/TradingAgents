/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        ui: {
          canvas: "rgb(var(--ui-canvas) / <alpha-value>)",
          panel: "rgb(var(--ui-panel) / <alpha-value>)",
          subtle: "rgb(var(--ui-subtle) / <alpha-value>)",
          hover: "rgb(var(--ui-hover) / <alpha-value>)",
          line: "rgb(var(--ui-line) / <alpha-value>)",
          strong: "rgb(var(--ui-strong) / <alpha-value>)",
          ink: "rgb(var(--ui-ink) / <alpha-value>)",
          body: "rgb(var(--ui-body) / <alpha-value>)",
          muted: "rgb(var(--ui-muted) / <alpha-value>)",
          faint: "rgb(var(--ui-faint) / <alpha-value>)",
          accent: "rgb(var(--ui-accent) / <alpha-value>)",
          accentSoft: "rgb(var(--ui-accent-soft) / <alpha-value>)",
          onAccent: "rgb(var(--ui-on-accent) / <alpha-value>)",
          onWarning: "rgb(var(--ui-on-warning) / <alpha-value>)",
          success: "rgb(var(--ui-success) / <alpha-value>)",
          warning: "rgb(var(--ui-warning) / <alpha-value>)",
          danger: "rgb(var(--ui-danger) / <alpha-value>)",
          info: "rgb(var(--ui-info) / <alpha-value>)",
        },
      },
    },
  },
  plugins: [require("@tailwindcss/typography")],
};
