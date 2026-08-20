import type { Config } from "tailwindcss";

// Restraint reads as seriousness. One accent, one alarm colour, a lot of grey. The
// subject matter is a death in the family - nothing here should feel like a growth
// dashboard.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#0b0d0f",
          900: "#111417",
          850: "#171b1f",
          800: "#1e2328",
          700: "#2b3138",
          600: "#3b434c",
          500: "#5a636e",
          400: "#8b939c",
          300: "#b6bcc3",
          200: "#d8dce0",
        },
        sage: {
          // Progress. Muted on purpose - a closed account is a relief, not a win.
          500: "#5f8f75",
          400: "#7aa88e",
          300: "#9cc0ac",
        },
        amber: {
          500: "#b08243",
          400: "#c69a5c",
        },
        alarm: {
          600: "#8f4a45",
          500: "#a85a53",
          400: "#c07a72",
        },
      },
      fontFamily: {
        sans: ["ui-sans-serif", "Inter", "Segoe UI", "system-ui", "sans-serif"],
        serif: ["Georgia", "Times New Roman", "serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Consolas", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
    },
  },
  plugins: [],
};

export default config;
