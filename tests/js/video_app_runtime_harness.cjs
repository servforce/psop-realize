const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

(async () => {
  const { createUploadController } = await import(pathToFileURL(path.resolve(process.argv[3])));
  const { mergeVideo, shouldIgnoreVideoUpdate } = await import(pathToFileURL(path.resolve('static/assets/js/utils/video.js')));
  const scenario = process.argv[2];
  const id = '123e4567e89b42d3a456426614174000';
  const job = (status, at = '2026-09-10T01:00:00') => ({ id, status, current_stage: status, updated_at: at });
  const state = {}, videos = [], timers = new Map(), responses = []; let timerId = 0, xhr;
  class Form { constructor() { this.values = new Map(); } append(k,v) { this.values.set(k,v); } }
  const controller = createUploadController({
    onChange: patch => Object.assign(state, patch), onVideo: video => mergeVideo(videos, video),
    crypto: { randomUUID: () => '123e4567-e89b-42d3-a456-426614174000' }, Form,
    schedule: (fn, delay) => { timers.set(++timerId, {fn,delay}); return timerId; }, cancel: id => timers.delete(id),
    fetchImpl: async () => responses.shift() || { status:404, ok:false },
    xhrFactory: () => (xhr = { upload: {}, open(method,url) { this.method=method; this.url=url; }, send(body) {this.body=body;}, abort() {} }),
  });
  const tick = async response => {
    if (response) responses.push({ status: 200, ok: true, json: async () => response });
    const [key, {fn}] = timers.entries().next().value; timers.delete(key); await fn();
  };
  const post = (status, payload) => { xhr.status = status; xhr.responseText = JSON.stringify(payload); xhr.onload(); };
  if (scenario === 'stale-update') {
    assert.equal(mergeVideo(videos, job('completed', '2026-09-10T01:00:02')), true);
    assert.equal(mergeVideo(videos, job('processing', '2026-09-10T01:00:01')), false);
    assert.equal(videos[0].status, 'completed');
    assert.equal(shouldIgnoreVideoUpdate(job('completed'), job('processing')), true);
  } else if (scenario === 'reparse-update') {
    mergeVideo(videos, job('completed'));
    assert.equal(mergeVideo(videos, job('processing', '2026-09-10T01:01:00')), true);
    assert.equal(videos[0].status, 'processing');
  } else {
    controller.start({ name: 'operation.mp4', size: 1200, type: 'video/mp4' });
    assert.equal(xhr.method, 'POST'); assert.equal(xhr.url, '/api/videos/upload-and-parse');
    assert.equal(xhr.body.values.get('task_id'), id);
    assert.throws(() => controller.start({ name: 'second.mp4', size: 5, type: 'video/mp4' }));
    if (scenario === 'late-504') {
      await tick(job('completed')); assert.equal(state.busy, false);
      post(504, {}); assert.equal(state.error, ''); assert.equal(state.percent, 100); assert.equal(timers.size, 0);
    } else if (scenario === 'collision-409') {
      post(409, {detail:'task conflict'}); assert.equal(state.busy, false); assert.match(state.error, /冲突/); assert.equal(timers.size, 0);
    } else if (scenario === 'collision-inflight') {
      let resolveJob;
      responses.push({status:200,ok:true,json:()=>new Promise(resolve => {resolveJob=resolve;})});
      const pending=tick(); await Promise.resolve();
      post(409, {detail:'collision'}); resolveJob(job('completed')); await pending;
      assert.equal(state.registered,false); assert.equal(videos.length,0); assert.match(state.error,/冲突/);
    } else if (scenario === 'post-failed-job') {
      post(500, {job:job('processing'),error:{message:'模型处理失败'}});
      assert.equal(state.busy,false); assert.equal(videos[0].status,'failed'); assert.match(state.error,/模型处理失败/);
    } else if (scenario === 'temporary-404') {
      await tick(); assert.equal(state.busy, true); assert.equal(state.registered, false);
      assert.equal([...timers.values()][0].delay, 2000);
      await tick(job('processing')); assert.equal(state.registered, true);
      await tick(job('completed','2026-09-10T01:02:00')); assert.equal(state.busy,false);
    } else if (scenario === 'network-timeout') {
      xhr.onerror(); for (let i=0;i<150;i++) await tick();
      assert.equal(state.busy,false); assert.match(state.error,/未创建任务/); assert.equal(timers.size,0);
    } else if (scenario === 'disposed-response') {
      controller.dispose(); post(200,{job:job('completed')}); assert.equal(videos.length,0); assert.equal(timers.size,0);
    } else throw new Error('Unknown scenario');
  }
  console.log(`PASS ${scenario}`);
})().catch(error => { console.error(error); process.exitCode=1; });
