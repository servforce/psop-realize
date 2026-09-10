import { request } from '../services/api.js';
import { tone, number, formatDate } from '../utils/video.js';
export default function usagePage() {
  const controller = new AbortController(); let sequence = 0;
  return {
    items: [], loading: true, error: '', query: '', feature: '', status: '', updated: '', tone, number, formatDate,
    init() { this.refresh(); }, destroy() { controller.abort(); sequence += 1; },
    get visible() { return this.items.filter(item => (!this.feature || item.feature_name === this.feature) && (!this.status || item.status === this.status) && String(item.model || item.models || '').toLowerCase().includes(this.query.trim().toLowerCase())); },
    get totalTokens() { return this.visible.reduce((sum, item) => sum + (Number(item.total_tokens) || 0), 0); },
    async refresh() {
      const token = ++sequence; this.loading = true; this.error = '';
      try { const data = await request('/api/usage/summary', { signal: controller.signal }); if (token === sequence) { this.items = data.items || []; this.updated = new Date().toISOString(); } }
      catch (error) { if (token === sequence && error.name !== 'AbortError') this.error = error.message; }
      finally { if (token === sequence) this.loading = false; }
    },
  };
}
