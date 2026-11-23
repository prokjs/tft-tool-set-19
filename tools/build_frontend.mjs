// npm run build:frontend copies only local presentation assets and legal docs.
// npm run build:web first compiles Rust/WASM, then requires its generated files.
import { copyFile, mkdir, readdir, rm, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const output = path.join(root, 'frontend-dist');
const arguments_ = new Set(process.argv.slice(2));
if ([...arguments_].some(value => value !== '--wasm')) {
  throw new Error('Usage: node tools/build_frontend.mjs [--wasm]');
}
const directories = ['web', 'data', 'assets'];
const documents = ['web_manifest.json', 'README.md', 'LEGAL.md', 'THIRD_PARTY_NOTICES.txt'];
const sourceDocuments = ['lib.rs', 'resources.rs', 'model.rs', 'optimizer.rs', 'planner.rs', 'cap_out.rs', 'cap_items.rs', 'pool.rs', 'guidance.rs', 'positioning.rs', 'trait_support.rs', 'item_builds.rs', 'team_code.rs'];
const extensions = new Set(['.html', '.css', '.js', '.json', '.wasm', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg', '.ico', '.woff', '.woff2', '.ttf', '.otf', '.txt']);
const excludedDirectories = new Set(['node_modules', '__pycache__', 'target', 'build', 'dist', 'frontend-dist']);
const required = ['web/index.html', 'web/app.js', 'web/engine.js', 'web/rust-worker.js', 'web/i18n.js', 'web/styles.css', 'web/guide.css', 'web/team-drawer.js', 'web/team-drawer.css', 'web/item-icons.js', 'web/item-icons.css', 'web/inventory.js', 'web/champion-bis.js', 'web/item-workspace.css', 'data/set19.json', 'data/item_icons.json', 'data/item_catalog.json', 'data/item_builds.json', 'assets/manifest.json', ...documents];
if (arguments_.has('--wasm')) required.push('web/pkg/tft_core.js', 'web/pkg/tft_core_bg.wasm');
required.push(...sourceDocuments.map(filename => `crates/tft-core/src/${filename}`));

// Check required inputs before replacing the previous standalone distribution.
for (const filename of required) {
  if (!(await stat(path.join(root, filename))).isFile()) {
    throw new Error(`Missing frontend input: ${filename}`);
  }
}

async function copyDirectory(source, destination) {
  await mkdir(destination, { recursive: true });
  for (const entry of await readdir(source, { withFileTypes: true })) {
    if (entry.name.startsWith('.') || entry.isSymbolicLink()) continue;
    const from = path.join(source, entry.name);
    const to = path.join(destination, entry.name);
    if (entry.isDirectory()) {
      if (!excludedDirectories.has(entry.name)) await copyDirectory(from, to);
    } else if (entry.isFile() && extensions.has(path.extname(entry.name).toLowerCase())) {
      await copyFile(from, to);
    }
  }
}

await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
for (const directory of directories) {
  await copyDirectory(path.join(root, directory), path.join(output, directory));
}
for (const filename of documents) {
  await copyFile(path.join(root, filename), path.join(output, filename));
}
const sourceOutput = path.join(output, 'engine-source');
await mkdir(sourceOutput, { recursive: true });
for (const filename of sourceDocuments) {
  await copyFile(path.join(root, 'crates/tft-core/src', filename), path.join(sourceOutput, filename));
}
console.log(`Static frontend copied to ${output}`);
