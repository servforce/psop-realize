import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import postcss from 'postcss';

// Exercise the source-map disclosure case against a synthetic fixture, never real secrets.
test('untrusted sourceMappingURL cannot read an absolute map when from is unset', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'psop-postcss-security-'));
  const marker = 'PSOP_PRIVATE_SOURCE_MAP_FIXTURE';
  const sourceMap = join(directory, 'private.map');
  writeFileSync(sourceMap, JSON.stringify({ version: 3, sources: ['private.css'], mappings: '', names: [], sourcesContent: [marker] }));
  try {
    const result = await postcss([]).process(`a { color: red }\n/*# sourceMappingURL=${sourceMap} */`, { from: undefined, map: { inline: false } });
    expect(result.css).not.toContain(marker);
    expect(result.map?.toString() || '').not.toContain(marker);
    expect(result.root.first.source.input.map?.text || '').not.toContain(marker);
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test('the shipped Plotly Basic bundle has no MapLibre dependency chain', () => {
  const lock = JSON.parse(readFileSync(new URL('../../../../package-lock.json', import.meta.url), 'utf8'));
  const packages = Object.keys(lock.packages);
  expect(packages.some(name => /(?:^|\/)node_modules\/maplibre-gl$/.test(name))).toBe(false);
  expect(packages).not.toContain('node_modules/plotly.js');
  expect(packages).toContain('node_modules/plotly.js-basic-dist-min');
  const basic = JSON.parse(readFileSync(new URL('../../../../node_modules/plotly.js-basic-dist-min/package.json', import.meta.url), 'utf8'));
  expect(Object.keys(basic.dependencies || {})).toHaveLength(0);
});
