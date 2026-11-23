let initialization = null;
let itemImages = null;
let translate = value => value;

/** Load the bundled catalog once, before rendering item strips. */
export function initializeItemIcons(root = new URL('../', import.meta.url), localize = value => value) {
  translate = localize;
  if (!initialization) {
    initialization = (async () => {
      const base = new URL(root, import.meta.url);
      const response = await fetch(new URL('data/item_icons.json', base));
      if (!response.ok) throw new Error(`Unable to load bundled item icons: ${response.status}`);
      const catalog = await response.json();
      const entries = Object.entries(catalog.items || {});
      if (catalog.set !== 19 || !entries.length || catalog.coverage?.missing?.length ||
          catalog.coverage?.required !== entries.length || catalog.coverage?.mapped !== entries.length) {
        throw new Error('The bundled Set 19 item-icon catalog is incomplete.');
      }
      const images = new Map();
      for (const [name, entry] of entries) {
        if (!name || !/^assets\/items\/[a-z0-9-]+\.webp$/.test(entry.file)) {
          throw new Error(`Invalid bundled item-icon mapping: ${name}`);
        }
        images.set(name, new URL(entry.file, base).href);
      }
      itemImages = images;
    })();
  }
  return initialization;
}

/** Bundled artwork where available; full-name tokens for the rest of the catalog. */
export function itemIconStrip(names = []) {
  if (!itemImages) throw new Error('Call initializeItemIcons(root) before rendering item icons.');
  const strip = document.createElement('span');
  strip.className = 'item-icon-strip';
  for (const name of names) {
    const source = itemImages.get(name);
    if (!source) {
      const token = document.createElement('span');
      token.className = 'item-text-token'; token.textContent = translate(name);
      token.title = translate(name); token.setAttribute('aria-label', translate(name));
      strip.append(token); continue;
    }
    const icon = document.createElement('span');
    icon.className = 'item-icon';
    icon.title = translate(name);
    icon.setAttribute('role', 'img');
    icon.setAttribute('aria-label', translate(name));
    const image = document.createElement('img');
    image.alt = '';
    image.setAttribute('aria-hidden', 'true');
    image.width = 24;
    image.height = 24;
    image.decoding = 'async';
    image.addEventListener('error', () => {
      const message = translate(name);
      icon.className = 'item-text-token';
      icon.title = message;
      icon.setAttribute('aria-label', message);
      image.remove();
      icon.textContent = message;
    }, { once: true });
    image.src = source;
    icon.append(image);
    strip.append(icon);
  }
  return strip;
}

const ROLE_SHAPES = {
  tank: { label: 'Tank', paths: ['M12 3 20 6v6c0 4-3.5 7-8 9-4.5-2-8-5-8-9V6Z', 'M12 7v10M8 12h8'] },
  ad: { label: 'AD · physical damage', paths: ['m9 14 9-10 2-1v5l-9 9Z', 'm6 12 7 7M9 16l-5 5-2-2 5-5'] },
  ap: { label: 'AP · magic damage', paths: ['m13 3 2.5 6.5L22 12l-6.5 2.5L13 21l-2.5-6.5L4 12l6.5-2.5Z', 'M4 3v5M1.5 5.5h5'] },
  flex: { label: 'AD / AP flex · choose one item build', paths: ['M12 3a9 9 0 1 0 9 9', 'M4 20 20 4', 'm6 13 4-5 1-1v3l-3 4M5 12l4 4', 'm17 12 1.5 3.5L22 17l-3.5 1.5L17 22l-1.5-3.5L12 17l3.5-1.5Z'] },
  unknown: { label: 'Unclassified role', paths: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18', 'M9.5 9a2.5 2.5 0 0 1 5 0c0 2-2.5 2-2.5 4M12 16v.2'] },
};

/** Shield / sword / magic / split-flex glyph, with no visible role label. */
export function roleIcon(kind = 'unknown') {
  const key = String(kind).toLowerCase();
  const shape = ROLE_SHAPES[key] || ROLE_SHAPES.unknown;
  const icon = document.createElement('span');
  icon.className = `role-icon role-icon-${key}`;
  const label = translate({ tank: 'Tank', ad: 'AD', ap: 'AP', flex: 'AD / AP flex', unknown: 'unavailable' }[key] || 'unavailable');
  icon.title = label;
  icon.setAttribute('role', 'img');
  icon.setAttribute('aria-label', label);
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.7');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  for (const d of shape.paths) {
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', d);
    svg.append(path);
  }
  icon.append(svg);
  return icon;
}
