import { mergeVideo, rankedFrames, tone, safeImageUrl, formatDate } from '../video.js';
import { resolveRoute } from '../../components/frame.js';
import { renderMarkdownDocument } from '../markdown.js';
import { createTaskId } from '../../services/upload.js';

test('task IDs are lowercase UUID v4 hex, including secure fallback', () => {
  expect(createTaskId({ randomUUID: () => 'A23E4567-E89B-42D3-A456-426614174000' })).toBe('a23e4567e89b42d3a456426614174000');
  const id = createTaskId({ getRandomValues: bytes => bytes.fill(0) });
  expect(id).toMatch(/^[a-f0-9]{12}4[a-f0-9]{3}[89ab][a-f0-9]{15}$/);
  expect(() => createTaskId({})).toThrow();
});
test('older list responses cannot overwrite completed tasks', () => {
  const videos = [{id:'a', status:'completed', updated_at:'2026-09-10T00:00:02'}];
  expect(mergeVideo(videos, {id:'a',status:'processing',updated_at:'2026-09-10T00:00:01'})).toBe(false);
  expect(mergeVideo(videos, {id:'a',status:'processing',updated_at:'2026-09-10T00:00:03'})).toBe(true);
});
test('frames deduplicate by highest score and display only top five', () => {
  const frames = Array.from({length:8}, (_,i) => ({id:String(i),url:`/api/frame/${i}`,score:i/10}));
  const ranked = rankedFrames({query_graph_matches:[{frames}, {frames:[{...frames[0],score:.99}]}]});
  expect(ranked).toHaveLength(5); expect(ranked[0].id).toBe('0'); expect(ranked[4].rank).toBe(5);
});
test('semantic status colors do not use primary for success', () => {
  expect(tone('completed')).toBe('badge-success'); expect(tone('failed')).toBe('badge-danger'); expect(tone('processing')).toBe('badge-info');
});
test('history routes are explicit, malformed routes are rejected', () => {
  expect(resolveRoute('/videos/abc').id).toBe('abc'); expect(resolveRoute('/usage').key).toBe('usage');
  expect(resolveRoute('/videos/%')).toBeNull(); expect(resolveRoute('/api/videos')).toBeNull();
});
test('Markdown renders rich text without executable HTML or javascript URLs', () => {
  const html = renderMarkdownDocument('# 标题\n\n<script>alert(1)</script>\n\n[链接](javascript:alert(1))\n\n| 名称 | 数值 |\n| --- | --- |\n| 测试 | 2 |');
  expect(html).toContain('<h1>'); expect(html).toContain('<table');
  expect(html).not.toContain('<script>'); expect(html).not.toContain('href="javascript:');
});
test('unsafe preview URLs are rejected', () => {
  expect(safeImageUrl('javascript:alert(1)')).toBe(''); expect(safeImageUrl('//evil.test/x')).toBe(''); expect(safeImageUrl('/api/frames/a')).toBe('/api/frames/a');
});
test('backend naive dates are interpreted as UTC and displayed in Beijing time', () => {
  expect(formatDate('2026-09-10T00:00:00')).toContain('08:00'); expect(formatDate('bad-date')).toBe('—');
});
