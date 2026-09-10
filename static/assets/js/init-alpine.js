import Alpine from '/node_modules/alpinejs/dist/module.esm.js';
import frame from './components/frame.js';
import videosPage from './components/videosPage.js';
import uploadsPage from './components/uploadsPage.js';
import uploadDrawer from './components/uploadDrawer.js';
import usagePage from './components/usagePage.js';
import { createUploadController } from './services/upload.js';
import { request } from './services/api.js';
import { safeImageUrl, isRunning } from './utils/video.js';
import { createVideoCatalog } from './services/videoCatalog.js';

window.Alpine = Alpine;
let uploader, toastTimer, previousFocus;
Alpine.store('app', {
  ...createVideoCatalog(),
  route: { key: 'videos', id: '' }, health: 'checking', toast: '', toastError: false, image: '',
  upload: { busy: false, id: '', filename: '', registered: false, percent: 0, indeterminate: false, message: '', error: '', job: null },
  markParseStarted(id) { const job = this.videos.find(item => item.id === id); if (job) { job.status = 'processing'; job.current_stage = 'processing'; } },
  async deleteVideo(id) {
    if (isRunning(this.videos.find(video => video.id === id)) || (this.upload.busy && this.upload.id === id)) {
      throw new Error('上传或解析中的视频不能删除，请等待任务结束。');
    }
    // Do not cancel an admitted deletion when a route is destroyed.
    try {
      const result = await request(`/api/videos/${encodeURIComponent(id)}`, { method: 'DELETE' });
      if (result?.deleted !== true || result.id !== id) throw new Error('服务未确认删除结果，请刷新列表后重试。');
    }
    catch (error) { if (error.status !== 404) throw error; }
    this.forget(id);
    if (this.upload.id === id) Object.assign(this.upload, { id: '', filename: '', registered: false, job: null, percent: 0, message: '', error: '' });
    this.notify('视频及解析产物已删除');
  },
  async checkHealth() { this.health = 'checking'; try { const result = await request('/health'); this.health = result.ok ? 'online' : 'offline'; } catch { this.health = 'offline'; } },
  startUpload(file, title) { return uploader.start(file, title); },
  notify(message, error = false) { this.toast = message; this.toastError = error; clearTimeout(toastTimer); toastTimer = setTimeout(() => { this.toast = ''; }, 4000); },
  preview(url) { const safe = safeImageUrl(url); if (!safe) return; previousFocus = document.activeElement; this.image = safe; document.getElementById('image-preview').showModal(); },
  closePreview() { document.getElementById('image-preview').close(); this.image = ''; previousFocus?.focus(); },
});
uploader = createUploadController({
  onChange: patch => Object.assign(Alpine.store('app').upload, patch),
  onVideo: job => Alpine.store('app').accept(job),
});
Alpine.data('frame', frame);
Alpine.data('videosPage', videosPage);
Alpine.data('uploadsPage', uploadsPage);
Alpine.data('uploadDrawer', uploadDrawer);
Alpine.data('usagePage', usagePage);
Alpine.start();
window.addEventListener('beforeunload', event => { if (Alpine.store('app').upload.busy) { event.preventDefault(); event.returnValue = ''; } });
