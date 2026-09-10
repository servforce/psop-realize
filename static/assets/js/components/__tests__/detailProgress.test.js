import { readFileSync } from 'node:fs';
import videosPage from '../videosPage.js';
import { stageCount, statusLabel } from '../../utils/video.js';

const markup = readFileSync(new URL('../../../../pages/videos.html', import.meta.url), 'utf8');
const upload = readFileSync(new URL('../../../../pages/uploads.html', import.meta.url), 'utf8');

test('detail progress lives in the header status rather than a separate progress bar', () => {
  const detail = markup.slice(markup.indexOf('<template x-if="id">'));
  const header = detail.slice(0, detail.indexOf('<div x-show="loading" class="p-4"'));
  expect(header).toContain('id="detail-status"');
  expect(header).toContain('x-text="statusLabel(job)"');
  expect(header).toContain('x-text="stageCount(job)"');
  expect(header).toContain('role="status" aria-live="polite" aria-atomic="true"');
  expect(header).toContain('max-w-[60%]');
  expect(header).toContain('class="truncate"');
  expect(markup).not.toContain('role="progressbar"');
  expect(markup).not.toContain('progress-track');
  expect(upload).toContain('role="progressbar"');
  const page = videosPage();
  expect(page.stageCount).toBe(stageCount);
  page.destroy();
});

test('feedback is conditional and preserves polling, job and parse errors', () => {
  const feedback = markup.match(/<div id="detail-feedback"[\s\S]*?<\/div>/)?.[0];
  expect(feedback).toContain('x-show="pollError || job?.error_message || parseError"');
  for (const field of ['pollError', 'job?.error_message', 'parseError']) {
    expect(feedback).toContain(`x-show="${field}" x-text="${field}"`);
  }
});

test.each([
  [10, 3, '3/10'], ['10', '3', '3/10'], [10, 30, '10/10'], [10, -1, '0/10'],
  [10, undefined, '0/10'], [10, 'invalid', '0/10'], [10, Infinity, '0/10'],
  [0, 3, ''], [-1, 3, ''], [undefined, 3, ''], ['invalid', 3, ''], [Infinity, 3, ''],
])('stage count with total %s and processed %s is %s', (stage_total, stage_processed, expected) => {
  const job = { status: 'processing', current_stage: 'structuring_transcript', stage_total, stage_processed };
  expect(statusLabel(job)).toBe('生成结构化转写');
  expect(stageCount(job)).toBe(expected);
});

test('inactive and terminal jobs never show stale stage counts', () => {
  for (const status of ['uploaded', 'queued', 'completed', 'completed_with_warnings', 'failed', 'deleting', 'unknown']) {
    expect(stageCount({ status, current_stage: 'structuring_transcript', stage_total: 10, stage_processed: 3 })).toBe('');
  }
  for (const job of [null, undefined, {}]) expect(stageCount(job)).toBe('');
  expect(statusLabel({ status: 'completed_with_warnings', current_stage: 'structuring_transcript' })).toBe('完成 · 有警告');
  expect(statusLabel({ status: 'failed', current_stage: 'structuring_transcript' })).toBe('处理失败');
});
