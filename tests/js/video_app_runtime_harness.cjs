const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const scenario = process.argv[2];
const frontendPath = process.argv[3];
const TASK_ID = "123e4567e89b42d3a456426614174000";

class FakeClassList {
  constructor() {
    this.values = new Set();
  }

  add(...names) {
    names.forEach((name) => this.values.add(name));
  }

  remove(...names) {
    names.forEach((name) => this.values.delete(name));
  }

  toggle(name, force) {
    if (force === undefined) force = !this.values.has(name);
    if (force) this.values.add(name);
    else this.values.delete(name);
    return force;
  }

  contains(name) {
    return this.values.has(name);
  }
}

class FakeElement {
  constructor(id = "") {
    this.id = id;
    this.value = "";
    this.textContent = "";
    this.innerHTML = "";
    this.dataset = {};
    this.style = {};
    this.classList = new FakeClassList();
    this.listeners = {};
  }

  addEventListener(name, listener) {
    this.listeners[name] = listener;
  }

  click() {}
  querySelectorAll() { return []; }
  appendChild() {}
}

class FakeFormData {
  constructor() {
    this.values = new Map();
  }

  append(name, value) {
    this.values.set(name, value);
  }
}

class FakeXMLHttpRequest {
  static instances = [];

  constructor() {
    this.upload = {};
    this.status = 0;
    this.responseText = "";
    FakeXMLHttpRequest.instances.push(this);
  }

  open(method, url) {
    this.method = method;
    this.url = url;
  }

  send(body) {
    this.body = body;
  }

  async respond(status, payload = {}) {
    this.status = status;
    this.responseText = typeof payload === "string" ? payload : JSON.stringify(payload);
    return this.onload?.();
  }
}

class FakeResponse {
  constructor(status, payload) {
    this.status = status;
    this.ok = status >= 200 && status < 300;
    this.payload = payload;
  }

  async json() { return this.payload; }
  async text() {
    return typeof this.payload === "string" ? this.payload : JSON.stringify(this.payload ?? {});
  }
}

const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, new FakeElement(id));
  return elements.get(id);
}

let nextTimerId = 1;
const timers = new Map();
function scheduleTimer(callback, milliseconds) {
  const id = nextTimerId++;
  timers.set(id, { callback, milliseconds });
  return id;
}
function clearTimer(id) { timers.delete(id); }
async function runTimer(milliseconds) {
  const entry = [...timers.entries()].find(([, timer]) => timer.milliseconds === milliseconds);
  assert.ok(entry, `expected a ${milliseconds}ms timer`);
  const [id, timer] = entry;
  timers.delete(id);
  await timer.callback();
  await Promise.resolve();
}

let fetchHandler = async () => new FakeResponse(500, { detail: "unexpected fetch" });
const quietConsole = { log() {}, warn() {}, error() {} };
const context = {
  console: quietConsole,
  document: {
    body: new FakeElement("body"),
    getElementById: element,
    querySelectorAll: () => [],
    querySelector: () => null,
    createElement: (tag) => new FakeElement(tag),
  },
  FormData: FakeFormData,
  XMLHttpRequest: FakeXMLHttpRequest,
  fetch: (...args) => fetchHandler(...args),
  setTimeout: scheduleTimer,
  clearTimeout: clearTimer,
  setInterval: scheduleTimer,
  clearInterval: clearTimer,
  crypto: { randomUUID: () => TASK_ID },
  navigator: {},
};
context.window = context;
context.globalThis = context;
vm.createContext(context);

let source = fs.readFileSync(frontendPath, "utf8").replace(/^\uFEFF/, "");
const bootstrapIndex = source.lastIndexOf("\nbootstrapVideoApp().catch");
assert.notEqual(bootstrapIndex, -1, "frontend bootstrap marker changed");
source = source.slice(0, bootstrapIndex);
source += `\n;globalThis.__testHooks = {
  state,
  uploadFile,
  updateVideoInState,
  shouldIgnoreVideoUpdate,
};`;
vm.runInContext(source, context, { filename: frontendPath });

const hooks = context.__testHooks;

function video(status, updatedAt, overrides = {}) {
  return {
    id: TASK_ID,
    filename: "demo.mp4",
    title: "demo",
    status,
    current_stage: status,
    updated_at: updatedAt,
    ...overrides,
  };
}

function configureUploadFetch(task) {
  fetchHandler = async (url) => {
    if (url === `/api/videos/${TASK_ID}`) return new FakeResponse(200, task);
    if (url === `/api/videos/${TASK_ID}/transcript`) return new FakeResponse(200, { transcript: null });
    if (url === "/api/videos") return new FakeResponse(200, [task]);
    return new FakeResponse(404, { detail: "not found" });
  };
}

async function run() {
  if (scenario === "stale-update") {
    const latest = video("completed", "2026-08-27T10:00:02.000000");
    const stale = video("processing", "2026-08-27T10:00:01.000000");
    hooks.state.videos = [latest];
    assert.equal(hooks.updateVideoInState(stale), false);
    assert.equal(hooks.state.videos[0].status, "completed");
    assert.equal(hooks.updateVideoInState({ filename: "missing-id.mp4" }), false);
    return;
  }

  if (scenario === "reparse-update") {
    const oldTerminal = video("completed", "2026-08-27T10:00:01.000000");
    const newProcessing = video("processing", "2026-08-27T10:00:02.000000");
    hooks.state.videos = [oldTerminal];
    assert.equal(hooks.updateVideoInState(newProcessing), true);
    assert.equal(hooks.state.videos[0].status, "processing");
    assert.equal(hooks.updateVideoInState(oldTerminal), false);
    assert.equal(hooks.state.videos[0].status, "processing");
    return;
  }

  if (scenario === "late-504" || scenario === "collision-409") {
    const completed = video("completed", "2026-08-27T10:00:02.000000");
    configureUploadFetch(completed);
    hooks.uploadFile({ name: "demo.mp4", size: 1024 });
    const xhr = FakeXMLHttpRequest.instances.at(-1);
    assert.equal(xhr.method, "POST");
    assert.equal(xhr.url, "/api/videos/upload-and-parse");
    assert.equal(xhr.body.values.get("task_id"), TASK_ID);

    await runTimer(0);
    assert.match(element("uploadInfo").textContent, /上传和解析已全部完成/);
    assert.equal(hooks.state.activeUploadTaskId, null);

    if (scenario === "late-504") {
      await xhr.respond(504, "gateway timeout");
      assert.match(element("uploadInfo").textContent, /上传和解析已全部完成/);
      assert.equal(hooks.state.videos[0].status, "completed");
      assert.equal(element("uploadProgressBar").style.width, "100%");
      return;
    }

    await xhr.respond(409, { detail: "task_id 已存在" });
    assert.match(element("uploadInfo").textContent, /上传失败：任务 ID 冲突，请重新上传/);
    assert.doesNotMatch(element("uploadInfo").textContent, /全部完成/);
    assert.equal(element("uploadProgressBar").style.width, "0%");
    assert.equal(hooks.state.activeUploadTaskId, null);
    assert.equal(hooks.state.activeUploadTaskRevealed, false);
    assert.equal(hooks.state.activeUploadAttempt, 0);
    assert.equal(hooks.state.videos[0].status, "completed");
    return;
  }

  throw new Error(`unknown scenario: ${scenario}`);
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
