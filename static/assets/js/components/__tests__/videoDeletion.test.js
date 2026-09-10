import { jest } from '@jest/globals';
import videosPage from '../videosPage.js';
import { createVideoCatalog } from '../../services/videoCatalog.js';

let page, app, dialog, opener, originalFetch;
beforeEach(() => {
  originalFetch = global.fetch;
  opener = { isConnected: true, focus: jest.fn() };
  global.document = { activeElement: opener };
  app = { ...createVideoCatalog(), upload: { busy: false, id: '' }, notify: jest.fn(), deleteVideo: jest.fn().mockResolvedValue(undefined) };
  page = Object.assign(videosPage(), { $store: { app } });
  page.refresh = jest.fn().mockResolvedValue(undefined);
  dialog = { open: false, showModal: jest.fn(() => { dialog.open = true; }), close: jest.fn(() => { dialog.open = false; page.afterDeleteClose(); }) };
  page.$refs = { deleteDialog: dialog, listHeading: { focus: jest.fn() } };
});
afterEach(() => { page.destroy(); delete global.document; global.fetch = originalFetch; });
const video = (status = 'completed') => ({ id: 'target', title: '演示视频', status });

test('cancel closes the confirmation without calling DELETE and restores focus', () => {
  page.confirmDelete(video());
  expect(dialog.open).toBe(true);
  expect(app.deleteVideo).not.toHaveBeenCalled();
  page.closeDelete();
  expect(dialog.open).toBe(false);
  expect(page.deleteTarget).toBeNull();
  expect(opener.focus).toHaveBeenCalled();
});

test('running uploads, queued tasks and unknown states cannot open deletion', () => {
  for (const status of ['queued', 'processing', 'unknown']) page.confirmDelete(video(status));
  app.upload = { busy: true, id: 'target' };
  page.confirmDelete(video());
  expect(dialog.showModal).not.toHaveBeenCalled();
});

test('status changes after confirmation are checked before sending DELETE', async () => {
  const job = video();
  page.confirmDelete(job);
  job.status = 'processing';
  await page.deleteConfirmed();
  expect(app.deleteVideo).not.toHaveBeenCalled();
  expect(page.deleteError).toContain('任务状态已改变');
});

test('only one DELETE is sent while pending, with cancellation disabled', async () => {
  let finish;
  app.deleteVideo.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  page.confirmDelete(video());
  const first = page.deleteConfirmed();
  await page.deleteConfirmed();
  page.closeDelete();
  expect(app.deleteVideo).toHaveBeenCalledTimes(1);
  expect(page.deleteBusy).toBe(true);
  expect(dialog.open).toBe(true);
  finish(); await first;
  expect(page.deleteBusy).toBe(false);
  expect(dialog.open).toBe(false);
});

test('failure keeps the row and confirmation available for retry', async () => {
  app.accept(video());
  app.deleteVideo.mockRejectedValueOnce(new Error('存储服务不可用'));
  page.confirmDelete(app.videos[0]);
  await page.deleteConfirmed();
  expect(app.videos).toHaveLength(1);
  expect(dialog.open).toBe(true);
  expect(page.deleteError).toContain('存储服务不可用');
  expect(page.deleteBusy).toBe(false);
  await page.deleteConfirmed();
  expect(app.deleteVideo).toHaveBeenCalledTimes(2);
  expect(dialog.open).toBe(false);
});

test('pending deletion survives route destruction without accessing destroyed refs', async () => {
  let finish;
  app.deleteVideo.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  page.confirmDelete(video());
  const pending = page.deleteConfirmed();
  page.destroy();
  page.$refs = {};
  finish(); await pending;
  expect(dialog.close).not.toHaveBeenCalled();
});

test('late errors after navigation are reported globally', async () => {
  let fail;
  app.deleteVideo.mockImplementation(() => new Promise((_, reject) => { fail = reject; }));
  page.confirmDelete(video());
  const pending = page.deleteConfirmed();
  page.destroy();
  fail(new Error('连接中断')); await pending;
  expect(app.notify).toHaveBeenCalledWith('删除失败：连接中断', true);
});

test('deleted IDs cannot be resurrected by stale list or upload responses', async () => {
  let respond;
  global.fetch = jest.fn(() => new Promise(resolve => { respond = resolve; }));
  app.accept(video());
  const pending = app.loadVideos();
  app.forget('target');
  respond({ ok: true, json: async () => [video(), { id: 'other', status: 'completed' }] });
  await pending;
  expect(app.accept({ ...video(), updated_at: '2099-01-01' })).toBe(false);
  expect(app.videos.map(job => job.id)).toEqual(['other']);
});
