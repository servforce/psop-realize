import { isTerminal, statusLabel, progressLabel, shouldIgnoreVideoUpdate } from '../utils/video.js';

export function createTaskId(crypto = globalThis.crypto) {
  if (crypto?.randomUUID) return crypto.randomUUID().replace(/-/g, '').toLowerCase();
  if (!crypto?.getRandomValues) throw new Error('浏览器不支持安全任务 ID，请使用 HTTPS 或 localhost。');
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
  return [...bytes].map(value => value.toString(16).padStart(2, '0')).join('');
}

// Lives outside page components: navigating away does not interrupt a long POST.
export function createUploadController({ onChange, onVideo, fetchImpl = globalThis.fetch, xhrFactory = () => new XMLHttpRequest(), crypto = globalThis.crypto, schedule = setTimeout, cancel = clearTimeout, Form = globalThis.FormData }) {
  let attempt = 0, current = null, timer = null;
  function clearTimer() { if (timer !== null) cancel(timer); timer = null; }
  function emit(patch) { onChange(patch); }
  function active(run) { return current === run && run.attempt === attempt; }
  function finish(run, patch) {
    if (!active(run)) return;
    run.done = true; clearTimer(); emit({ busy: false, indeterminate: false, ...patch });
  }
  function accept(run, job) {
    if (!active(run) || run.done || !job?.id || job.id !== run.id) return;
    if (run.job && shouldIgnoreVideoUpdate(run.job, job)) return;
    run.job = job; run.seen = true; onVideo(job);
    emit({ registered: true, job, message: `${statusLabel(job)} · ${progressLabel(job)}` });
    if (isTerminal(job)) finish(run, { percent: job.status === 'failed' ? 0 : 100, error: job.status === 'failed' ? job.error_message || '视频解析失败' : '', message: job.status === 'failed' ? '解析失败，可在视频详情中重试' : statusLabel(job) });
    else emit({ indeterminate: true });
  }
  async function poll(run) {
    if (!active(run) || run.done) return;
    try {
      const response = await fetchImpl(`/api/videos/${encodeURIComponent(run.id)}`, { cache: 'no-store' });
      if (!active(run) || run.done) return;
      if (response.status === 404) {
        if (run.networkFailed && !run.seen) run.missing += 1;
      } else if (!response.ok) throw new Error('暂时无法查询任务');
      else { accept(run, await response.json()); run.missing = 0; }
    } catch {
      if (!active(run) || run.done) return;
      if (run.networkFailed && !run.seen) run.missing += 1;
      emit({ message: '连接暂时不可用，正在重试任务状态查询' });
    } finally {
      if (active(run) && !run.done) {
        if (run.missing >= 150) finish(run, { percent: 0, error: '连接失败且服务端未创建任务，请重新上传。' });
        else timer = schedule(() => poll(run), 2000);
      }
    }
  }
  function networkFailed(run) {
    if (!active(run) || run.done) return;
    run.networkFailed = true;
    emit({ indeterminate: true, message: run.seen ? '上传连接已中断，后台任务继续运行，正在查询进度' : '上传连接异常，正在确认服务端是否已创建任务' });
  }
  return {
    start(file, title = '') {
      if (current && !current.done) throw new Error('当前上传与解析任务尚未结束。');
      if (!file?.size) throw new Error('请选择非空视频文件。');
      if (!/^video\//.test(file.type || '') && !/\.(mp4|mov|webm|m4v|mkv)$/i.test(file.name)) throw new Error('请选择 MP4、MOV、WebM、M4V 或 MKV 视频。');
      const id = createTaskId(crypto);
      clearTimer();
      const run = { id, attempt: ++attempt, done: false, seen: false, networkFailed: false, missing: 0, job: null };
      current = run;
      emit({ busy: true, id, filename: file.name, percent: 0, registered: false, indeterminate: false, message: '正在上传视频', error: '', job: null });
      const xhr = xhrFactory(); run.xhr = xhr;
      const form = new Form(); form.append('file', file); form.append('title', title.trim() || file.name); form.append('task_id', id);
      xhr.upload.onprogress = event => {
        if (!active(run) || run.done || !event.lengthComputable || run.seen) return;
        const percent = Math.round(event.loaded / event.total * 100);
        emit({ percent, indeterminate: percent === 100, message: percent === 100 ? '服务器已接收，正在保存并准备解析' : `上传到服务器 ${percent}%` });
      };
      xhr.onload = () => {
        if (!active(run) || run.done) return;
        let payload = {}; try { payload = JSON.parse(xhr.responseText || '{}'); } catch { /* Gateway may return HTML. */ }
        if (payload.job?.id === run.id) {
          const job = xhr.status >= 400 && payload.error && !isTerminal(payload.job)
            ? { ...payload.job, status: 'failed', current_stage: 'failed', error_message: payload.error.message || '视频解析失败' }
            : payload.job;
          accept(run, job); return;
        }
        if ((xhr.status >= 400 && xhr.status < 500 && ![408, 425, 429].includes(xhr.status)) || payload.error?.code === 'VIDEO_UPLOAD_FAILED') {
          finish(run, { percent: 0, registered: false, error: xhr.status === 409 ? '任务 ID 冲突，请重新上传。' : typeof payload.detail === 'string' ? payload.detail : payload.error?.message || `上传失败（HTTP ${xhr.status}）` });
        } else networkFailed(run);
      };
      xhr.onerror = () => networkFailed(run);
      xhr.ontimeout = () => networkFailed(run);
      xhr.onabort = () => networkFailed(run);
      try { xhr.open('POST', '/api/videos/upload-and-parse'); xhr.send(form); timer = schedule(() => poll(run), 0); }
      catch (error) { finish(run, { error: error.message, percent: 0 }); }
      return id;
    },
    dispose() { attempt += 1; clearTimer(); current?.xhr?.abort?.(); current = null; },
  };
}
