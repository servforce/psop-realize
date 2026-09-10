import { trapDialogFocus } from '../utils/dialog.js';

// The drawer owns only its presentation. Upload requests and polling stay in the app store.
export default function uploadDrawer() {
  let dialog, opener, closeTimer, controller, sequence = 0, openListener, closeListener;
  return {
    loading: false, loaded: false, error: '', closing: false,
    init() {
      dialog = this.$el;
      openListener = () => this.open();
      closeListener = () => this.close();
      window.addEventListener('psop:upload-open', openListener);
      window.addEventListener('psop:upload-close', closeListener);
    },
    destroy() {
      sequence += 1;
      controller?.abort();
      clearTimeout(closeTimer);
      window.removeEventListener('psop:upload-open', openListener);
      window.removeEventListener('psop:upload-close', closeListener);
      if (dialog.open) dialog.close();
    },
    open() {
      clearTimeout(closeTimer);
      this.closing = false;
      if (!dialog.open) {
        opener = document.activeElement;
        dialog.showModal();
      }
      if (!this.loaded && !this.loading) this.load();
    },
    close() {
      if (!dialog.open || this.closing) return;
      this.closing = true;
      const delay = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 200;
      closeTimer = setTimeout(() => dialog.close(), delay);
    },
    afterClose() {
      // A close event may arrive after a rapid reopen. Do not steal focus from the drawer.
      if (dialog.open) return;
      clearTimeout(closeTimer);
      this.closing = false;
      const target = opener?.isConnected && opener.checkVisibility() ? opener : document.getElementById('main-content');
      target?.focus({ preventScroll: true });
      opener = null;
    },
    trapFocus(event) {
      trapDialogFocus(dialog, event);
    },
    async load() {
      const token = ++sequence;
      controller?.abort();
      controller = new AbortController();
      this.loading = true;
      this.error = '';
      try {
        const response = await fetch('pages/uploads.html', { signal: controller.signal, cache: 'no-store' });
        if (!response.ok) throw new Error(`上传表单加载失败（HTTP ${response.status}）`);
        const html = await response.text();
        if (token !== sequence) return;
        const container = this.$refs.content;
        window.Alpine.mutateDom(() => {
          container.innerHTML = html;
          window.Alpine.initTree(container);
        });
        this.loaded = true;
      } catch (error) {
        if (token === sequence && error.name !== 'AbortError') {
          this.error = error.name === 'TypeError' ? '无法加载上传表单，请检查连接后重试。' : error.message;
        }
      } finally {
        if (token === sequence) this.loading = false;
      }
    },
  };
}
