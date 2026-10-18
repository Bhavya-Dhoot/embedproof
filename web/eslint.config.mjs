import { FlatCompat } from "@eslint/eslintrc";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

const compat = new FlatCompat({ baseDirectory: dirname(fileURLToPath(import.meta.url)) });

const config = [
  { ignores: [".next/**", "node_modules/**", "src/generated/**", "next-env.d.ts", "tests/vectors/**"] },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
]

export default config;
