/**
 * What the deployment decided, injected by serve.py into index.html's marker
 * (see scripts/web/serve.py). --deepseek <key> injects apiKey; --vllm <model>
 * --vllm_port <port> injects apiKey ("EMPTY"), model and modelBase; --backend
 * <url> injects backend. Under a plain static server nothing is injected and the
 * login screen asks. Injected values are deliberately never written to
 * localStorage: they live only in the page that served them.
 */
declare global {
  interface Window {
    SS_CONFIG?: { apiKey?: string; model?: string; modelBase?: string; backend?: string };
  }
}

const injected = window.SS_CONFIG ?? {};

export const INJECTED_KEY = injected.apiKey || "";
export const INJECTED_MODEL = injected.model || "";
export const INJECTED_BASE = injected.modelBase || "";
// The empty string is a meaningful backend ("same origin, so prefix nothing"),
// so presence — not truthiness — is what says the deployment chose one.
export const HAS_BACKEND = Object.hasOwn(injected, "backend");
export const INJECTED_BACKEND = HAS_BACKEND ? String(injected.backend).replace(/\/$/, "") : "";

export const DEFAULT_BACKEND = "http://localhost:8000";
export const DEFAULT_MODEL = "deepseek-chat";
export const DEFAULT_MODEL_BASE = "https://api.deepseek.com/v1";
