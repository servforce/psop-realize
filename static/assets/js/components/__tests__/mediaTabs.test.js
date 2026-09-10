import { jest } from '@jest/globals';
import videosPage, { artifactTabs } from '../videosPage.js';

test('artifact tabs expose only transcript, business frames and Markdown', () => {
  expect(artifactTabs.map(item => item.key)).toEqual(['transcript', 'frames', 'markdown']);
  expect(artifactTabs.map(item => item.label)).toEqual(['转写文本', '业务帧', 'Markdown']);
  expect(artifactTabs.map(item => item.endpoint)).toEqual(['transcript', 'semantic-frames', 'markdown']);
});

test('unsupported tabs do not change the selection or trigger a request', async () => {
  const page = videosPage();
  page.loadArtifact = jest.fn();
  await page.changeTab('unsupported');
  expect(page.tab).toBe('transcript');
  expect(page.loadArtifact).not.toHaveBeenCalled();
  await page.changeTab('frames');
  expect(page.tab).toBe('frames');
  expect(page.loadArtifact).toHaveBeenCalledTimes(1);
});
