import { readFileSync } from 'node:fs';

const page = readFileSync(new URL('../../../../pages/videos.html', import.meta.url), 'utf8');
const markdown = page.slice(page.indexOf('class="markdown-pane"'), page.indexOf('x-show="tab === \'frames\'"'));
const css = readFileSync(new URL('../../../css/style.css', import.meta.url), 'utf8');

test('Markdown has a single floating group with preview, source and copy controls', () => {
  const actions = markdown.match(/<div class="markdown-actions"[^>]*>([\s\S]*?)<\/div>/)?.[1];
  expect(actions.match(/<button\b/g)).toHaveLength(3);
  expect(actions).toContain('>预览</button>');
  expect(actions).toContain('>源码</button>');
  expect(actions).toContain('复制</button>');
  expect(markdown).not.toContain('artifact-toolbar');
  expect(markdown).not.toContain('渲染视图');
  expect(markdown).not.toContain('源码视图');
  expect(css).toMatch(/\.markdown-pane\s*\{[^}]*relative[^}]*overflow-hidden/);
  expect(css).toMatch(/\.markdown-actions\s*\{[^}]*absolute top-3 right-4 z-10/);
});

test('preview and source remain independent scroll surfaces under the floating controls', () => {
  expect(markdown).toMatch(/id="markdown-preview"[^>]*x-show="markdown && !source"[^>]*x-html="renderedMarkdown"[^>]*overflow-auto/);
  expect(markdown).toMatch(/<textarea id="markdown-source"[^>]*x-show="markdown && source"[^>]*readonly[^>]*:value="markdown"[^>]*overflow-auto/);
  expect(markdown).toContain(':aria-pressed="!source"');
  expect(markdown).toContain(':aria-pressed="source"');
  expect(markdown).toContain('aria-controls="markdown-preview"');
  expect(markdown).toContain('aria-controls="markdown-source"');
  expect(markdown).toContain('@click="copy(markdown, \'markdown\')" :disabled="!markdown"');
});
