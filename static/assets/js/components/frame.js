export const menuItems = [
  { key: 'videos', path: '/videos', label: '文件解析', icon: 'video_library', description: '管理视频与解析产物' },
  { key: 'usage', path: '/usage', label: '用量统计', icon: 'monitoring', description: '查看模型调用与消耗' },
];
export function resolveRoute(path) {
  if (path === '/' || path === '/videos' || path === '/videos/') return { key: 'videos', id: '', title: '文件解析' };
  // Keep existing bookmarks, without retaining a separate upload page.
  if (path === '/uploads' || path === '/uploads/') return { key: 'videos', id: '', title: '文件解析', upload: true };
  const match = path.match(/^\/videos\/([^/]+)\/?$/);
  if (match) { try { return { key: 'videos', id: decodeURIComponent(match[1]), title: '视频分析' }; } catch { return null; } }
  const item = menuItems.find(item => item.path === path.replace(/\/$/, ''));
  return item ? { key: item.key, id: '', title: item.label } : null;
}
export default function frame() {
  let controller, sequence = 0, popstate, navigate;
  return {
    menuItems, active: 'videos', title: '文件解析', loading: true, error: '', mobileOpen: false, collapsed: false,
    init() {
      try { this.collapsed = localStorage.getItem('psop-sidebar') === 'collapsed'; } catch { /* Storage can be disabled. */ }
      popstate = () => this.load(location.pathname);
      navigate = event => this.go(event.detail);
      window.addEventListener('popstate', popstate);
      window.addEventListener('psop:navigate', navigate);
      this.load(location.pathname);
      this.$store.app.checkHealth();
    },
    destroy() { sequence += 1; controller?.abort(); window.removeEventListener('popstate', popstate); window.removeEventListener('psop:navigate', navigate); },
    toggleSidebar() { this.collapsed = !this.collapsed; try { localStorage.setItem('psop-sidebar', this.collapsed ? 'collapsed' : 'expanded'); } catch {} },
    go(path) { if (path !== location.pathname) history.pushState({}, '', path); this.mobileOpen = false; this.load(path); },
    async load(path) {
      window.dispatchEvent(new CustomEvent('psop:upload-close'));
      const token = ++sequence;
      controller?.abort(); controller = new AbortController();
      const container = this.$refs.page;
      const route = resolveRoute(path);
      this.loading = true; this.error = '';
      // Alpine manages the old tree's cleanup once, before replacing the fragment.
      window.Alpine.mutateDom(() => { Array.from(container.children).forEach(child => window.Alpine.destroyTree(child)); container.replaceChildren(); });
      if (!route) { this.loading = false; this.error = '找不到此页面，请从左侧菜单选择功能。'; return; }
      if (route.upload) history.replaceState({}, '', '/videos');
      this.active = route.key; this.title = route.title;
      this.$store.app.route = route;
      document.title = `${route.title} · PSOP Realize`;
      try {
        const response = await fetch(`pages/${route.key}.html`, { signal: controller.signal, cache: 'no-store' });
        if (!response.ok) throw new Error(`页面加载失败（HTTP ${response.status}）`);
        const html = await response.text();
        if (token !== sequence) return;
        window.Alpine.mutateDom(() => {
          container.innerHTML = html;
          window.Alpine.initTree(container);
        });
        if (route.upload) window.dispatchEvent(new CustomEvent('psop:upload-open'));
      } catch (error) { if (token === sequence && error.name !== 'AbortError') this.error = error.message; }
      finally { if (token === sequence) this.loading = false; }
    },
  };
}
