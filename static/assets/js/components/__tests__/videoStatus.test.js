import videosPage from '../videosPage.js';
import { listStatusLabel, videoStatusOptions, progressLabel, statusLabel } from '../../utils/video.js';

const states = [
  ['uploaded', '待解析'],
  ['queued', '排队中'],
  ['processing', '处理中'],
  ['completed', '已完成'],
  ['completed_with_warnings', '完成（有警告）'],
  ['failed', '失败'],
  ['deleting', '删除未完成'],
];

test.each(states)('%s rows use the exact filter label rather than the current stage', (status, label) => {
  const page = videosPage();
  expect(page.videoStatusOptions.find(option => option.value === status)?.label).toBe(label);
  expect(page.listStatusLabel({ status, current_stage: 'structuring_transcript' })).toBe(label);
  page.destroy();
});

test('processing stages remain available in detail progress without replacing the list status', () => {
  const job = { status: 'processing', current_stage: 'structuring_transcript' };
  expect(listStatusLabel(job)).toBe('处理中');
  expect(statusLabel(job)).toBe('生成结构化转写');
  expect(progressLabel(job)).toBe('生成结构化转写');
  expect(progressLabel({ ...job, stage_total: 10, stage_processed: 3, stage_message: '整理段落' })).toBe('整理段落 3/10');
});

test('missing and unknown task states cannot be mistaken for a parsing stage', () => {
  for (const job of [null, {}, { status: 'unknown', current_stage: 'completed' }]) {
    expect(listStatusLabel(job)).toBe('未知状态');
  }
});

test('server-filtered page rows use the same labels as the status options', () => {
  const page = videosPage();
  const videos = states.map(([status], index) => ({
    id: String(index), title: `视频 ${index}`, status, current_stage: 'structuring_transcript',
  }));
  videos.push({ id: 'another-stage', status: 'processing', current_stage: 'transcribing_asr' });
  page.$store = { app: { videos } };
  expect(page.videoStatusOptions).toBe(videoStatusOptions);
  expect(videoStatusOptions.map(option => [option.value, option.label])).toEqual(states);
  for (const { value, label } of videoStatusOptions) {
    page.status = value;
    page.listIds = videos.filter(video => video.status === value).map(video => video.id);
    expect(page.items.length).toBe(value === 'processing' ? 2 : 1);
    expect(page.items.every(video => listStatusLabel(video) === label)).toBe(true);
  }
  page.status = '';
  page.listIds = videos.map(video => video.id);
  expect(page.items).toHaveLength(videos.length);
  page.destroy();
});
