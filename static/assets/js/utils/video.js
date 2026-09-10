export const TERMINAL = ['completed', 'completed_with_warnings', 'failed'];
export const isTerminal = job => TERMINAL.includes(job?.status);
export const isRunning = job => ['queued', 'processing'].includes(job?.status);
// The catalog filter and row badges share labels for task states, not parsing stages.
export const videoStatusOptions = [
  { value: 'uploaded', label: '待解析' },
  { value: 'queued', label: '排队中' },
  { value: 'processing', label: '处理中' },
  { value: 'completed', label: '已完成' },
  { value: 'completed_with_warnings', label: '完成（有警告）' },
  { value: 'failed', label: '失败' },
  { value: 'deleting', label: '删除未完成' },
];
export const listStatusLabel = job => videoStatusOptions.find(option => option.value === job?.status)?.label || '未知状态';
const stages = {
  uploaded: '已上传，等待解析', queued: '等待处理', processing: '处理中', deleting: '删除未完成',
  downloading_source: '读取源视频', probing_video: '分析视频信息', preparing_analysis_proxy: '生成解析代理视频',
  extracting_keyframes: '密集抽取候选帧', filtering_frames: '图像质量过滤', deduplicating_frames: '业务帧去重',
  semantic_matching: '图索引评分与匹配', transcribing: '语音转写', transcribing_asr: '语音转写',
  structuring_transcript: '生成结构化转写', generating_markdown: '生成 Markdown',
  uploading_artifacts: '保存解析产物', completed: '已完成', completed_with_warnings: '完成 · 有警告', failed: '处理失败',
};
export const statusLabel = job => stages[isTerminal(job) ? job.status : job?.current_stage] || stages[job?.status] || '未知状态';
export function tone(status) {
  if (['completed', 'published', 'enabled'].includes(status)) return 'badge-success';
  if (['failed', 'cancelled', 'rejected', 'timeout'].includes(status)) return 'badge-danger';
  if (['processing', 'running'].includes(status)) return 'badge-info';
  if (['queued', 'uploaded', 'completed_with_warnings', 'pending', 'deleting'].includes(status)) return 'badge-warning';
  return 'badge-neutral';
}
export function parseDate(value) {
  if (!value) return new Date(NaN);
  const text = String(value).trim();
  return new Date(/[zZ]$|[+-]\d{2}:?\d{2}$/.test(text) ? text : text.replace(' ', 'T') + 'Z');
}
export function formatDate(value) {
  const date = parseDate(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}
export function shouldIgnoreVideoUpdate(existing, incoming) {
  const previous = parseDate(existing?.updated_at).getTime();
  const next = parseDate(incoming?.updated_at).getTime();
  if (Number.isFinite(previous) && Number.isFinite(next) && previous !== next) return next < previous;
  return isTerminal(existing) && !isTerminal(incoming);
}
export function mergeVideo(items, video) {
  if (!video?.id) return false;
  const index = items.findIndex(item => item.id === video.id);
  if (index !== -1 && shouldIgnoreVideoUpdate(items[index], video)) return false;
  if (index === -1) items.unshift(video); else items.splice(index, 1, video);
  return true;
}
export function formatBytes(bytes) {
  const n = Math.max(0, Number(bytes) || 0);
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1073741824) return `${(n / 1048576).toFixed(1)} MB`;
  return `${(n / 1073741824).toFixed(2)} GB`;
}
export const number = value => Math.max(0, Number(value) || 0).toLocaleString('zh-CN');
export function timestamp(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  return (s >= 3600 ? `${String(Math.floor(s / 3600)).padStart(2, '0')}:` : '') + `${String(Math.floor(s / 60) % 60).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}
export function progressLabel(job) {
  if (isTerminal(job)) return job.status === 'failed' ? '失败' : '完成';
  const total = Number(job?.stage_total) || 0;
  if (total > 0) return `${job.stage_message || '当前阶段'} ${Math.min(total, Math.max(0, Number(job.stage_processed) || 0))}/${total}`;
  return statusLabel(job);
}
// Counts describe the active stage, not an overall completion percentage.
export function stageCount(job) {
  if (job?.status !== 'processing') return '';
  const total = Math.floor(Number(job.stage_total));
  if (!Number.isFinite(total) || total <= 0) return '';
  const processed = Number(job.stage_processed);
  return `${Number.isFinite(processed) ? Math.min(total, Math.max(0, Math.floor(processed))) : 0}/${total}`;
}
export function rankedFrames(section) {
  let candidates = (section.query_graph_matches || []).flatMap(match => match.frames || []);
  if (!candidates.length) candidates = section.semantic_frames || [];
  const frames = new Map();
  for (const frame of candidates) {
    const key = frame?.id || frame?.object_key || frame?.url;
    if (!key) continue;
    if (!frames.has(key) || Number(frame.score || 0) > Number(frames.get(key).score || 0)) frames.set(key, frame);
  }
  return [...frames.values()].sort((a, b) => Number(b.score || 0) - Number(a.score || 0)).slice(0, 5).map((frame, i) => ({ ...frame, rank: i + 1 }));
}
export function safeImageUrl(value) {
  const text = String(value || '').trim();
  if (/^data:image\/(png|jpeg|webp);base64,[a-z\d+/=\s]+$/i.test(text)) return text;
  if (/^\/(?!\/)/.test(text) || /^https?:\/\//i.test(text)) return text;
  return '';
}
