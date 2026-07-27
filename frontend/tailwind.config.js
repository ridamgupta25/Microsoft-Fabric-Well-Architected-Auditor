/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Rating bands from the scoring rubric. Named by meaning, not by hue,
        // so a palette change never requires touching component code.
        rating: {
          excellent: "#2563eb",
          good: "#16a34a",
          medium: "#ca8a04",
          high: "#ea580c",
          critical: "#dc2626",
          unknown: "#94a3b8",
        },
      },
    },
  },
  plugins: [],
};
