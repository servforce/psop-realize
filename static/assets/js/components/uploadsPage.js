export default function uploadsPage() {
  return {
    title: '', dragging: false, error: '',
    get upload() { return this.$store.app.upload; },
    select(file) { this.error = ''; if (!file) return; try { this.$store.app.startUpload(file, this.title); } catch (error) { this.error = error.message; } },
    drop(event) { this.dragging = false; this.select(event.dataTransfer.files?.[0]); },
    openResult() { if (this.upload.registered) window.dispatchEvent(new CustomEvent('psop:navigate', { detail: `/videos/${encodeURIComponent(this.upload.id)}` })); },
  };
}
