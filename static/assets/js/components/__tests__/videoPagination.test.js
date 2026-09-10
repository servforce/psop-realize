import { jest } from '@jest/globals';
import { readFileSync } from 'node:fs';
import videosPage from '../videosPage.js';
import { createVideoCatalog } from '../../services/videoCatalog.js';

const markup = readFileSync(new URL('../../../../pages/videos.html', import.meta.url), 'utf8');
const video = (id, status = 'completed') => ({ id, status, title: id, created_at: '2026-09-10T00:00:00Z' });
const payload = (ids, page = 1, total = 65) => ({ items: ids.map(id => video(id)), page, page_size: 20, total, total_pages: Math.max(1, Math.ceil(total / 20)) });
const response = data => ({ ok: true, json: async () => data });
const flush = async () => { for (let i = 0; i < 12; i += 1) await Promise.resolve(); };
let page, app, originalFetch;
beforeEach(() => {
  jest.useFakeTimers();
  originalFetch = global.fetch;
  global.fetch = jest.fn().mockResolvedValue(response(payload(['first'])));
  app = { ...createVideoCatalog(), route: { id: '' }, upload: { busy: false }, notify: jest.fn() };
  page = Object.assign(videosPage(), { $store: { app }, $watch: jest.fn(), $refs: { listRegion: { scrollTo: jest.fn() } } });
});
afterEach(() => { page.destroy(); jest.useRealTimers(); global.fetch = originalFetch; });

test('upload shares the filter row; the old statistics and refresh control are gone', () => {
  const list = markup.slice(0, markup.indexOf('<template x-if="id">'));
  const toolbar = list.slice(list.indexOf('id="video-filters"'), list.indexOf('<p x-show="listError'));
  expect(toolbar).toContain('id="upload-video-button"');
  expect(toolbar).toContain('id="video-search"');
  expect(toolbar).toContain('overflow-x-auto whitespace-nowrap');
  expect(toolbar).toContain('x-model.debounce.300ms="query"');
  expect(list).not.toContain('counts.');
  expect(list).not.toContain('刷新文件解析');
  expect(list).toContain('aria-label="文件解析分页"');
  expect(list).toContain('x-model.number="pageSize"');
  expect(list).toContain('<option value="20">20 条/页</option>');
  expect(list).toContain(':selected="status === option.value"');
  expect(list).toContain('aria-label="上一页"');
  expect(list).toContain('aria-label="下一页"');
});

test('only server-selected rows are displayed, in server order, not the entire cache', async () => {
  app.accept(video('cached-other-page'));
  fetch.mockResolvedValueOnce(response(payload(['second', 'first'])));
  await page.refresh();
  expect(fetch).toHaveBeenCalledWith('/api/videos?page=1&page_size=20&query=&status=&sort=desc', expect.any(Object));
  expect(page.items.map(item => item.id)).toEqual(['second', 'first']);
  expect(page.totalPages).toBe(4);
  expect(page.$refs.listRegion.scrollTo).toHaveBeenCalledWith({ top: 0 });
  fetch.mockResolvedValueOnce(response(payload(['third'], 2)));
  await page.changePage(2);
  expect(page.items.map(item => item.id)).toEqual(['third']);
  expect(fetch.mock.lastCall[0]).toContain('page=2');
});

test('search, status, sorting and page size all reset pagination and query the API', async () => {
  page.init(); await flush();
  expect(page.$watch.mock.calls.map(([key]) => key)).toEqual(['query', 'status', 'sort', 'pageSize']);
  for (const [key, value] of [['query', '  机械臂 & RAM  '], ['status', 'processing'], ['sort', 'asc'], ['pageSize', 50]]) {
    page.page = 3; page[key] = value;
    const callback = page.$watch.mock.calls.find(([watched]) => watched === key)[1];
    await callback();
    const params = new URL(fetch.mock.lastCall[0], 'http://localhost').searchParams;
    expect(params.get('page')).toBe('1');
    expect(params.get(key === 'pageSize' ? 'page_size' : key)).toBe(String(value).trim());
  }
});

test.each(['uploaded', 'queued', 'processing', 'completed', 'completed_with_warnings', 'failed', 'deleting'])('status %s is passed to the database query', async status => {
  page.status = status;
  await page.applyFilters();
  expect(new URL(fetch.mock.lastCall[0], 'http://localhost').searchParams.get('status')).toBe(status);
});

test('empty results have one valid page and cannot page beyond their bounds', async () => {
  fetch.mockResolvedValueOnce(response(payload([], 1, 0)));
  await page.refresh();
  await page.changePage(0); await page.changePage(2); await page.changePage(1);
  expect(page.items).toEqual([]);
  expect(page.page).toBe(1);
  expect(page.totalPages).toBe(1);
  expect(fetch).toHaveBeenCalledTimes(1);
});

test('a late previous query cannot overwrite or pollute the newer page', async () => {
  let respond;
  fetch.mockImplementationOnce(() => new Promise(resolve => { respond = resolve; }));
  const older = page.refresh();
  const oldSignal = fetch.mock.calls[0][1].signal;
  page.query = 'new';
  fetch.mockResolvedValueOnce(response(payload(['new-result'])));
  await page.applyFilters();
  expect(oldSignal.aborted).toBe(true);
  respond(response(payload(['stale-result'], 3)));
  await older;
  expect(page.items.map(item => item.id)).toEqual(['new-result']);
  expect(app.videos.some(item => item.id === 'stale-result')).toBe(false);
  expect(page.page).toBe(1);
});

test('pagination controls do not start duplicate requests while loading or deleting', async () => {
  page.totalPages = 4;
  page.loading = true;
  await page.changePage(2);
  page.loading = false; page.deleteBusy = true;
  await page.changePage(2);
  expect(fetch).not.toHaveBeenCalled();
});

test('deletion re-queries the current page and accepts the server-clamped last page', async () => {
  const target = video('last');
  app.accept(target); app.deleteVideo = jest.fn(async id => app.forget(id));
  page.page = 4; page.totalPages = 4; page.listIds = ['last']; page.deleteTarget = target;
  page.$refs.deleteDialog = { close: jest.fn() };
  fetch.mockResolvedValueOnce(response(payload(['previous-page'], 3, 60)));
  await page.deleteConfirmed();
  expect(app.deleteVideo).toHaveBeenCalledWith('last');
  expect(fetch.mock.lastCall[0]).toContain('page=4');
  expect(page.page).toBe(3);
  expect(page.totalPages).toBe(3);
  expect(page.items.map(item => item.id)).toEqual(['previous-page']);
  expect(page.$refs.deleteDialog.close).toHaveBeenCalled();
});

test('returning from a detail page restores list criteria and page, without sharing row membership', async () => {
  Object.assign(page, { query: 'install', status: 'completed', page: 3 });
  fetch.mockResolvedValueOnce(response(payload(['third'], 3)));
  await page.refresh();
  page.destroy();
  page = Object.assign(videosPage(), { $store: { app }, $watch: jest.fn() });
  fetch.mockResolvedValueOnce(response(payload(['restored'], 3)));
  page.init(); await flush();
  expect(fetch.mock.lastCall[0]).toBe('/api/videos?page=3&page_size=20&query=install&status=completed&sort=desc');
  expect(page.items.map(item => item.id)).toEqual(['restored']);
});

test('quiet refresh retains content and scroll on failure and recovers without a refresh button', async () => {
  await page.refresh();
  page.$refs.listRegion.scrollTo.mockClear();
  fetch.mockRejectedValueOnce(new Error('offline'));
  await jest.advanceTimersByTimeAsync(5000);
  expect(page.items.map(item => item.id)).toEqual(['first']);
  expect(page.listError).toContain('正在重试');
  expect(page.error).toBe('');
  expect(page.loading).toBe(false);
  fetch.mockResolvedValueOnce(response(payload(['updated'])));
  await jest.advanceTimersByTimeAsync(5000);
  expect(page.items.map(item => item.id)).toEqual(['updated']);
  expect(page.listError).toBe('');
  expect(page.$refs.listRegion.scrollTo).not.toHaveBeenCalled();
});

test('foreground failure hides stale rows; retry reads the requested page', async () => {
  await page.refresh();
  fetch.mockRejectedValueOnce(new Error('offline'));
  await page.changePage(2);
  expect(page.items).toEqual([]);
  expect(page.error).toBe('offline');
  fetch.mockResolvedValueOnce(response(payload(['retry'], 2)));
  await page.refresh();
  expect(page.error).toBe('');
  expect(page.items[0].id).toBe('retry');
  expect(page.page).toBe(2);
});

test('legacy or malformed responses cannot silently masquerade as a complete paginated result', async () => {
  fetch.mockResolvedValueOnce(response([video('legacy')]));
  await page.refresh();
  expect(page.error).toContain('分页响应格式');
});

test('leaving the list aborts pending requests and clears automatic refresh', async () => {
  await page.refresh();
  let respond;
  fetch.mockImplementationOnce(() => new Promise(resolve => { respond = resolve; }));
  const pending = page.refresh();
  const signal = fetch.mock.lastCall[1].signal;
  page.destroy();
  expect(signal.aborted).toBe(true);
  respond(response(payload(['late']))); await pending;
  await jest.advanceTimersByTimeAsync(10000);
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(page.items.map(item => item.id)).toEqual(['first']);
  expect(app.videos.some(item => item.id === 'late')).toBe(false);
});
