import { jest } from '@jest/globals';
import uploadDrawer from '../uploadDrawer.js';
import { menuItems, resolveRoute } from '../frame.js';

const flush = async () => { for (let i = 0; i < 6; i += 1) await Promise.resolve(); };
let drawer, dialog, content, opener, fallback;

beforeEach(() => {
  jest.useFakeTimers();
  const events = new EventTarget();
  global.window = {
    addEventListener: events.addEventListener.bind(events),
    removeEventListener: events.removeEventListener.bind(events),
    dispatchEvent: events.dispatchEvent.bind(events),
    matchMedia: () => ({ matches: false }),
    Alpine: { mutateDom: callback => callback(), initTree: jest.fn() },
  };
  opener = { isConnected: true, checkVisibility: () => true, focus: jest.fn() };
  fallback = { focus: jest.fn() };
  global.document = { activeElement: opener, getElementById: () => fallback };
  global.fetch = jest.fn().mockResolvedValue({ ok: true, text: async () => '<section x-data="uploadsPage"></section>' });
  content = { innerHTML: '' };
  dialog = {
    open: false,
    showModal: jest.fn(() => { dialog.open = true; }),
    close: jest.fn(() => { dialog.open = false; drawer.afterClose(); }),
  };
  drawer = Object.assign(uploadDrawer(), { $el: dialog, $refs: { content } });
  drawer.init();
});

afterEach(() => {
  drawer.destroy();
  jest.useRealTimers();
  jest.restoreAllMocks();
  delete global.window;
  delete global.document;
  delete global.fetch;
});

test('navigation contains only file parsing and usage, old upload URLs open the drawer', () => {
  expect(menuItems.map(item => item.label)).toEqual(['文件解析', '用量统计']);
  expect(menuItems.some(item => item.key === 'uploads')).toBe(false);
  expect(resolveRoute('/videos').title).toBe('文件解析');
  for (const path of ['/uploads', '/uploads/']) {
    expect(resolveRoute(path)).toEqual({ key: 'videos', id: '', title: '文件解析', upload: true });
  }
});

test('opens on demand and preserves the mounted form across close and reopen', async () => {
  expect(fetch).not.toHaveBeenCalled();
  window.dispatchEvent(new Event('psop:upload-open'));
  await flush();
  expect(dialog.open).toBe(true);
  expect(drawer.loaded).toBe(true);
  expect(window.Alpine.initTree).toHaveBeenCalledWith(content);
  window.dispatchEvent(new Event('psop:upload-close'));
  expect(dialog.open).toBe(true); // Keep the native focus trap until the slide-out finishes.
  jest.advanceTimersByTime(200);
  expect(dialog.open).toBe(false);
  expect(opener.focus).toHaveBeenCalledWith({ preventScroll: true });
  drawer.open();
  await flush();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(window.Alpine.initTree).toHaveBeenCalledTimes(1);
});

test('closing while loading neither aborts the form nor starts a second request on reopen', async () => {
  let resolve;
  fetch.mockImplementation(() => new Promise(done => { resolve = done; }));
  drawer.open();
  const signal = fetch.mock.calls[0][1].signal;
  drawer.close();
  jest.advanceTimersByTime(200);
  drawer.open();
  expect(signal.aborted).toBe(false);
  expect(fetch).toHaveBeenCalledTimes(1);
  resolve({ ok: true, text: async () => '<section></section>' });
  await flush();
  expect(drawer.loaded).toBe(true);
});

test('a failed fragment fetch can be retried without losing the close control', async () => {
  fetch.mockResolvedValueOnce({ ok: false, status: 503 });
  drawer.open();
  await flush();
  expect(drawer.error).toContain('503');
  expect(drawer.loaded).toBe(false);
  expect(dialog.open).toBe(true);
  await drawer.load();
  expect(drawer.error).toBe('');
  expect(drawer.loaded).toBe(true);
});

test('rapid reopen cancels the pending close and stale close events do not steal focus', async () => {
  drawer.open();
  await flush();
  drawer.close();
  jest.advanceTimersByTime(100);
  drawer.open();
  jest.advanceTimersByTime(200);
  drawer.afterClose();
  expect(dialog.open).toBe(true);
  expect(drawer.closing).toBe(false);
  expect(opener.focus).not.toHaveBeenCalled();
});

test('reduced motion closes immediately and navigation restores focus to the main region', () => {
  window.matchMedia = () => ({ matches: true });
  drawer.open();
  opener.isConnected = false;
  drawer.close();
  jest.advanceTimersByTime(0);
  expect(dialog.open).toBe(false);
  expect(fallback.focus).toHaveBeenCalledWith({ preventScroll: true });
});

test('Tab wraps between visible enabled controls instead of escaping the drawer', () => {
  const control = (props = {}) => ({ tabIndex: 0, disabled: false, checkVisibility: () => true, focus: jest.fn(), ...props });
  const first = control(), last = control();
  dialog.querySelectorAll = () => [first, control({ disabled: true }), last, control({ checkVisibility: () => false })];
  dialog.contains = () => true;
  document.activeElement = first;
  const backwards = { shiftKey: true, preventDefault: jest.fn() };
  drawer.trapFocus(backwards);
  expect(backwards.preventDefault).toHaveBeenCalled();
  expect(last.focus).toHaveBeenCalled();
  document.activeElement = last;
  const forwards = { shiftKey: false, preventDefault: jest.fn() };
  drawer.trapFocus(forwards);
  expect(forwards.preventDefault).toHaveBeenCalled();
  expect(first.focus).toHaveBeenCalled();
});

test('destroy aborts stale fragment responses and removes window listeners', async () => {
  let resolve;
  fetch.mockImplementation(() => new Promise(done => { resolve = done; }));
  drawer.open();
  const signal = fetch.mock.calls[0][1].signal;
  drawer.destroy();
  expect(signal.aborted).toBe(true);
  resolve({ ok: true, text: async () => '<section>stale</section>' });
  await flush();
  expect(content.innerHTML).toBe('');
  window.dispatchEvent(new Event('psop:upload-open'));
  expect(dialog.open).toBe(false);
});
