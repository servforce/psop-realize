import { request } from './api.js';
import { mergeVideo } from '../utils/video.js';

export function createVideoCatalog() {
  // Prevent a GET or upload response started before DELETE from resurrecting the row.
  const deleted = new Set();
  return {
    videos: [], listState: null,
    accept(job) { return deleted.has(job?.id) ? false : mergeVideo(this.videos, job); },
    forget(id) { deleted.add(id); this.videos = this.videos.filter(video => video.id !== id); },
    async loadVideos(signal) {
      const items = await request('/api/videos', { signal });
      if (!Array.isArray(items)) throw new Error('视频列表响应格式不正确');
      for (const item of items) this.accept(item);
    },
  };
}
