export async function request(url, options = {}) {
  const { text = false, ...init } = options;
  const response = await fetch(url, { cache: 'no-store', ...init });
  if (!response.ok) {
    let detail = '';
    try { const body = await response.json(); detail = typeof body.detail === 'string' ? body.detail : body.error?.message || ''; } catch { /* Do not expose proxy HTML. */ }
    const error = new Error(detail || `请求失败（HTTP ${response.status}）`);
    error.status = response.status;
    throw error;
  }
  return text ? response.text() : response.json();
}
export const videoUrl = id => `/api/videos/${encodeURIComponent(id)}`;
export async function copyText(text) {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text);
  const field = document.createElement('textarea');
  field.value = text; field.style.position = 'fixed'; field.style.opacity = '0';
  document.body.append(field); field.select();
  try { if (!document.execCommand('copy')) throw new Error('复制失败，请手动选择文本复制。'); } finally { field.remove(); }
}
