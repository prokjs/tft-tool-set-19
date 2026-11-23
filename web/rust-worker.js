// The bundled Rust/WASM core runs entirely outside the rendering thread.
const REQUEST_TYPES = new Set(["initialize", "pool", "recommend", "replan", "choose_branch", "damage_alternative", "cap_out", "encode", "format"]);
let dispatchJson = null;
let initialization = null;
let queue = Promise.resolve();

function status(message) {
  self.postMessage({ type: "status", message });
}

function dispatch(type, payload) {
  const reply = JSON.parse(dispatchJson(JSON.stringify({ type, payload })));
  if (reply.ok !== true) {
    const error = new Error(reply.error?.message || "The local calculation failed.");
    error.name = reply.error?.name || "Error";
    throw error;
  }
  return reply.result;
}

function initialize() {
  if (!initialization) {
    initialization = (async () => {
      status("Loading the bundled Rust WebAssembly engine…");
      const core = await import("./pkg/tft_core.js");
      await core.default({ module_or_path: new URL("./pkg/tft_core_bg.wasm", import.meta.url) });
      dispatchJson = core.dispatch_json;
      const metadata = dispatch("initialize", {});
      status("Ready. Calculations run locally in this browser.");
      return metadata;
    })();
  }
  return initialization;
}

async function handle(message) {
  if (!message || !Number.isSafeInteger(message.id) || message.id < 1) return;
  const { id, type, payload = {} } = message;
  try {
    if (!REQUEST_TYPES.has(type)) throw new Error(`Unknown calculation request: ${type}`);
    const metadata = await initialize();
    const result = type === "initialize" ? metadata : dispatch(type, payload);
    self.postMessage({ id, ok: true, result });
  } catch (error) {
    self.postMessage({
      id,
      ok: false,
      error: {
        name: typeof error?.name === "string" ? error.name : "Error",
        message: typeof error?.message === "string" ? error.message : String(error),
      },
    });
  }
}

self.onmessage = (event) => {
  // Each request reports its own error. Failed initialization stays failed, but
  // invalid inputs must not poison later calculations in an initialized worker.
  queue = queue.then(() => handle(event.data)).catch((error) => {
    self.postMessage({
      id: event.data?.id,
      ok: false,
      error: { name: "Error", message: String(error) },
    });
  });
};
