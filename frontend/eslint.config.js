import js from "@eslint/js";
import { defineConfigWithVueTs, vueTsConfigs } from "@vue/eslint-config-typescript";
import pluginVue from "eslint-plugin-vue";
import globals from "globals";

export default defineConfigWithVueTs(
  { ignores: ["dist", "src/api/schema.d.ts"] },
  {
    files: ["**/*.{ts,vue}"],
    languageOptions: { globals: globals.browser },
  },
  pluginVue.configs["flat/recommended"],
  vueTsConfigs.recommended,
  {
    // Layout-only rules; template formatting is left to authors.
    rules: {
      "vue/max-attributes-per-line": "off",
      "vue/singleline-html-element-content-newline": "off",
    },
  },
  {
    files: ["scripts/**/*.mjs"],
    ...js.configs.recommended,
    languageOptions: { globals: globals.node },
  },
);
