import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { build as esbuild } from "esbuild";

globalThis.require = createRequire(import.meta.url);
const artifactDir = path.dirname(fileURLToPath(import.meta.url));

await esbuild({
  entryPoints: [path.resolve(artifactDir, "../src/scripts/seed.ts")],
  platform: "node",
  bundle: true,
  format: "esm",
  outdir: path.resolve(artifactDir, "../dist-seed"),
  outExtension: { ".js": ".mjs" },
  logLevel: "info",
  external: ["pg-native", "bcrypt", "*.node"],
  banner: {
    js: `import { createRequire as __bannerCrReq } from 'node:module';
globalThis.require = __bannerCrReq(import.meta.url);`,
  },
});
