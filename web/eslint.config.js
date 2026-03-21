import js from "@eslint/js"
import globals from "globals"
import react from "eslint-plugin-react"
import reactHooks from "eslint-plugin-react-hooks"
import reactRefresh from "eslint-plugin-react-refresh"

export default [
  { ignores: ["dist", "storybook-static"] },
  {
    files: ["**/*.{js,jsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: globals.browser,
      parserOptions: {
        ecmaFeatures: { jsx: true }
      }
    },
    plugins: {
      react,
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh
    },
    rules: {
      ...js.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      "react/jsx-uses-react": "off",
      "react/jsx-uses-vars": "error",
      "react-refresh/only-export-components": "off",
      "react-hooks/exhaustive-deps": "off",
      "no-empty": "off",
      "no-useless-escape": "off"
    }
  },
  {
    files: ["**/*.test.{js,jsx}"],
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.vitest
      }
    }
  }
]
