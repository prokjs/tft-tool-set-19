// Prerequisites: rustup target add wasm32-unknown-unknown; install wasm-bindgen-cli
// with the same version as the wasm-bindgen crate resolved in Cargo.lock.
// npm run build:wasm performs both real compilation and JS binding generation.
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const target = path.resolve(root, process.env.CARGO_TARGET_DIR || 'target');
function run(command, args) {
  console.log([command, ...args].join(' '));
  const result = spawnSync(command, args, { cwd: root, stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status || 1);
}

run('cargo', ['build', '-p', 'tft-core', '--release', '--target', 'wasm32-unknown-unknown', '--features', 'wasm', '--target-dir', target]);
run('wasm-bindgen', [path.join(target, 'wasm32-unknown-unknown', 'release', 'tft_core.wasm'), '--target', 'web', '--out-dir', path.join(root, 'web', 'pkg'), '--out-name', 'tft_core', '--no-typescript']);
