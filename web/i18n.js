// Display-only localization over the canonical shared catalogs. Text remains logical
// Unicode: the browser owns bidi and shaping, including clipboard/search text.
const BRAND = "prokjs's Set 19 TFT tool";
const COMMUNITY_NOTICE = 'Community AI translation · Some text may remain in English.';
const PROVENANCE_NOTICE = 'Community AI translations, not official Riot translations. No endorsement or translation-quality guarantee. Missing text falls back visibly to English.';
const escapeRegex = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// Parse Python's presentation fields without evaluating fields or formatting
// already-formatted values. Escaped braces remain literal, as in string.Formatter.
function fields(text) {
  const parts = [];
  let literal = '';
  for (let index = 0; index < text.length;) {
    const character = text[index];
    if ((character === '{' || character === '}') && text[index + 1] === character) {
      literal += character;
      index += 2;
    } else if (character === '{') {
      let end = index + 1;
      let nesting = 0;
      for (; end < text.length; end += 1) {
        if (text[end] === '{') nesting += 1;
        if (text[end] === '}') {
          if (!nesting) break;
          nesting -= 1;
        }
      }
      if (end === text.length) return null;
      const field = text.slice(index + 1, end).split(/[!:]/, 1)[0];
      parts.push({literal, field});
      literal = '';
      index = end + 1;
    } else if (character === '}') {
      return null;
    } else {
      literal += character;
      index += 1;
    }
  }
  parts.push({literal, field: null});
  return parts;
}

function compileCatalog(ui, game) {
  const messages = new Map(Object.entries(ui.messages || {}).filter(
    ([, value]) => typeof value === 'string' && value.trim()
  ));
  const stripped = new Map();
  const candidates = new Map();
  const fragments = [];
  for (const [source, translated] of messages) {
    candidates.set(source.trim(), translated.trim());
    if (source.trim() && source.trim() !== source) {
      stripped.set(source.trim(), translated.trim());
      const parsed = fields(source);
      if (parsed && !parsed.some(part => part.field !== null)) fragments.push([source, translated]);
    }
  }
  for (const pair of messages) candidates.set(...pair);
  const templates = [];
  for (const [source, translated] of candidates) {
    const parsed = fields(source);
    const output = fields(translated);
    if (!parsed || !output || !parsed.some(part => part.field !== null)
        || !parsed.some(part => part.literal.trim())) continue;
    const names = parsed.filter(part => part.field !== null).map(part => part.field);
    if (output.some(part => part.field !== null && !names.includes(part.field))) continue;
    const pattern = parsed.map(part => escapeRegex(part.literal)
      + (part.field !== null ? '(.*?)' : '')).join('');
    templates.push({pattern: new RegExp(`^(?:${pattern})$`, 's'), names, output,
      specificity: parsed.reduce((size, part) => size + part.literal.length, 0)});
  }
  templates.sort((left, right) => right.specificity - left.specificity);
  fragments.sort((left, right) => right[0].length - left[0].length);

  const missing = new Set(game.missing || []);
  const names = new Map();
  for (const [name, value] of Object.entries(game.champions || {})) {
    if (!missing.has(`champions.${name}`)) names.set(name, value);
  }
  for (const [name, value] of Object.entries(game.traits || {})) {
    if (value && typeof value === 'object' && !missing.has(`traits.${name}.name`)) {
      names.set(name, value.name || name);
    }
  }
  // A populated English PBE value is not proof of an official localized name.
  for (const group of [game.champions || {}, game.traits || {}]) {
    for (const name of Object.keys(group)) {
      if ((!names.has(name) || names.get(name) === name) && messages.has(name)) {
        names.set(name, messages.get(name));
      }
    }
  }
  for (const [name, value] of names) {
    if (typeof value !== 'string' || !value) names.delete(name);
  }
  const changed = new Map([...names].filter(([name, value]) => name !== value));
  // Include localized values as alternatives so that an already-localized name
  // containing another canonical name is not translated a second time.
  const alternatives = [...new Set([...changed.keys(), ...changed.values()])]
    .sort((left, right) => right.length - left.length).map(escapeRegex);
  const namePattern = alternatives.length
    ? new RegExp(`(?<![\\p{L}\\p{N}_])(?:${alternatives.join('|')})(?![\\p{L}\\p{N}_])`, 'gu')
    : null;
  return {ui, game, messages, stripped, templates, fragments, missing, names, changed, namePattern};
}

export class Localizer {
  // baseUrl is the repository root, not the web/ directory.
  constructor(baseUrl = new URL('../', import.meta.url)) {
    this._baseUrl = new URL(baseUrl, import.meta.url);
    this._language = 'en_US';
    this._catalog = compileCatalog({}, {});
    this._cache = new Map();
    this._request = 0;
  }

  get language() { return this._language; }

  get notice() {
    if (this._language.startsWith('en_')) return '';
    const metadata = this._catalog.ui.metadata || {};
    return [this.tr(COMMUNITY_NOTICE), this.tr(metadata.notice || PROVENANCE_NOTICE),
      metadata.regional_note ? this.tr(metadata.regional_note) : ''].filter(Boolean).join('\n');
  }

  async setLanguage(code) {
    // The engine supplies the actual locale list; never duplicate it here.
    if (typeof code !== 'string' || !/^[a-z]{2}_[A-Z]{2}$/.test(code)) {
      throw new Error(this.tr('Invalid language code.'));
    }
    const request = ++this._request;
    if (!this._cache.has(code)) {
      const load = async folder => {
        const response = await fetch(new URL(`data/${folder}/${code}.json`, this._baseUrl));
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      };
      const pending = Promise.all([load('locales_ui'), load('locales_game')])
        .then(([ui, game]) => compileCatalog(ui, game));
      this._cache.set(code, pending);
      pending.catch(() => this._cache.delete(code));
    }
    let catalog;
    try {
      catalog = await this._cache.get(code);
    } catch (error) {
      throw new Error(`${this.tr('Unable to load language resources.')} ${code}: ${error.message}`, {cause: error});
    }
    // Language switches are atomic; a slower earlier request cannot win a race.
    if (request !== this._request) return;
    this._catalog = catalog;
    this._language = code;
  }

  championName(name) {
    const source = String(name);
    if (this._language.startsWith('en_')) return source;
    const value = this._catalog.game.champions?.[source];
    return this._catalog.missing.has(`champions.${source}`) || !value || value === source
      ? this.tr(source) : value;
  }

  traitName(name) {
    const source = String(name);
    if (this._language.startsWith('en_')) return source;
    const value = this._catalog.game.traits?.[source]?.name;
    return this._catalog.missing.has(`traits.${source}.name`) || !value || value === source
      ? this.tr(source) : value;
  }

  traitDescription(name, english) {
    const source = String(english ?? '');
    if (this._language.startsWith('en_')) return source;
    if (this._catalog.missing.has(`traits.${name}.description`)) return this.tr(source);
    const description = this._catalog.game.traits?.[name]?.description;
    return typeof description === 'string' && description ? description : this.tr(source);
  }

  searchText(text) {
    // NFKD handles accent/compatibility aliases. The remaining common casefold
    // expansions keep German and Greek search consistent with the native app.
    return String(text).normalize('NFKD').toLowerCase().replace(/ß/g, 'ss')
      .replace(/ς/g, 'σ').replace(/\u0345/g, 'ι').replace(/\p{M}/gu, '');
  }

  tr(text) { return this._translate(String(text), 0); }

  _replaceNames(text) {
    const {namePattern, changed} = this._catalog;
    return namePattern ? text.replace(namePattern, name => changed.get(name) || name) : text;
  }

  _translate(text, depth) {
    if (!text || this._language.startsWith('en_') || text === BRAND
        || text.startsWith(`${BRAND} · v`)
        || text.startsWith(`${BRAND} isn't endorsed by Riot Games`)
        || text === 'Developed by prokjs' || depth > 8 || /^https?:\/\/\S+$/.test(text)) return text;
    const catalog = this._catalog;
    if (catalog.names.has(text) && catalog.names.get(text) !== text) return catalog.names.get(text);
    if (catalog.messages.has(text)) return this._replaceNames(catalog.messages.get(text));
    if (catalog.stripped.has(text)) return this._replaceNames(catalog.stripped.get(text));
    const stripped = text.trim();
    if (stripped && stripped !== text) {
      const start = text.length - text.trimStart().length;
      return text.slice(0, start) + this._translate(stripped, depth + 1) + text.slice(start + stripped.length);
    }
    for (const {pattern, names, output} of catalog.templates) {
      const match = pattern.exec(text);
      if (!match || match[0] !== text) continue;
      const values = new Map(names.map((name, index) => [name, this._translate(match[index + 1], depth + 1)]));
      return output.map(part => this._replaceNames(part.literal)
        + (part.field === null ? '' : values.get(part.field))).join('');
    }
    for (const [source, translated] of catalog.fragments) {
      if (/\s$/.test(source) && text.startsWith(source)) {
        return this._replaceNames(translated) + this._translate(text.slice(source.length), depth + 1);
      }
      if (/^\s/.test(source) && text.endsWith(source)) {
        return this._translate(text.slice(0, -source.length), depth + 1) + this._replaceNames(translated);
      }
    }
    for (const separator of ['\n', ' · ', ' → ', ': ']) {
      if (text.includes(separator)) return text.split(separator)
        .map(part => this._translate(part, depth + 1)).join(separator);
    }
    return this._replaceNames(text);
  }
}
