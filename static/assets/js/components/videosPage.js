import { request, videoUrl, copyText } from '../services/api.js';
import { statusLabel, listStatusLabel, videoStatusOptions, tone, isTerminal, isRunning, formatDate, formatBytes, progressLabel, stageCount, rankedFrames, timestamp, safeImageUrl } from '../utils/video.js';
import { renderMarkdownDocument } from '../utils/markdown.js';
import { trapDialogFocus } from '../utils/dialog.js';

export const artifactTabs = [
  { key: 'transcript', label: '转写文本', endpoint: 'transcript' },
  { key: 'frames', label: '业务帧', endpoint: 'semantic-frames' },
  { key: 'markdown', label: 'Markdown', endpoint: 'markdown' },
];

export default function videosPage() {
  let controller = new AbortController(), timer, artifactSequence = 0, alive = true, deleteOpener;
  let listController, listTimer, listSequence = 0;
  return {
    id: '', loading: true, error: '', query: '', status: '', sort: 'desc', job: null,
    listIds: [], page: 1, pageSize: 20, total: 0, totalPages: 1, listError: '',
    tab: 'transcript', artifactLoading: false, artifactError: '', transcript: '', markdown: '', sections: [], source: false,
    parseBusy: false, parseError: '', pollError: '', copied: '', exportBusy: false,
    deleteTarget: null, deleteBusy: false, deleteError: '',
    statusLabel, listStatusLabel, videoStatusOptions, tone, formatDate, formatBytes, progressLabel, stageCount, isRunning, rankedFrames, timestamp, safeImageUrl, artifactTabs,
    init() {
      this.id = this.$store.app.route.id;
      if (this.id) { this.loadDetail(); return; }
      if (this.$store.app.listState) Object.assign(this, this.$store.app.listState);
      for (const key of ['query', 'status', 'sort', 'pageSize']) this.$watch(key, () => this.applyFilters());
      this.refresh();
    },
    destroy() {
      alive = false; controller.abort(); clearTimeout(timer); artifactSequence += 1;
      listController?.abort(); clearTimeout(listTimer); listSequence += 1;
    },
    get items() {
      const cached = new Map(this.$store.app.videos.map(video => [video.id, video]));
      return this.listIds.map(id => cached.get(id)).filter(Boolean);
    },
    get renderedMarkdown() { return renderMarkdownDocument(this.markdown); },
    get busy() { return this.parseBusy || isRunning(this.job) || this.job?.status === 'deleting'; },
    go(path) { window.dispatchEvent(new CustomEvent('psop:navigate', { detail: path })); },
    open(video) { this.go(`/videos/${encodeURIComponent(video.id)}`); },
    canDelete(video) { return ['uploaded', 'completed', 'completed_with_warnings', 'failed', 'deleting'].includes(video?.status) && !(this.$store.app.upload.busy && this.$store.app.upload.id === video?.id); },
    confirmDelete(video) {
      if (!this.canDelete(video) || this.deleteBusy) return;
      deleteOpener = document.activeElement;
      this.deleteTarget = video; this.deleteError = '';
      this.$refs.deleteDialog.showModal();
    },
    closeDelete() { if (!this.deleteBusy) this.$refs.deleteDialog.close(); },
    afterDeleteClose() {
      this.deleteTarget = null; this.deleteError = '';
      const target = deleteOpener?.isConnected && !deleteOpener.disabled ? deleteOpener : this.$refs.listHeading;
      target?.focus({ preventScroll: true });
      deleteOpener = null;
    },
    trapDeleteFocus(event) { trapDialogFocus(this.$refs.deleteDialog, event); },
    async deleteConfirmed() {
      if (!this.deleteTarget || this.deleteBusy) return;
      if (!this.canDelete(this.deleteTarget)) { this.deleteError = '任务状态已改变，请等待上传或解析结束后再删除。'; return; }
      this.deleteBusy = true; this.deleteError = '';
      try {
        await this.$store.app.deleteVideo(this.deleteTarget.id);
        if (alive) { this.$refs.deleteDialog.close(); await this.refresh(); }
      } catch (error) {
        if (alive) this.deleteError = `删除失败：${error.message}`;
        else this.$store.app.notify(`删除失败：${error.message}`, true);
      } finally { if (alive) this.deleteBusy = false; }
    },
    applyFilters() { this.page = 1; return this.refresh(); },
    changePage(page) {
      if (this.loading || this.deleteBusy || page === this.page || page < 1 || page > this.totalPages) return;
      this.page = page;
      return this.refresh();
    },
    async refresh({ background = false } = {}) {
      if (!alive || this.id) return;
      const token = ++listSequence;
      clearTimeout(listTimer); listController?.abort(); listController = new AbortController();
      if (!background) { this.loading = true; this.error = ''; }
      this.listError = '';
      this.$store.app.listState = { page: this.page, pageSize: this.pageSize, query: this.query, status: this.status, sort: this.sort };
      const params = new URLSearchParams({ page: this.page, page_size: this.pageSize, query: this.query.trim(), status: this.status, sort: this.sort });
      try {
        const result = await request(`/api/videos?${params}`, { signal: listController.signal });
        if (!alive || token !== listSequence) return;
        if (!Array.isArray(result?.items) || !Number.isInteger(result.total) || result.total < 0 ||
            !Number.isInteger(result.page) || result.page < 1 || !Number.isInteger(result.total_pages) || result.total_pages < result.page) {
          throw new Error('视频分页响应格式不正确');
        }
        for (const item of result.items) this.$store.app.accept(item);
        this.listIds = result.items.map(item => item.id);
        this.page = result.page; this.total = result.total; this.totalPages = result.total_pages;
        this.$store.app.listState.page = result.page;
        if (!background) this.$refs?.listRegion?.scrollTo({ top: 0 });
      } catch (error) {
        if (alive && token === listSequence && error.name !== 'AbortError') {
          if (background) this.listError = '列表更新暂时失败，正在重试…';
          else { this.error = error.message; this.listIds = []; }
        }
      } finally {
        if (alive && token === listSequence) {
          this.loading = false;
          // Keep statuses and newly uploaded videos current without a refresh button.
          if (!this.error) listTimer = setTimeout(() => this.refresh({ background: true }), 5000);
        }
      }
    },
    async loadDetail() {
      this.loading = true; this.error = '';
      try {
        const job = await request(videoUrl(this.id), { signal: controller.signal });
        if (!alive) return;
        this.$store.app.accept(job); this.job = this.$store.app.videos.find(item => item.id === this.id);
        await this.loadArtifact(); this.startPolling();
      } catch (error) { if (alive && error.name !== 'AbortError') this.error = error.status === 404 ? '视频不存在或已不可访问。' : error.message; }
      finally { if (alive) this.loading = false; }
    },
    async changeTab(tab) { if (!artifactTabs.some(item => item.key === tab)) return; this.tab = tab; await this.loadArtifact(); },
    async loadArtifact() {
      const token = ++artifactSequence, tab = this.tab;
      this.artifactError = ''; this.artifactLoading = true;
      try {
        const endpoint = artifactTabs.find(item => item.key === tab)?.endpoint;
        if (!endpoint) throw new Error('不支持的解析产物类型');
        const result = await request(`${videoUrl(this.id)}/${endpoint}`, { text: tab === 'markdown', signal: controller.signal });
        if (!alive || token !== artifactSequence) return;
        if (tab === 'transcript') this.transcript = result.rendered_text || result.text || '';
        if (tab === 'frames') this.sections = Array.isArray(result.sections) ? result.sections : [];
        if (tab === 'markdown') this.markdown = result;
      } catch (error) { if (alive && token === artifactSequence && error.name !== 'AbortError') this.artifactError = error.message; }
      finally { if (alive && token === artifactSequence) this.artifactLoading = false; }
    },
    startPolling() {
      clearTimeout(timer);
      if (!alive || !this.job || isTerminal(this.job)) return;
      timer = setTimeout(async () => {
        try {
          const job = await request(videoUrl(this.id), { signal: controller.signal });
          if (!alive) return;
          this.$store.app.accept(job); this.job = this.$store.app.videos.find(item => item.id === this.id); this.pollError = '';
          if (isTerminal(this.job)) await this.loadArtifact();
        } catch (error) { if (alive && error.name !== 'AbortError') this.pollError = '进度连接暂时中断，正在重试…'; }
        finally { if (alive) this.startPolling(); }
      }, 2000);
    },
    async parse(mode) {
      if (this.busy || !['full', 'transcript', 'keyframes', 'markdown'].includes(mode)) return;
      this.parseBusy = true; this.parseError = '';
      // Start GET observation before the long POST. Leaving the page cancels observation, not the server task.
      this.job = { ...this.job, status: 'processing', stage_processed: 0, stage_total: 0, stage_message: '', current_stage: { full: 'processing', transcript: 'transcribing_asr', keyframes: 'extracting_keyframes', markdown: 'generating_markdown' }[mode] };
      this.$store.app.markParseStarted(this.id);
      this.startPolling();
      try {
        const payload = await request(`${videoUrl(this.id)}/parse?mode=${encodeURIComponent(mode)}`, { method: 'POST', signal: controller.signal });
        if (!alive) return;
        if (payload.job) this.$store.app.accept(payload.job);
        this.job = this.$store.app.videos.find(item => item.id === this.id) || this.job;
        this.tab = { keyframes: 'frames', markdown: 'markdown' }[mode] || 'transcript';
        await this.loadArtifact();
      } catch (error) {
        if (alive && error.name !== 'AbortError') {
          this.parseError = `解析请求未正常返回：${error.message}。将继续查询任务状态。`;
          try { const job = await request(videoUrl(this.id), { signal: controller.signal }); if (alive) { this.$store.app.accept(job); this.job = this.$store.app.videos.find(item => item.id === this.id); } } catch {}
        }
      } finally { if (alive) { this.parseBusy = false; this.startPolling(); } }
    },
    async copy(value, key) { try { await copyText(String(value)); this.copied = key; this.$store.app.notify('已复制到剪贴板'); } catch (error) { this.$store.app.notify(error.message, true); } },
    preview(url) { this.$store.app.preview(safeImageUrl(url)); },
    async exportResult() {
      this.exportBusy = true;
      try {
        const response = await fetch(`${videoUrl(this.id)}/export`, { signal: controller.signal });
        if (!response.ok) throw new Error(`导出失败（HTTP ${response.status}）`);
        const url = URL.createObjectURL(await response.blob());
        const link = document.createElement('a'); link.href = url; link.download = `video_${this.id}.zip`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (error) { if (error.name !== 'AbortError') this.$store.app.notify(error.message, true); }
      finally { if (alive) this.exportBusy = false; }
    },
  };
}
