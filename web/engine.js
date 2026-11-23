const REQUEST_TYPES = new Set(["pool", "recommend", "replan", "choose_branch", "damage_alternative", "cap_out", "encode", "format"]);

function nativeTransport() {
  const tauri = globalThis.__TAURI__?.core || globalThis.__TAURI_INTERNALS__;
  return typeof tauri?.invoke === "function" ? tauri : null;
}

export function isNative() {
  return nativeTransport() !== null;
}

export function invokeNative(command, payload = {}) {
  const tauri = nativeTransport();
  if (!tauri) return Promise.reject(new Error("Native application commands are unavailable in this browser."));
  return tauri.invoke(command, payload);
}

/** Local Rust calculation client: native background commands or a WASM worker. */
export class BrowserEngine {
  #worker = null;
  #invoke = null;
  #nativeQueue = Promise.resolve();
  #initialization = null;
  #pending = new Map();
  #nextId = 0;
  #closed = false;
  #failure = null;
  #onStatus;

  constructor({ onStatus } = {}) {
    this.#onStatus = typeof onStatus === "function" ? onStatus : null;
  }

  initialize() {
    if (this.#closed) return Promise.reject(new Error("The calculation engine is closed."));
    if (this.#failure) return Promise.reject(this.#failure);
    if (this.#initialization) return this.#initialization;
    try {
      const tauri = nativeTransport();
      if (tauri) {
        this.#invoke = tauri.invoke.bind(tauri);
        this.#receive({ type: "status", message: "Loading the native Rust calculation engine…" });
      } else {
        this.#worker = new Worker(new URL("./rust-worker.js", import.meta.url), {
          type: "module",
          name: "tft-local-rust",
        });
        this.#worker.onmessage = (event) => this.#receive(event.data);
        this.#worker.onerror = (event) => {
          event.preventDefault();
          this.#fail(new Error(event.message || "The local Rust worker could not start."));
        };
        this.#worker.onmessageerror = () => {
          this.#fail(new Error("The local Rust worker returned an unreadable message."));
        };
      }
      this.#initialization = this.#send("initialize", {}).catch((error) => {
        this.#fail(error);
        throw error;
      });
    } catch (error) {
      this.#fail(error);
      this.#initialization = Promise.reject(error);
    }
    return this.#initialization;
  }

  async request(type, payload = {}) {
    if (!REQUEST_TYPES.has(type)) throw new Error(`Unknown calculation request: ${type}`);
    await this.initialize();
    return this.#send(type, payload);
  }

  close() {
    if (this.#closed) return;
    this.#closed = true;
    this.#fail(new Error("The calculation engine is closed."));
  }

  #send(type, payload) {
    if (this.#closed || this.#failure) {
      return Promise.reject(this.#failure || new Error("The calculation engine is closed."));
    }
    const id = ++this.#nextId;
    return new Promise((resolve, reject) => {
      this.#pending.set(id, { resolve, reject });
      try {
        if (this.#invoke) {
          // Match worker ordering without blocking rendering or invoking closed requests.
          this.#nativeQueue = this.#nativeQueue.then(async () => {
            if (!this.#pending.has(id)) return;
            try {
              const result = await this.#invoke("engine_dispatch", { requestType: type, payload });
              this.#receive({ id, ok: true, result });
              if (type === "initialize") {
                this.#receive({ type: "status", message: "Ready. Calculations run locally in native Rust." });
              }
            } catch (error) {
              this.#receive({
                id,
                ok: false,
                error: {
                  name: typeof error?.name === "string" ? error.name : "Error",
                  message: typeof error?.message === "string" ? error.message : String(error),
                },
              });
            }
          });
        } else {
          this.#worker.postMessage({ id, type, payload });
        }
      } catch (error) {
        this.#pending.delete(id);
        reject(error);
      }
    });
  }

  #receive(message) {
    if (this.#closed || this.#failure || !message || typeof message !== "object") return;
    if (message.type === "status") {
      // Presentation callbacks must not interrupt request delivery.
      try {
        this.#onStatus?.(message.message);
      } catch {
        // The callback belongs to the UI; the worker remains usable.
      }
      return;
    }
    const pending = this.#pending.get(message.id);
    if (!pending) return;
    this.#pending.delete(message.id);
    if (message.ok === true) {
      pending.resolve(message.result);
    } else {
      const error = new Error(message.error?.message || "The local calculation failed.");
      error.name = message.error?.name || "Error";
      pending.reject(error);
    }
  }

  #fail(error) {
    this.#failure = error instanceof Error ? error : new Error(String(error));
    this.#worker?.terminate();
    this.#worker = null;
    this.#invoke = null;
    for (const { reject } of this.#pending.values()) reject(this.#failure);
    this.#pending.clear();
  }
}
