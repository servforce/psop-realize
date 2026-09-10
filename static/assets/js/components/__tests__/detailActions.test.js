import { jest } from '@jest/globals';
import { readFileSync } from 'node:fs';
import videosPage from '../videosPage.js';

const markup = readFileSync(new URL('../../../../pages/videos.html', import.meta.url), 'utf8');
const actions = [
  ['full', '重新解析', 'transcript'],
  ['transcript', '重新转写', 'transcript'],
  ['keyframes', '重新抽帧', 'frames'],
  ['markdown', '重新生成 Markdown', 'markdown'],
];

test('all regeneration buttons share one scrollable action group with export', () => {
  const group = markup.match(/<div[^>]+aria-label="解析与导出操作"[^>]*>([\s\S]*?)<\/div>/)?.[0];
  expect(group).toBeDefined();
  expect(group).toContain('overflow-x-auto');
  expect(group.match(/<button\b/g)).toHaveLength(5);
  for (const [mode, label] of actions) {
    const button = group.match(new RegExp(`<button[^>]+@click="parse\\('${mode}'\\)"[^>]*>[\\s\\S]*?<\\/button>`))?.[0];
    expect(button).toContain(label);
    expect(button).toContain(':disabled="busy"');
    expect(button).toContain('title=');
  }
  expect(group).toContain('@click="exportResult()"');
  expect(group).toContain(':disabled="exportBusy || !job || busy"');
});

test('empty-state instructions use the renamed actions', () => {
  for (const [, label] of actions) expect(markup).toContain(`“${label}”`);
  expect(markup).not.toContain('一键解析');
  expect(markup).not.toContain('单步解析');
  expect(markup).not.toContain('“抽取业务帧”');
});

test.each(actions)('%s still sends the original parse mode and opens the corresponding artifact', async (mode, _label, tab) => {
  const originalFetch = global.fetch;
  const page = videosPage();
  const job = { id: 'test-video', status: 'completed', stage_total: 10, stage_processed: 10, stage_message: 'Previous stage' };
  const app = { videos: [job], markParseStarted: jest.fn(), accept: jest.fn() };
  const startPolling = jest.fn().mockImplementationOnce(() => {
    expect(page.job.stage_total).toBe(0);
    expect(page.job.stage_processed).toBe(0);
    expect(page.job.stage_message).toBe('');
    expect(page.stageCount(page.job)).toBe('');
  });
  Object.assign(page, { id: job.id, job, $store: { app }, startPolling, loadArtifact: jest.fn() });
  global.fetch = jest.fn().mockResolvedValue({ ok: true, json: async () => ({ job }) });
  try {
    await page.parse(mode);
    expect(global.fetch).toHaveBeenCalledWith(`/api/videos/test-video/parse?mode=${mode}`, expect.objectContaining({ method: 'POST' }));
    expect(app.markParseStarted).toHaveBeenCalledWith(job.id);
    expect(page.tab).toBe(tab);
    expect(page.loadArtifact).toHaveBeenCalledTimes(1);
    expect(page.parseBusy).toBe(false);
  } finally {
    page.destroy();
    global.fetch = originalFetch;
  }
});
