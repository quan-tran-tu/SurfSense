/**
 * Build: `npm run build` writes dist/, which scripts/web/serve.py serves with the
 * deployment's config injected into index.html's marker.
 *
 * Dev: `npm run dev` serves the page on :3000 and proxies the API, so the page
 * and the API share an origin and the backend's CSRF check never sees :3000:
 *
 *   SS_DEV_BACKEND=https://osint.lab.ncc.local/api \
 *   SS_MODEL=qwen3.6-35b SS_MODEL_BASE=http://10.11.34.11:8014/v1 SS_API_KEY=EMPTY \
 *   npm run dev
 *
 * SS_DEV_BACKEND is the API as this machine sees it (default http://localhost:8000).
 * The SS_MODEL* values play the part of serve.py's --vllm/--deepseek flags.
 */
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const PROXY_PREFIX = "/__api";
const MARKER = "/*__SS_CONFIG__*/";

/** serve.py's injection, for the dev server only: the build keeps the marker. */
function devConfig(): Plugin {
  const config: Record<string, string> = { backend: PROXY_PREFIX };
  const env: Record<string, string> = {
    apiKey: "SS_API_KEY",
    model: "SS_MODEL",
    modelBase: "SS_MODEL_BASE",
  };
  for (const [key, name] of Object.entries(env)) {
    const value = process.env[name]?.trim();
    if (value) config[key] = value;
  }
  return {
    name: "ss-dev-config",
    apply: "serve",
    transformIndexHtml: (html) =>
      html.replace(
        MARKER,
        Object.entries(config)
          .map(([k, v]) => `window.SS_CONFIG.${k} = ${JSON.stringify(v)};`)
          .join(" "),
      ),
  };
}

const target = (process.env.SS_DEV_BACKEND ?? "http://localhost:8000").replace(/\/$/, "");

export default defineConfig({
  // Relative asset URLs, so the page also works when a proxy mounts it under a prefix.
  base: "./",
  plugins: [react(), devConfig()],
  server: {
    port: 3000,
    proxy: {
      [PROXY_PREFIX]: {
        target,
        changeOrigin: true,
        secure: false, // the lab ingress uses a private CA
        rewrite: (path) => path.slice(PROXY_PREFIX.length),
        // The session cookie must land on localhost, whatever domain the API names.
        cookieDomainRewrite: "",
        configure: (proxy) => {
          // The CSRF middleware allow-lists the API's public origin, not :3000.
          proxy.on("proxyReq", (req) => req.setHeader("origin", new URL(target).origin));
        },
      },
    },
  },
});
