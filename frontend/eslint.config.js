import js from "@eslint/js"
import globals from "globals"
import reactHooks from "eslint-plugin-react-hooks"
import reactRefresh from "eslint-plugin-react-refresh"
import tseslint from "typescript-eslint"

export default tseslint.config(
  { ignores: ["dist", "node_modules"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // This React 18 app intentionally hydrates local form state from query
      // results and navigation state in effects. The React 19-oriented rule
      // treats those established synchronization effects as hard errors.
      "react-hooks/set-state-in-effect": "off",
      // Chat visualization modules intentionally co-locate pure parsers with
      // their component so the WebSocket and Library paths share one contract.
      // This affects HMR granularity only, not runtime hook correctness.
      "react-refresh/only-export-components": "off",
    },
  },
)
