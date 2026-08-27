// Video service frontend. Generated from app.js split points; keep service-specific API calls here.

const state = {
  videos: [],
  usageItems: [],
  selectedVideoId: null,
  activeVideoView: "directory",
  activeVideoTab: "transcript",
  videoSearchText: "",
  videoSortOrder: "desc",
  videoLatestUploadActive: false,
  videoMarkdownView: "rendered",
  activeParseMode: null,
  activeUploadTaskId: null,
  activeUploadTaskRevealed: false,
  activeUploadRequestFailed: false,
  activeUploadTaskSeen: false,
  uploadAttemptSequence: 0,
  activeUploadAttempt: 0,
  uploadStatusPoll: null,
  videoStatusPoll: null,
  wireframeJobPoll: null,
};


function bindElement(id, eventName, handler) {
  document.getElementById(id)?.addEventListener(eventName, handler);
}

function setActiveView(viewId) {
  if (!viewId || !document.getElementById(viewId)) return;
  document.querySelectorAll(".sidebar button").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === viewId);
  });
  document.querySelectorAll(".view").forEach((view) => {
    view.classList.toggle("active", view.id === viewId);
  });
  if (viewId === "usage") {
    loadUsageSummary();
  }
}
window.setActiveView = setActiveView;

function bindNavigation() {
  document.querySelectorAll(".sidebar button[data-view]").forEach((button) => {
    button.addEventListener("click", () => setActiveView(button.dataset.view));
  });
}

function bindVideoControls() {
  bindElement("uploadVideo", "click", uploadVideo);
  bindElement("refreshUsage", "click", loadUsageSummary);
  bindElement("videoFile", "change", (event) => {
    const file = event.target.files?.[0];
    if (file) uploadFile(file);
  });
  bindElement("reloadVideos", "click", loadVideos);
  bindElement("videoSearch", "input", updateVideoSearchChrome);
  bindElement("videoSearch", "keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      applyVideoSearch();
    }
  });
  bindElement("videoSearchButton", "click", applyVideoSearch);
  bindElement("videoSearchClear", "click", clearVideoSearch);
  bindElement("backToVideoDirectory", "click", () => setVideoWorkspaceView("directory"));
  bindElement("videoLatestUpload", "click", () => {
    state.videoSortOrder = "desc";
    state.videoLatestUploadActive = true;
    updateVideoSortButton();
    renderVideoList();
  });

  const dropZone = document.getElementById("dropZone");
  if (!dropZone) return;
  dropZone.addEventListener("dragover", (event) => {
    event.preventDefault();
    dropZone.classList.add("drag-over");
  });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
  dropZone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropZone.classList.remove("drag-over");
    const file = event.dataTransfer.files?.[0];
    if (file) {
      document.getElementById("videoFile").files = event.dataTransfer.files;
      uploadFile(file);
    }
  });
}

const SEMANTIC_SECTION_FRAME_DISPLAY_LIMIT = 5;
const UPLOAD_POLL_MAX_MISSING_AFTER_NETWORK_ERROR = 150;

const stageText = {
  uploaded: "已上传到 MinIO，等待解析",
  downloading_source: "正在从 MinIO 读取源视频",
  probing_video: "正在分析视频信息",
  preparing_analysis_proxy: "正在生成 720P H.265 解析代理视频",
  extracting_keyframes: "正在使用本地 FFmpeg 抽取候选业务帧",
  filtering_frames: "正在进行段落时间窗内图像质量过滤",
  deduplicating_frames: "正在进行 HSV+pHash 去重",
  semantic_matching: "正在进行图索引评分",
  generating_wireframes: "正在筛选业务帧并生成线框图",
  transcribing: "正在调用本地 ASR 模型进行原始转写",
  transcribing_asr: "正在调用本地 ASR 模型进行原始转写",
  structuring_transcript: "正在调用 qwen3.7-plus 生成语义结构化转写",
  generating_markdown: "正在根据结构化转写生成视频分析 Markdown",
  uploading_artifacts: "正在上传产物到 MinIO",
  completed: "已完成",
  failed: "处理失败",
};


async function uploadVideo() {
  const input = document.getElementById("videoFile");
  input.value = "";
  input.click();
}

function uploadFile(file) {
  const uploadInfo = document.getElementById("uploadInfo");
  const uploadProgressBar = document.getElementById("uploadProgressBar");
  let taskId = "";
  try {
    taskId = createClientTaskId();
  } catch (error) {
    uploadInfo.textContent = `无法创建安全任务 ID：${error.message || error}`;
    uploadProgressBar.classList.remove("indeterminate");
    uploadProgressBar.style.width = "0%";
    return;
  }
  const form = new FormData();
  form.append("file", file);
  form.append("title", document.getElementById("videoTitle")?.value || file.name);
  form.append("task_id", taskId);
  const xhr = new XMLHttpRequest();
  const uploadAttempt = state.uploadAttemptSequence + 1;
  state.uploadAttemptSequence = uploadAttempt;
  state.activeUploadAttempt = uploadAttempt;
  state.activeParseMode = "full";
  state.activeUploadTaskId = taskId;
  state.activeUploadTaskRevealed = false;
  state.activeUploadRequestFailed = false;
  state.activeUploadTaskSeen = false;
  stopUploadStatusPolling();
  uploadProgressBar.classList.remove("indeterminate");
  uploadInfo.textContent = `${file.name} · ${formatBytes(file.size)} · 正在上传到服务器`;
  uploadProgressBar.style.width = "2%";
  xhr.upload.onprogress = (event) => {
    if (state.activeUploadAttempt !== uploadAttempt) return;
    if (!event.lengthComputable) return;
    const percent = Math.max(2, Math.round((event.loaded / event.total) * 100));
    uploadProgressBar.style.width = `${percent}%`;
    if (percent >= 100) {
      uploadProgressBar.classList.add("indeterminate");
      uploadInfo.textContent = `${file.name} · 服务器已接收，正在保存并准备解析`;
    } else {
      uploadInfo.textContent = `${file.name} · ${formatBytes(file.size)} · 上传到服务器 ${percent}%`;
    }
  };
  xhr.onload = async () => {
    if (state.activeUploadAttempt !== uploadAttempt) return;
    const payload = parseXhrJson(xhr.responseText);
    if (xhr.status >= 200 && xhr.status < 300) {
      if (payload?.job) await applyUploadAndParseStatus(payload.job, file.name);
      if (state.activeUploadAttempt === uploadAttempt) state.activeUploadAttempt = 0;
      return;
    }
    if (payload?.job) {
      const finalJob = isVideoTerminal(payload.job)
        ? payload.job
        : failedVideoFromPostResponse(payload.job, payload);
      await applyUploadAndParseStatus(finalJob, file.name);
      if (state.activeUploadAttempt === uploadAttempt) state.activeUploadAttempt = 0;
      return;
    }
    if (!isDefinitiveUploadRequestFailure(xhr, payload)) {
      if (state.activeUploadTaskId !== taskId) {
        // GET polling already observed the terminal state.  A late proxy
        // 502/504 for the long POST must not regress the completed UI.
        state.activeUploadAttempt = 0;
        return;
      }
      state.activeUploadRequestFailed = true;
      uploadProgressBar.classList.add("indeterminate");
      uploadProgressBar.style.width = "100%";
      uploadInfo.textContent = state.activeUploadTaskSeen
        ? `${file.name} · POST 连接已中断，任务仍在运行，正在每 2 秒查询状态`
        : `${file.name} · POST 响应异常，正在每 2 秒确认服务端是否已创建任务`;
      return;
    }
    stopUploadStatusPolling();
    state.activeUploadTaskId = null;
    state.activeUploadTaskRevealed = false;
    state.activeUploadRequestFailed = false;
    state.activeUploadTaskSeen = false;
    state.activeUploadAttempt = 0;
    uploadProgressBar.classList.remove("indeterminate");
    uploadProgressBar.style.width = "0%";
    uploadInfo.textContent = `上传失败：${uploadRequestErrorMessage(payload, xhr)}`;
  };
  xhr.onerror = () => {
    if (state.activeUploadAttempt !== uploadAttempt || state.activeUploadTaskId !== taskId) return;
    state.activeUploadRequestFailed = true;
    uploadProgressBar.classList.add("indeterminate");
    uploadProgressBar.style.width = "100%";
    uploadInfo.textContent = `${file.name} · POST 连接异常，正在每 2 秒查询任务状态`;
  };
  xhr.open("POST", "/api/videos/upload-and-parse");
  xhr.send(form);
  startUploadAndParsePolling(taskId, file.name, uploadAttempt);
}

function createClientTaskId() {
  if (typeof window.crypto?.randomUUID === "function") {
    return window.crypto.randomUUID().replace(/-/g, "").toLowerCase();
  }
  const bytes = new Uint8Array(16);
  if (typeof window.crypto?.getRandomValues !== "function") {
    throw new Error("当前浏览器不支持 Web Crypto");
  }
  window.crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

function parseXhrJson(value) {
  try {
    return JSON.parse(value || "{}");
  } catch (_error) {
    return null;
  }
}

function uploadRequestErrorMessage(payload, xhr) {
  if (xhr.status === 409) return "任务 ID 冲突，请重新上传";
  if (typeof payload?.detail === "string" && payload.detail) return payload.detail;
  return payload?.error?.message || xhr.responseText || "未知错误";
}

function isDefinitiveUploadRequestFailure(xhr, payload) {
  if (xhr.status >= 400 && xhr.status < 500 && ![408, 425, 429].includes(xhr.status)) return true;
  return payload?.error?.code === "VIDEO_UPLOAD_FAILED";
}

function failedVideoFromPostResponse(job, payload) {
  return {
    ...job,
    status: "failed",
    current_stage: "failed",
    progress_percent: 100,
    error_message: payload?.error?.message || "视频解析失败",
    updated_at: new Date().toISOString(),
  };
}

function startUploadAndParsePolling(taskId, filename, uploadAttempt) {
  stopUploadStatusPolling();
  let taskSeen = false;
  let missingAfterNetworkError = 0;
  const poll = async () => {
    if (state.activeUploadAttempt !== uploadAttempt || state.activeUploadTaskId !== taskId) return;
    let continuePolling = true;
    try {
      const response = await fetch(`/api/videos/${encodeURIComponent(taskId)}`, { cache: "no-store" });
      if (response.status === 404) {
        if (state.activeUploadRequestFailed && !taskSeen) {
          missingAfterNetworkError += 1;
          if (missingAfterNetworkError >= UPLOAD_POLL_MAX_MISSING_AFTER_NETWORK_ERROR) {
            continuePolling = false;
            stopUnregisteredUploadTask(taskId, filename, uploadAttempt);
          }
        }
        return;
      }
      if (!response.ok) throw new Error(await response.text());
      const video = await response.json();
      taskSeen = true;
      state.activeUploadTaskSeen = true;
      missingAfterNetworkError = 0;
      await applyUploadAndParseStatus(video, filename);
      continuePolling = !isVideoTerminal(video);
    } catch (error) {
      console.warn("failed to poll upload-and-parse task", error);
      if (state.activeUploadRequestFailed && !taskSeen) {
        missingAfterNetworkError += 1;
        if (missingAfterNetworkError >= UPLOAD_POLL_MAX_MISSING_AFTER_NETWORK_ERROR) {
          continuePolling = false;
          stopUnregisteredUploadTask(taskId, filename, uploadAttempt);
        }
      }
    } finally {
      if (
        continuePolling
        && state.activeUploadAttempt === uploadAttempt
        && state.activeUploadTaskId === taskId
      ) {
        state.uploadStatusPoll = setTimeout(poll, 2000);
      }
    }
  };
  state.uploadStatusPoll = setTimeout(poll, 0);
}

async function applyUploadAndParseStatus(video, filename) {
  if (!video?.id) return;
  const isActiveUpload = state.activeUploadTaskId === video.id;
  const shouldReveal = isActiveUpload && !state.activeUploadTaskRevealed;
  if (!updateVideoInState(video)) return;
  renderVideoList();
  if (shouldReveal) {
    state.activeUploadTaskRevealed = true;
    state.selectedVideoId = video.id;
    setVideoWorkspaceView("analysis");
    renderVideoShell(video);
  }
  if (state.selectedVideoId === video.id) updateVideoProgress(video);

  const uploadInfo = document.getElementById("uploadInfo");
  const uploadProgressBar = document.getElementById("uploadProgressBar");
  if (!isVideoTerminal(video)) {
    if (isActiveUpload && uploadInfo) {
      uploadInfo.textContent = `${filename} · ${statusLabel(video)} · ${videoProgressLabel(video)}`;
    }
    if (isActiveUpload && uploadProgressBar) {
      uploadProgressBar.classList.add("indeterminate");
      uploadProgressBar.style.width = "100%";
    }
    return;
  }

  if (isActiveUpload) {
    stopUploadStatusPolling();
    state.activeUploadTaskId = null;
    state.activeUploadRequestFailed = false;
    state.activeUploadTaskSeen = false;
    if (uploadProgressBar) {
      uploadProgressBar.classList.remove("indeterminate");
      uploadProgressBar.style.width = video.status === "failed" ? "0%" : "100%";
    }
    if (uploadInfo) {
      uploadInfo.textContent = video.status === "failed"
        ? `${filename} · 解析失败`
        : `${filename} · 上传和解析已全部完成`;
    }
  }
  if (state.selectedVideoId === video.id) {
    renderVideoShell(video);
    updateVideoProgress(video);
    if (video.status !== "failed") applyParseCompletionMessage("full", video);
    await loadActiveVideoTab(video.id);
  }
  await loadVideos();
}

function stopUnregisteredUploadTask(taskId, filename, uploadAttempt) {
  if (state.activeUploadAttempt !== uploadAttempt || state.activeUploadTaskId !== taskId) return;
  stopUploadStatusPolling();
  state.activeUploadTaskId = null;
  state.activeUploadTaskRevealed = false;
  state.activeUploadRequestFailed = false;
  state.activeUploadTaskSeen = false;
  state.activeUploadAttempt = 0;
  const uploadInfo = document.getElementById("uploadInfo");
  const uploadProgressBar = document.getElementById("uploadProgressBar");
  if (uploadInfo) uploadInfo.textContent = `${filename} · 连接失败且服务端未创建任务，请重新上传`;
  if (uploadProgressBar) {
    uploadProgressBar.classList.remove("indeterminate");
    uploadProgressBar.style.width = "0%";
  }
}

function stopUploadStatusPolling() {
  if (state.uploadStatusPoll) clearTimeout(state.uploadStatusPoll);
  state.uploadStatusPoll = null;
}

function stopVideoStatusPolling() {
  if (state.videoStatusPoll) {
    clearTimeout(state.videoStatusPoll);
    clearInterval(state.videoStatusPoll);
  }
  state.videoStatusPoll = null;
}

async function loadVideos() {
  state.videos = await fetchJson("/api/videos");
  renderVideoList();
}

function renderVideoList() {
  const list = document.getElementById("videoList");
  updateVideoSortButton();
  const visibleVideos = getVisibleVideos();
  list.innerHTML = visibleVideos.map((v) => `
    <div class="item ${state.selectedVideoId === v.id ? "active" : ""}" onclick="showVideo('${escapeJsString(v.id)}')">
      <div class="video-list-item-head">
        <strong class="video-list-title">${escapeHtml(v.title || v.filename)}</strong>
        <div class="muted video-list-uploaded-at">${escapeHtml(formatVideoUploadedAt(v))}</div>
      </div>
      <div class="video-list-item-meta">
        <span>大小：${escapeHtml(formatBytes(v.size_bytes || 0))}</span>
        <span>状态：${escapeHtml(videoDirectoryStatusLabel(v))}</span>
        <span>帧数：${v.frame_count || 0}</span>
      </div>
    </div>`).join("") || `<div class='muted'>${state.videoSearchText.trim() ? "没有匹配的视频" : "暂无视频"}</div>`;
}

async function loadUsageSummary() {
  const body = document.getElementById("usageTableBody");
  try {
    const payload = await fetchJson("/api/usage/summary");
    state.usageItems = Array.isArray(payload.items) ? payload.items : [];
    renderUsageSummary();
  } catch (error) {
    if (body) {
      body.innerHTML = `<tr><td colspan="5" class="error-text">用量统计加载失败：${escapeHtml(error.message || error)}</td></tr>`;
    }
  }
}

function renderUsageSummary() {
  const body = document.getElementById("usageTableBody");
  if (!body) return;
  body.innerHTML = state.usageItems.map((item) => `
    <tr>
      <td>${escapeHtml(item.feature_name || "")}</td>
      <td>${escapeHtml(item.model || item.models || "本地流程")}</td>
      <td>${escapeHtml(formatTokenCount(item.total_tokens))}</td>
      <td>${escapeHtml(item.created_at ? formatBeijingDateTime(item.created_at) : "-")}</td>
      <td><span class="usage-status usage-status-${escapeHtml(item.status || "not_started")}">${escapeHtml(item.status_label || "")}</span></td>
    </tr>
  `).join("");
}

function videoDirectoryStatusLabel(video) {
  const status = statusLabel(video);
  const progress = videoProgressLabel(video);
  if (!progress || progress === status || ["完成", "等待中", "处理中"].includes(progress)) {
    return status;
  }
  return `${status} · ${progress}`;
}

function applyVideoSearch() {
  state.videoSearchText = document.getElementById("videoSearch")?.value || "";
  state.videoLatestUploadActive = false;
  updateVideoSearchChrome();
  updateVideoSortButton();
  renderVideoList();
}

function clearVideoSearch() {
  const input = document.getElementById("videoSearch");
  if (input) {
    input.value = "";
    input.focus();
  }
  state.videoSearchText = "";
  state.videoLatestUploadActive = false;
  updateVideoSearchChrome();
  updateVideoSortButton();
  renderVideoList();
}

function updateVideoSearchChrome() {
  const input = document.getElementById("videoSearch");
  const hasValue = Boolean(input?.value.trim());
  document.querySelector(".video-search-box")?.classList.toggle("has-value", hasValue);
  document.getElementById("videoSearchClear")?.classList.toggle("hidden", !hasValue);
}

function updateVideoSortButton() {
  document.getElementById("videoLatestUpload")?.classList.toggle("active", state.videoLatestUploadActive);
}

function getVisibleVideos() {
  const keyword = state.videoSearchText.trim().toLowerCase();
  return [...state.videos]
    .filter((video) => {
      if (!keyword) return true;
      return `${video.title || ""} ${video.filename || ""}`.toLowerCase().includes(keyword);
    })
    .sort((left, right) => {
      if (!state.videoLatestUploadActive) return 0;
      const leftTime = videoUploadedTime(left);
      const rightTime = videoUploadedTime(right);
      return state.videoSortOrder === "asc" ? leftTime - rightTime : rightTime - leftTime;
    });
}

function videoUploadedTime(video) {
  const date = parseBackendDateAsUtc(video.created_at || video.uploaded_at || video.updated_at);
  const time = date.getTime();
  return Number.isNaN(time) ? 0 : time;
}

function formatVideoUploadedAt(video) {
  const value = video.created_at || video.uploaded_at || video.updated_at;
  return `上传时间：${formatVideoDateTime(value)}`;
}

function formatVideoDateTime(value) {
  if (!value) return "未知时间";
  const date = parseBackendDateAsUtc(value);
  if (Number.isNaN(date.getTime())) return value;
  const beijing = new Date(date.getTime() + 8 * 60 * 60 * 1000);
  return `${beijing.getUTCFullYear()}/${beijing.getUTCMonth() + 1}/${beijing.getUTCDate()} ${String(beijing.getUTCHours()).padStart(2, "0")}:${String(beijing.getUTCMinutes()).padStart(2, "0")}:${String(beijing.getUTCSeconds()).padStart(2, "0")}`;
}

window.showVideo = async (id) => {
  const changedVideo = state.selectedVideoId !== id;
  state.selectedVideoId = id;
  setVideoWorkspaceView("analysis");
  if (changedVideo) state.activeVideoTab = "transcript";
  renderVideoList();
  const video = await fetchJson(`/api/videos/${id}`);
  updateVideoInState(video);
  renderVideoShell(video);
  updateVideoProgress(video);
  await refreshWireframeJob(id);
  if (isVideoTaskIndeterminate(video)) startVideoStatusPolling(id);
  await loadActiveVideoTab(id);
};

function setVideoWorkspaceView(view) {
  state.activeVideoView = view === "analysis" ? "analysis" : "directory";
  const workspace = document.querySelector(".video-workspace");
  const directoryPage = document.querySelector(".video-directory-page");
  const analysisPage = document.querySelector(".video-analysis-page");
  if (!workspace || !directoryPage || !analysisPage) return;
  workspace.dataset.videoView = state.activeVideoView;
  directoryPage.classList.toggle("hidden", state.activeVideoView !== "directory");
  analysisPage.classList.toggle("hidden", state.activeVideoView !== "analysis");
  document.getElementById("videos")?.scrollIntoView({ block: "start" });
}

function renderVideoShell(video) {
  document.getElementById("videoProgressPanel").innerHTML = `
    <div class="video-analysis-head">
      <div>
        <h3>${escapeHtml(video.title || video.filename)}</h3>
        <div class="video-meta-row">
          <span>${escapeHtml(formatBytes(video.size_bytes || 0))}</span>
          <span>${video.frame_count || 0} 帧</span>
          <span>${escapeHtml(statusLabel(video))}</span>
        </div>
      </div>
    </div>
    <div class="analysis-actions">
      <div class="primary-analysis-action">
        <button onclick="parseVideo('${video.id}', 'full')">一键解析视频</button>
        <span>按转写文本、抽取并匹配业务帧、Markdown 顺序完成解析。</span>
      </div>
      <div class="analysis-section-label">单步解析</div>
      <div class="step-analysis-actions" aria-label="单步解析">
        <button onclick="parseVideo('${video.id}', 'transcript')">转写文本（包括结构化文本）</button>
        <button onclick="parseVideo('${video.id}', 'keyframes')">抽取业务帧</button>
        <button id="generateVideoWireframes" disabled title="线框图功能暂不使用">转为线框图</button>
        <button onclick="parseVideo('${video.id}', 'markdown')">生成最终 Markdown</button>
      </div>
    </div>
    <div class="analysis-progress">
      <div class="progress-row">
        <span id="stageText">${escapeHtml(statusLabel(video))}</span>
        <span id="progressText">${escapeHtml(videoProgressLabel(video))}</span>
      </div>
      <div class="progress"><div id="jobProgressBar" class="${videoProgressBarClass(video)}" style="width:${videoProgressBarWidth(video)}"></div></div>
      <div id="videoError" class="analysis-hint"></div>
    </div>
    <div class="analysis-section-label">解析结果</div>
    <div class="result-tabs">
      <button class="result-tab active" data-video-tab="transcript" onclick="switchVideoTab('transcript')">转写文本</button>
      <button class="result-tab" data-video-tab="frames" onclick="switchVideoTab('frames')">业务帧</button>
      <button class="result-tab" data-video-tab="wireframes" onclick="switchVideoTab('wireframes')">线框图</button>
      <button class="result-tab" data-video-tab="markdown" onclick="switchVideoTab('markdown')">Markdown</button>
    </div>
    <div id="transcriptTab" class="video-tab hidden">
      <section class="text-result transcript-pane">
        <div class="transcript-pane-head">
          <strong>最新结构化转写</strong>
        </div>
        <div class="transcript-text-box">
          <button class="transcript-copy-button" onclick="copyActiveTranscript()" aria-label="复制最新结构化转写">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <rect x="9" y="9" width="10" height="10" rx="2" />
              <rect x="5" y="5" width="10" height="10" rx="2" />
            </svg>
            <span>复制</span>
          </button>
          <pre id="transcriptText">暂无最新结构化转写。</pre>
        </div>
      </section>
    </div>
    <div id="framesTab" class="video-tab artifact-panel hidden"></div>
    <div id="wireframesTab" class="video-tab hidden">
      <div id="wireframesGrid"></div>
    </div>
    <div id="markdownTab" class="video-tab hidden">
      <div class="markdown-result">
        <div class="video-markdown-toolbar">
          <div class="video-markdown-switch" aria-label="Markdown 显示模式">
            <button type="button" data-video-markdown-view="rendered" onclick="setVideoMarkdownView('rendered')">渲染视图</button>
            <button type="button" data-video-markdown-view="source" onclick="setVideoMarkdownView('source')">源码视图</button>
          </div>
        </div>
        <button class="markdown-copy-button video-markdown-copy-button" onclick="copyActiveMarkdown()" aria-label="复制 Markdown">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <rect x="9" y="9" width="10" height="10" rx="2" />
            <rect x="5" y="5" width="10" height="10" rx="2" />
          </svg>
          <span>复制</span>
        </button>
        <div id="videoMarkdownRendered" class="video-markdown-rendered">
          <div class="empty">暂无 Markdown。</div>
        </div>
        <pre id="markdownText" class="hidden">暂无 Markdown。</pre>
      </div>
    </div>
  `;
  switchVideoTab(state.activeVideoTab);
}

window.parseVideo = async (id, mode) => {
  state.activeParseMode = mode;
  const labels = {
    full: "完整解析",
    keyframes: "抽取业务帧",
    wireframes: "线框图",
    transcript: "转写文本",
    markdown: "Markdown 报告",
  };
  const tabByMode = {
    keyframes: "frames",
    wireframes: "wireframes",
    transcript: "transcript",
    markdown: "markdown",
  };
  const startingText = {
    full: "正在启动完整解析流程，将先生成结构化转写文本",
    keyframes: "正在抽取并匹配业务帧",
    wireframes: "正在筛选业务帧并生成线框图",
    transcript: "正在调用本地 ASR 模型进行原始转写",
    markdown: "正在根据结构化转写生成 Markdown 报告",
  };
  if (tabByMode[mode]) switchVideoTab(tabByMode[mode]);
  const stage = document.getElementById("stageText");
  const progressText = document.getElementById("progressText");
  const bar = document.getElementById("jobProgressBar");
  const hint = document.getElementById("videoError");
  if (stage) stage.textContent = startingText[mode] || `正在生成${labels[mode] || mode}`;
  if (progressText) progressText.textContent = startingVideoProgressLabel(mode);
  if (bar) {
    bar.classList.add("indeterminate");
    bar.style.width = "100%";
  }
  if (hint) hint.textContent = "";
  try {
    const payload = await fetchJson(`/api/videos/${id}/parse?mode=${encodeURIComponent(mode)}`, { method: "POST" });
    updateVideoInState(payload.job);
    renderVideoList();
    updateVideoProgress(payload.job);
    if (payload.job && isVideoTaskIndeterminate(payload.job)) startVideoStatusPolling(id);
    if (payload.wireframe_job) {
      updateWireframeJobUI(payload.wireframe_job);
      if (["queued", "processing"].includes(payload.wireframe_job.status)) {
        startWireframeJobPolling(id, payload.wireframe_job.id);
      }
    }
    await loadVideoArtifacts(id);
  } catch (error) {
    const errorEl = document.getElementById("videoError");
    if (errorEl) errorEl.textContent = `解析失败：${error.message}`;
  }
};

async function loadVideoArtifacts(id) {
  await loadActiveVideoTab(id);
}

async function loadActiveVideoTab(id) {
  if (state.activeVideoTab === "frames") {
    await loadVideoFrames(id);
  } else if (state.activeVideoTab === "transcript") {
    await loadVideoTranscript(id);
  } else if (state.activeVideoTab === "markdown") {
    await loadVideoMarkdown(id);
  } else if (state.activeVideoTab === "wireframes") {
    await loadVideoWireframes(id);
  }
}

async function loadVideoFrames(id) {
  const target = document.getElementById("framesTab");
  if (target) target.innerHTML = '<div class="empty">加载中...</div>';
  const report = await fetchJson(`/api/videos/${id}/semantic-frames`).catch(() => null);
  if (report?.sections) {
    renderSemanticFrameReport(report);
    return;
  }
  if (target) target.innerHTML = '<div class="empty">暂无业务帧结果。请先点击“转写文本（包括结构化文本）”，再点击“抽取业务帧”。</div>';
}

async function loadVideoTranscript(id) {
  const transcriptEl = document.getElementById("transcriptText");
  if (transcriptEl) transcriptEl.textContent = "正在加载最新结构化转写...";
  const transcript = await fetchJson(`/api/videos/${id}/transcript`).catch(() => ({ text: "", rendered_text: "" }));
  if (transcriptEl) transcriptEl.textContent = transcript.rendered_text || transcript.text || "暂无最新结构化转写，请点击“转写文本”重新生成。";
}

async function loadVideoMarkdown(id) {
  const markdownEl = document.getElementById("markdownText");
  const renderedEl = document.getElementById("videoMarkdownRendered");
  if (markdownEl) markdownEl.textContent = "正在加载 Markdown...";
  if (renderedEl) renderedEl.innerHTML = `<div class="empty">正在加载 Markdown...</div>`;
  const markdown = await fetch(`/api/videos/${id}/markdown`).then((r) => r.text()).catch(() => "");
  if (markdownEl) markdownEl.textContent = markdown || "暂无 Markdown。";
  if (renderedEl) {
    renderedEl.innerHTML = markdown ? renderMarkdownDocument(markdown) : `<div class="empty">暂无 Markdown。</div>`;
  }
  applyVideoMarkdownView();
}

window.setVideoMarkdownView = (view) => {
  state.videoMarkdownView = view === "source" ? "source" : "rendered";
  applyVideoMarkdownView();
};

function applyVideoMarkdownView() {
  const renderedEl = document.getElementById("videoMarkdownRendered");
  const sourceEl = document.getElementById("markdownText");
  const sourceActive = state.videoMarkdownView === "source";
  if (renderedEl) renderedEl.classList.toggle("hidden", sourceActive);
  if (sourceEl) sourceEl.classList.toggle("hidden", !sourceActive);
  document.querySelectorAll("[data-video-markdown-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.videoMarkdownView === state.videoMarkdownView);
  });
}

async function copyTextResult({ textId, buttonSelector, emptyTexts }) {
  const textEl = document.getElementById(textId);
  const button = document.querySelector(buttonSelector);
  const label = button?.querySelector("span");
  const text = textEl?.textContent || "";
  if (!text || emptyTexts.some((emptyText) => text === emptyText || text.startsWith(emptyText))) return;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.left = "-9999px";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
    }
    if (button) button.classList.add("copied");
    if (label) label.textContent = "已复制";
  } finally {
    if (button) setTimeout(() => {
      button.classList.remove("copied");
      if (label) label.textContent = "复制";
    }, 1200);
  }
}

window.copyActiveMarkdown = async () => {
  await copyTextResult({
    textId: "markdownText",
    buttonSelector: ".video-markdown-copy-button",
    emptyTexts: ["暂无 Markdown。", "正在加载 Markdown..."],
  });
};

window.copyActiveTranscript = async () => {
  await copyTextResult({
    textId: "transcriptText",
    buttonSelector: ".transcript-copy-button",
    emptyTexts: ["暂无最新结构化转写。", "暂无最新结构化转写，请点击“转写文本”重新生成。", "正在加载最新结构化转写..."],
  });
};

async function loadVideoWireframes(id) {
  renderWireframesUnavailable();
}

function renderFrames(frames) {
  const target = document.getElementById("framesTab");
  if (!target) return;
  if (!frames.length) {
    target.innerHTML = '<div class="empty">暂无候选业务帧。</div>';
    return;
  }
  target.innerHTML = `<div class="frames-grid">${frames.map((f) => `
    <figure class="frame-card">
      <img src="${f.url}" alt="${escapeHtml(f.caption || "候选业务帧")}" loading="lazy" onclick="openImagePreview('${f.url}')" />
      <figcaption>
        <strong>${formatTimestamp(f.timestamp_seconds || 0)}</strong>
        <span>${escapeHtml(f.caption || "")}</span>
      </figcaption>
    </figure>
  `).join("")}</div>`;
}

function renderSemanticFrameReport(report) {
  const target = document.getElementById("framesTab");
  if (!target) return;
  const sections = Array.isArray(report.sections) ? report.sections : [];
  if (!sections.length) {
    target.innerHTML = '<div class="empty">暂无业务帧结果。请先点击“转写文本（包括结构化文本）”，再点击“抽取业务帧”。</div>';
    return;
  }
  target.innerHTML = `
    <div class="semantic-section-list">
      ${sections.map(renderSemanticSection).join("")}
    </div>
  `;
}

function renderSemanticSection(section) {
  const businessFrames = rankedBusinessFramesForSection(section);
  const rawFrameTotal = semanticSectionRawFrameTotal(section);
  const timeRange = `${section.start_time || formatTimestamp(section.start_seconds || 0)} - ${section.end_time || formatTimestamp(section.end_seconds || 0)}`;
  return `
    <section class="semantic-section">
      <div class="semantic-section-head">
        <div>
          <h3>${escapeHtml(section.index || "")}. ${escapeHtml(section.title || "文本段落")}</h3>
          <div class="muted">${escapeHtml(timeRange)}</div>
        </div>
      </div>
      <p class="semantic-section-text"><strong>润色后正文：</strong>${escapeHtml(section.polished_text || "未识别。")}</p>
      <p class="semantic-section-text semantic-match-text"><strong>待匹配文本：</strong>${escapeHtml(section.business_frame_text || "未识别。")}</p>
      <div class="semantic-frame-block">
        <h4>按匹配分数排序的业务帧${semanticFrameLimitLabel(businessFrames.length, rawFrameTotal)}</h4>
        ${businessFrames.length ? `<div class="semantic-ranked-list">${businessFrames.map(renderSemanticMatchedFrameCard).join("")}</div>` : '<div class="empty">暂无业务帧匹配结果。</div>'}
      </div>
    </section>
  `;
}

function semanticSectionRawFrameTotal(section) {
  const rawTotal = Number(section.raw_frames_total);
  if (Number.isFinite(rawTotal) && rawTotal >= 0) return rawTotal;
  const rawFrames = Array.isArray(section.raw_frames) ? section.raw_frames : [];
  return rawFrames.length;
}

function rankedBusinessFramesForSection(section) {
  const queryGraphMatches = Array.isArray(section.query_graph_matches) ? section.query_graph_matches : [];
  const candidates = [];
  queryGraphMatches.forEach((match) => {
    if (Array.isArray(match.frames)) candidates.push(...match.frames);
  });
  if (!candidates.length && Array.isArray(section.semantic_frames)) {
    candidates.push(...section.semantic_frames);
  }
  const byKey = new Map();
  candidates.forEach((frame) => {
    if (!frame || typeof frame !== "object") return;
    const key = frame.id || frame.object_key || frame.url;
    if (!key) return;
    const existing = byKey.get(key);
    if (!existing || Number(frame.score || 0) > Number(existing.score || 0)) {
      byKey.set(key, frame);
    }
  });
  const ranked = Array.from(byKey.values())
    .sort((left, right) => Number(right.score || 0) - Number(left.score || 0));
  const visible = ranked
    .slice(0, SEMANTIC_SECTION_FRAME_DISPLAY_LIMIT)
    .map((frame, index) => ({ ...frame, rank: index + 1 }));
  visible.total = ranked.length;
  return visible;
}

function semanticFrameLimitLabel(shown, total) {
  if (!Number.isFinite(total) || total <= shown) return "";
  return `（前 ${shown} / 共 ${total}）`;
}

function renderQueryGraphMatches(matches) {
  return `
    <div class="frame-query-match-list">
      ${matches.map((match) => {
        const framesAll = Array.isArray(match.frames) ? match.frames : [];
        const frames = framesAll.slice(0, SEMANTIC_SECTION_FRAME_DISPLAY_LIMIT);
        const framesTotal = Number(match.frames_total || framesAll.length);
        const source = match.source || "";
        const sourceLabel = queryGraphSourceLabel(source);
        const sectionTime = match.section_start_time && match.section_end_time ? `${match.section_start_time} - ${match.section_end_time}` : "";
        const sectionTitle = match.section_title || `段落 ${Number(match.query_graph_index || match.query_index || 0)}`;
        const businessFrameText = match.business_frame_text || "";
        return `
          <section class="frame-query-match">
            <div class="frame-query-text">
              <strong>${Number(match.query_graph_index || match.query_index || 0)}. ${escapeHtml(sectionTitle)}</strong>
              ${sourceLabel ? `<em class="frame-query-source">${escapeHtml(sourceLabel)}</em>` : ""}
              ${sectionTime ? `<small>${escapeHtml(sectionTime)}</small>` : ""}
              ${businessFrameText ? `<span>${escapeHtml(businessFrameText)}</span>` : ""}
            </div>
            ${framesTotal > frames.length ? `<div class="muted">仅展示前 ${frames.length} 张，共 ${framesTotal} 张。</div>` : ""}
            ${frames.length ? `<div class="semantic-ranked-list">${frames.map(renderSemanticMatchedFrameCard).join("")}</div>` : '<div class="empty">该段落暂无候选帧分数。</div>'}
          </section>
        `;
      }).join("")}
    </div>
  `;
}

function queryGraphSourceLabel(source) {
  if (source === "section_query_graph") return "section.query_graph";
  return "";
}

function renderFrameCard(frame, options = {}) {
  const url = frame.url || "";
  const timestamp = frame.timestamp_time || formatTimestamp(frame.timestamp_seconds || 0);
  const scoreValue = Number(frame.score);
  const scoreText = Number.isFinite(scoreValue) ? scoreValue.toFixed(4) : "";
  const filterLabel = frame.filter_label || rawFrameFilterLabel(frame.filter_status);
  return `
    <figure class="frame-card">
      <img src="${escapeHtml(url)}" alt="候选业务帧 ${escapeHtml(timestamp)}" loading="lazy" onclick="openImagePreview('${escapeJsString(url)}')" />
      <figcaption>
        <strong>${escapeHtml(timestamp)}</strong>
        ${options.score ? `<span>分数：${escapeHtml(scoreText)}</span>` : (filterLabel ? `<span class="frame-filter-label">${escapeHtml(filterLabel)}</span>` : "")}
      </figcaption>
    </figure>
  `;
}

function renderSemanticMatchedFrameCard(frame) {
  const timestamp = frame.timestamp_time || formatTimestamp(frame.timestamp_seconds || 0);
  const score = Math.max(0, Math.min(1, Number(frame.score || 0)));
  const scoreText = Number.isFinite(Number(frame.score)) ? Number(frame.score).toFixed(4) : "";
  const maskUrl = frame.mask_image ? `data:image/jpeg;base64,${frame.mask_image}` : "";
  const bboxUrl = frame.bbox_image ? `data:image/jpeg;base64,${frame.bbox_image}` : "";
  const previewUrl = frame.url || "";
  return `
    <article class="semantic-match-card">
      <button class="semantic-match-image" type="button" onclick="openImagePreview('${escapeJsString(previewUrl)}')" aria-label="预览业务帧">
        <img src="${escapeHtml(previewUrl)}" alt="业务帧 ${escapeHtml(timestamp)}" loading="lazy" />
      </button>
      <div class="semantic-match-body">
        <div class="semantic-match-head">
          <strong>#${Number(frame.rank || 0)} ${escapeHtml(timestamp)}</strong>
          <span>${escapeHtml(scoreText)}</span>
        </div>
        <div class="business-frame-scorebar semantic-match-scorebar" aria-label="匹配分数">
          <div style="width:${score * 100}%"></div>
        </div>
        <div class="semantic-match-actions">
          <button class="secondary small" type="button" ${maskUrl ? "" : "disabled"} onclick="openImagePreview('${escapeJsString(maskUrl)}')">查看 mask 图像</button>
          ${bboxUrl ? `<button class="secondary small" type="button" onclick="openImagePreview('${escapeJsString(bboxUrl)}')">查看 bbox 图像</button>` : ""}
        </div>
      </div>
    </article>
  `;
}

function renderSemanticObjectPill(obj) {
  const area = Number(obj.mask_area_ratio ?? obj.bbox_area_ratio ?? 0);
  const confidence = Number(obj.confidence || 0);
  return `<span>${escapeHtml(obj.label || "unknown")} · ${(confidence * 100).toFixed(0)}% · ${(area * 100).toFixed(1)}%</span>`;
}

function rawFrameFilterLabel(status) {
  if (status === "quality_rejected") return "质量不通过";
  if (status === "duplicate_rejected") return "重复";
  return "";
}

function renderWireframes(wireframes) {
  renderWireframesUnavailable();
}

function renderWireframesUnavailable() {
  const target = document.getElementById("wireframesGrid");
  if (!target) return;
  target.innerHTML = '<div class="empty wireframes-empty">暂无需生成线框图。如需生成，请在代码里恢复线框图生成逻辑。</div>';
}

function renderFrameSelectionPreview(item) {
  const imageUrl = item.source_frame_url || item.url || "";
  const score = typeof item.selection_score === "number" ? item.selection_score : Number(item.selection_score || 0);
  const scoreText = Number.isFinite(score) ? score.toFixed(2) : "未知";
  const sectionTitle = item.matched_section_title || (item.matched_section_index ? `段落 ${item.matched_section_index}` : "未匹配段落");
  const sectionText = item.matched_section_text ? truncateText(item.matched_section_text, 120) : "";
  const evidence = Array.isArray(item.visual_evidence) ? item.visual_evidence.filter(Boolean) : [];
  return `
    <figure class="frame-card selection-preview-card">
      <img src="${imageUrl}" alt="${escapeHtml(sectionTitle)}" loading="lazy" onclick="openImagePreview('${imageUrl}')" />
      <figcaption>
        <div class="selection-preview-head">
          <strong>${escapeHtml(formatTimestamp(item.timestamp_seconds || 0))}</strong>
          <span class="selection-score">分数 ${escapeHtml(scoreText)}</span>
        </div>
        <span class="selection-reason">${escapeHtml(item.selection_reason || "模型选择该帧作为线框图候选。")}</span>
        <span class="selection-section">对应段落：${escapeHtml(sectionTitle)}</span>
        ${sectionText ? `<span class="selection-section-text">${escapeHtml(sectionText)}</span>` : ""}
        ${evidence.length ? `<span class="selection-evidence">视觉证据：${escapeHtml(evidence.join("、"))}</span>` : ""}
      </figcaption>
    </figure>
  `;
}

function truncateText(value, limit) {
  const text = String(value || "").trim();
  if (text.length <= limit) return text;
  return `${text.slice(0, limit)}...`;
}

window.switchVideoTab = (tab) => {
  state.activeVideoTab = tab;
  document.querySelectorAll("[data-video-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.videoTab === tab);
  });
  ["frames", "transcript", "markdown", "wireframes"].forEach((name) => {
    const panel = document.getElementById(`${name}Tab`);
    if (panel) panel.classList.toggle("hidden", name !== tab);
  });
  if (state.selectedVideoId) loadActiveVideoTab(state.selectedVideoId);
};

function updateVideoProgress(video) {
  if (state.selectedVideoId !== video.id) return;
  const stage = document.getElementById("stageText");
  const progressText = document.getElementById("progressText");
  const bar = document.getElementById("jobProgressBar");
  const error = document.getElementById("videoError");
  if (stage) stage.textContent = statusLabel(video);
  if (progressText) progressText.textContent = videoProgressLabel(video);
  if (bar) {
    bar.classList.toggle("indeterminate", isVideoTaskIndeterminate(video));
    bar.style.width = videoProgressBarWidth(video);
  }
  if (error) {
    if (video.status === "failed") {
      error.textContent = video.error_message || "解析失败";
      error.classList.add("error-text");
    } else if (!["completed", "completed_with_warnings"].includes(video.status)) {
      error.textContent = "";
      error.classList.remove("error-text");
    }
  }
}

function applyParseCompletionMessage(mode, video) {
  const messages = {
    full: ["全部工作已完成", "候选业务帧、转写文本和 Markdown 都已完成。"],
    keyframes: ["业务帧抽取已完成", "已按文本段落输出图索引分数和排序结果。"],
    wireframes: ["业务帧筛选/线框图已完成", "接下来可以生成 Markdown。"],
    transcript: ["转写文本已完成", "已生成最新结构化转写。"],
    markdown: ["Markdown 报告已完成", "可以在 Markdown 标签中查看并复制。"],
  };
  const [stageMessage, hintMessage] = messages[mode] || [statusLabel(video), ""];
  const stage = document.getElementById("stageText");
  const progressText = document.getElementById("progressText");
  const bar = document.getElementById("jobProgressBar");
  const hint = document.getElementById("videoError");
  if (stage) stage.textContent = stageMessage;
  if (progressText) progressText.textContent = "完成";
  if (bar) {
    bar.classList.remove("indeterminate");
    bar.style.width = "100%";
  }
  if (hint) {
    hint.textContent = hintMessage;
    hint.classList.remove("error-text");
  }
}

function updateVideoInState(video) {
  if (!video?.id) return false;
  const index = state.videos.findIndex((item) => item.id === video.id);
  if (index >= 0) {
    if (shouldIgnoreVideoUpdate(state.videos[index], video)) return false;
    state.videos[index] = video;
  } else {
    state.videos.unshift(video);
  }
  return true;
}

function shouldIgnoreVideoUpdate(existing, incoming) {
  const existingTerminal = isVideoTerminal(existing);
  const incomingTerminal = isVideoTerminal(incoming);
  const existingTime = parseBackendDateAsUtc(existing?.updated_at).getTime();
  const incomingTime = parseBackendDateAsUtc(incoming?.updated_at).getTime();
  if (Number.isFinite(existingTime) && Number.isFinite(incomingTime)) {
    if (incomingTime < existingTime) return true;
    if (incomingTime > existingTime) return false;
  }
  return existingTerminal && !incomingTerminal;
}

function startVideoStatusPolling(id) {
  stopVideoStatusPolling();
  let timer = null;
  const poll = async () => {
    if (state.selectedVideoId !== id) {
      if (timer) clearInterval(timer);
      state.videoStatusPoll = null;
      return;
    }
    const video = await fetchJson(`/api/videos/${id}`).catch(() => null);
    if (!video || state.selectedVideoId !== id) return;
    const accepted = updateVideoInState(video);
    const currentVideo = accepted
      ? video
      : state.videos.find((item) => item.id === video.id) || video;
    renderVideoList();
    updateVideoProgress(currentVideo);
    if (isVideoTerminal(currentVideo)) {
      if (timer) clearInterval(timer);
      state.videoStatusPoll = null;
      renderVideoShell(currentVideo);
      updateVideoProgress(currentVideo);
      if (currentVideo.status !== "failed") applyParseCompletionMessage(state.activeParseMode, currentVideo);
      await refreshWireframeJob(id);
      await loadActiveVideoTab(id);
    }
  };
  poll();
  timer = setInterval(poll, 2000);
  state.videoStatusPoll = timer;
}

window.generateWireframes = async (id) => {
  await parseVideo(id, "wireframes");
};

async function refreshWireframeJob(videoId) {
  const payload = await fetchJson(`/api/videos/${videoId}/wireframes/jobs/latest`).catch(() => ({ job: null }));
  if (!payload.job) return;
  updateWireframeJobUI(payload.job);
  if (["queued", "processing"].includes(payload.job.status)) {
    startWireframeJobPolling(videoId, payload.job.id);
  }
}

function startWireframeJobPolling(videoId, jobId) {
  if (state.wireframeJobPoll) clearInterval(state.wireframeJobPoll);
  let timer = null;
  const poll = async () => {
    if (state.selectedVideoId !== videoId) {
      if (timer) clearInterval(timer);
      state.wireframeJobPoll = null;
      return;
    }
    const payload = await fetchJson(`/api/videos/${videoId}/wireframes/jobs/${jobId}`).catch(() => null);
    if (!payload?.job) return;
    updateWireframeJobUI(payload.job);
    if (!["queued", "processing"].includes(payload.job.status)) {
      if (timer) clearInterval(timer);
      state.wireframeJobPoll = null;
      const latestVideo = await fetchJson(`/api/videos/${videoId}`).catch(() => null);
      if (latestVideo && state.selectedVideoId === videoId) {
        updateVideoInState(latestVideo);
        renderVideoShell(latestVideo);
        applyParseCompletionMessage("wireframes", latestVideo);
      }
      await loadVideoWireframes(videoId);
    }
  };
  poll();
  timer = setInterval(poll, 1200);
  state.wireframeJobPoll = timer;
}

function updateWireframeJobUI(job) {
  const stage = document.getElementById("stageText");
  const progressText = document.getElementById("progressText");
  const bar = document.getElementById("jobProgressBar");
  const error = document.getElementById("videoError");
  if (!job) return;
  if (bar) {
    bar.classList.toggle("indeterminate", ["queued", "processing"].includes(job.status));
    bar.style.width = job.status === "failed" ? "0%" : job.status === "completed" ? "100%" : "100%";
  }
  if (progressText) progressText.textContent = wireframeJobProgressLabel(job);
  if (job.status === "completed") {
    if (stage) stage.textContent = "筛选结果已完成";
    if (error) {
      error.textContent = "";
      error.classList.remove("error-text");
    }
  } else if (job.status === "failed") {
    if (stage) stage.textContent = "线框图生成失败";
    if (error) {
      error.textContent = job.error_message || "未知错误";
      error.classList.add("error-text");
    }
  } else {
    if (stage) stage.textContent = "正在筛选业务帧并准备展示筛选结果";
    if (error) {
      error.textContent = "";
      error.classList.remove("error-text");
    }
  }
}
window.openImagePreview = (url) => {
  let overlay = document.getElementById("imagePreviewOverlay");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.id = "imagePreviewOverlay";
    overlay.className = "image-preview-overlay hidden";
    overlay.innerHTML = `
      <button class="image-preview-close" onclick="closeImagePreview()">关闭</button>
      <img id="imagePreviewImg" alt="图片预览" />
    `;
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) closeImagePreview();
    });
    document.body.appendChild(overlay);
  }
  document.getElementById("imagePreviewImg").src = url;
  overlay.classList.remove("hidden");
};

window.closeImagePreview = () => {
  const overlay = document.getElementById("imagePreviewOverlay");
  if (overlay) overlay.classList.add("hidden");
};

function renderMarkdownDocument(markdown) {
  const lines = normalizeMarkdownEntities(stripMarkdownFrontmatter(String(markdown || ""))).replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  for (let index = 0; index < lines.length;) {
    const line = lines[index];
    const trimmed = trimMarkdownLine(line);
    if (isMarkdownBlankLine(line)) {
      index += 1;
      continue;
    }
    if (/^```/.test(trimmed)) {
      const codeLines = [];
      index += 1;
      while (index < lines.length && !/^```/.test(lines[index].trim())) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      blocks.push(`<pre class="markdown-code-block"><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
      continue;
    }
    const heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const level = Math.min(6, heading[1].length);
      blocks.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      index += 1;
      continue;
    }
    if (isMarkdownTableStart(lines, index)) {
      const tableLines = [];
      while (index < lines.length && isMarkdownTableLine(lines[index])) {
        tableLines.push(lines[index]);
        index += 1;
      }
      blocks.push(renderMarkdownTable(tableLines));
      continue;
    }
    if (isMarkdownOutlineStart(lines, index)) {
      const outlineLines = [];
      while (index < lines.length && !isMarkdownBlankLine(lines[index]) && parseMarkdownOutlineLine(lines[index])) {
        outlineLines.push(lines[index]);
        index += 1;
      }
      blocks.push(renderMarkdownPlainBlock(outlineLines));
      continue;
    }
    if (/^\s*[-*+]\s+/.test(line)) {
      const listLines = [];
      while (index < lines.length && /^\s*[-*+]\s+/.test(lines[index])) {
        listLines.push(lines[index]);
        index += 1;
      }
      if (hasIndentedListLines(listLines)) {
        blocks.push(renderMarkdownPlainBlock(listLines));
        continue;
      }
      const items = listLines.map((item) => item.replace(/^\s*[-*+]\s+/, ""));
      blocks.push(`<ul>${items.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ul>`);
      continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const listLines = [];
      while (index < lines.length && /^\s*\d+[.)]\s+/.test(lines[index])) {
        listLines.push(lines[index]);
        index += 1;
      }
      if (hasIndentedListLines(listLines)) {
        blocks.push(renderMarkdownPlainBlock(listLines));
        continue;
      }
      const items = listLines.map((item) => item.replace(/^\s*\d+[.)]\s+/, ""));
      blocks.push(`<ol>${items.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ol>`);
      continue;
    }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      blocks.push("<hr />");
      index += 1;
      continue;
    }
    const paragraph = [];
    while (index < lines.length && !isMarkdownBlankLine(lines[index]) && !isMarkdownBlockStart(lines, index)) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    blocks.push(`<p>${paragraph.map((item) => renderInlineMarkdown(item)).join("<br />")}</p>`);
  }
  return blocks.join("") || "<div class='empty'>暂无内容。</div>";
}

function stripMarkdownFrontmatter(markdown) {
  if (!markdown.startsWith("---")) return markdown;
  const lines = markdown.replace(/\r\n/g, "\n").split("\n");
  for (let index = 1; index < lines.length; index += 1) {
    if (lines[index].trim() === "---") return lines.slice(index + 1).join("\n").trimStart();
  }
  return markdown;
}

function trimMarkdownLine(line) {
  return normalizeMarkdownEntities(line)
    .replace(/[\u00a0\u2002\u2003\u3000]/g, " ")
    .trim();
}

function isMarkdownBlankLine(line) {
  return trimMarkdownLine(line) === "";
}

function isMarkdownBlockStart(lines, index) {
  const line = lines[index] || "";
  const trimmed = line.trim();
  return /^```/.test(trimmed)
    || /^(#{1,6})\s+/.test(trimmed)
    || isMarkdownTableStart(lines, index)
    || isMarkdownOutlineStart(lines, index)
    || /^\s*[-*+]\s+/.test(line)
    || /^\s*\d+[.)]\s+/.test(line)
    || /^(-{3,}|\*{3,}|_{3,})$/.test(trimmed);
}

function isMarkdownOutlineStart(lines, index) {
  if (!parseMarkdownOutlineLine(lines[index] || "")) return false;
  return Boolean(parseMarkdownOutlineLine(lines[index + 1] || ""));
}

function parseMarkdownOutlineLine(line) {
  const text = String(line || "");
  const numbered = text.match(/^(\s*)(\d+(?:\.\d+)*)(?:[、.．]|\s+)(.+)$/);
  if (numbered) {
    return {
      depth: Math.max(0, numbered[2].split(".").length - 1),
      marker: numbered[2],
      text: numbered[3].trim(),
    };
  }
  const cnNumbered = text.match(/^(\s*)((?:第[一二三四五六七八九十百千万\d]+[章节篇])|(?:[一二三四五六七八九十]+、)|(?:（[一二三四五六七八九十\d]+）))\s*(.+)$/);
  if (cnNumbered) {
    const leadingDepth = Math.floor(cnNumbered[1].replace(/\t/g, "  ").length / 2);
    return {
      depth: Math.max(0, leadingDepth),
      marker: cnNumbered[2],
      text: cnNumbered[3].trim(),
    };
  }
  return null;
}

function renderMarkdownOutline(outlineLines) {
  return `
    <div class="markdown-outline">
      ${outlineLines.map((line) => {
        const item = parseMarkdownOutlineLine(line);
        if (!item) return "";
        const depth = Math.max(0, Math.min(5, item.depth));
        return `
          <div class="markdown-outline-row" style="--outline-depth:${depth}">
            <span class="markdown-outline-marker">${escapeHtml(item.marker)}</span>
            <span class="markdown-outline-text">${renderInlineMarkdown(item.text)}</span>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

function hasIndentedListLines(lines) {
  return lines.some((line) => /^\s{2,}[-*+\d]/.test(line));
}

function renderMarkdownPlainBlock(lines) {
  return `
    <div class="markdown-plain-block">
      ${lines.map((line) => `<div class="markdown-plain-line">${renderInlineMarkdown(line)}</div>`).join("")}
    </div>
  `;
}

function isMarkdownTableStart(lines, index) {
  return isMarkdownTableLine(lines[index]) && isMarkdownSeparatorLine(lines[index + 1] || "");
}

function isMarkdownTableLine(line) {
  const trimmed = String(line || "").trim();
  return trimmed.startsWith("|") && trimmed.endsWith("|") && trimmed.includes("|");
}

function isMarkdownSeparatorLine(line) {
  if (!isMarkdownTableLine(line)) return false;
  return splitMarkdownTableRow(line).every((cell) => /^:?-{3,}:?$/.test(cell.trim()));
}

function splitMarkdownTableRow(line) {
  return String(line || "")
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function renderMarkdownTable(tableLines) {
  const headers = splitMarkdownTableRow(tableLines[0]);
  const rows = tableLines.slice(2).map(splitMarkdownTableRow).filter((row) => row.some(Boolean));
  const keyValue = headers.length === 2 && /字段|项目|名称/.test(headers[0]) && /内容|说明|取值/.test(headers[1]);
  const tableClass = keyValue ? "markdown-table markdown-kv-table" : "markdown-table";
  return `
    <div class="markdown-table-wrap">
      <table class="${tableClass}">
        <thead>
          <tr>${headers.map((header) => `<th>${renderInlineMarkdown(header)}</th>`).join("")}</tr>
        </thead>
        <tbody>
          ${rows.map((row) => renderMarkdownTableRow(row, headers.length, keyValue)).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderMarkdownTableRow(row, columnCount, keyValue) {
  const normalized = [...row];
  while (normalized.length < columnCount) normalized.push("");
  const fieldClass = keyValue ? videoFieldRowClass(normalized[0]) : "";
  const rowClass = fieldClass ? ` class="${fieldClass}"` : "";
  return `<tr${rowClass}>${normalized.slice(0, columnCount).map((cell, index) => {
    const className = keyValue ? (index === 0 ? " class=\"markdown-kv-field\"" : " class=\"markdown-kv-value\"") : "";
    return `<td${className}>${renderMarkdownTableCell(cell, normalized[0], index, keyValue)}</td>`;
  }).join("")}</tr>`;
}

function renderMarkdownTableCell(cell, field, index, keyValue) {
  return renderInlineMarkdown(cell);
}

function videoFieldRowClass(field) {
  const text = String(field || "");
  if (/标准名称|名称/.test(text)) return "markdown-kv-name-row";
  if (/标准编号|编号|标准号/.test(text)) return "markdown-kv-code-row";
  if (/来源|PDF|文件/.test(text)) return "markdown-kv-source-row";
  if (/对象|适用/.test(text)) return "markdown-kv-object-row";
  if (/主题|风险|要求/.test(text)) return "markdown-kv-topic-row";
  return "";
}

function renderInlineMarkdown(value, options = {}) {
  let html = escapeHtml(normalizeMarkdownEntities(value));
  html = html.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)/g, (match, alt, url) => {
    const safeUrl = safeMarkdownUrl(url);
    if (!safeUrl) return match;
    return `<img class="markdown-image" src="${escapeHtml(safeUrl)}" alt="${alt}" loading="lazy" />`;
  });
  html = html.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)/g, (match, text, url) => {
    const safeUrl = safeMarkdownUrl(url);
    if (!safeUrl) return match;
    return `<a href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer">${text}</a>`;
  });
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  if (options.emphasis !== false) {
    html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/__([^_]+)__/g, "<strong>$1</strong>");
    html = html.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  }
  return html;
}

function normalizeMarkdownEntities(value) {
  return String(value ?? "")
    .replace(/&emsp;?/gi, "\u2003")
    .replace(/&#8195;/gi, "\u2003")
    .replace(/&ensp;?/gi, "\u2002")
    .replace(/&#8194;/gi, "\u2002")
    .replace(/&nbsp;?/gi, " ")
    .replace(/&#160;/gi, " ");
}

function safeMarkdownUrl(url) {
  const decoded = String(url || "")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, "\"")
    .trim();
  if (!decoded) return "";
  if (/^(https?:|data:image\/|\/|\.\/|\.\.\/|#)/i.test(decoded)) return decoded;
  return "";
}

function statusLabel(task) {
  if (task.status === "failed") return "处理失败";
  return stageText[task.current_stage] || stageText[task.status] || task.status || "未知状态";
}

function isVideoTaskIndeterminate(task) {
  return ["queued", "processing"].includes(task?.status);
}

function isVideoTerminal(task) {
  return ["completed", "completed_with_warnings", "failed"].includes(task?.status);
}

function videoProgressLabel(task) {
  if (!task) return "等待中";
  if (task.status === "failed") return "失败";
  if (["completed", "completed_with_warnings"].includes(task.status)) return "完成";
  const stepProgress = videoStageCountLabel(task);
  if (stepProgress) return stepProgress;
  const stage = task.current_stage || task.status;
  if (stage === "transcribing_asr" || stage === "transcribing") return "步骤 1/2";
  if (stage === "structuring_transcript") return "步骤 2/2";
  if (stage === "generating_markdown") return "生成中";
  if (stage === "generating_wireframes") return "生成中";
  if (stage === "extracting_keyframes") return "密集抽帧中";
  if (stage === "semantic_matching") return "图索引评分中";
  if (stage === "uploaded") return "等待中";
  return isVideoTaskIndeterminate(task) ? "处理中" : "等待中";
}

function videoStageCountLabel(task) {
  const total = Number(task.stage_total || 0);
  if (!Number.isFinite(total) || total <= 0) return "";
  const processed = Math.max(0, Math.min(total, Number(task.stage_processed || 0)));
  const message = String(task.stage_message || "").trim();
  return `${message ? `${message} ` : ""}${processed}/${total}`;
}

function startingVideoProgressLabel(mode) {
  if (mode === "transcript") return "步骤 1/2";
  if (mode === "markdown") return "生成中";
  if (mode === "keyframes") return "抽取中";
  if (mode === "wireframes") return "生成中";
  if (mode === "full") return "处理中";
  return "处理中";
}

function videoProgressBarClass(task) {
  return isVideoTaskIndeterminate(task) ? "indeterminate" : "";
}

function videoProgressBarWidth(task) {
  if (!task) return "0%";
  if (task.status === "failed") return "0%";
  if (["completed", "completed_with_warnings"].includes(task.status)) return "100%";
  if (isVideoTaskIndeterminate(task)) return "100%";
  return "0%";
}

function wireframeJobProgressLabel(job) {
  if (!job) return "等待中";
  if (job.status === "failed") return "失败";
  const total = Number(job.total_frames || 0);
  const completed = Number(job.completed_frames || 0);
  if (total > 0) return `${completed}/${total}`;
  if (job.status === "completed") return "完成";
  return "生成中";
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function formatTokenCount(value) {
  const count = Math.max(0, Number(value || 0));
  if (!Number.isFinite(count)) return "0";
  return Math.round(count).toLocaleString("zh-CN");
}

function formatTimestamp(seconds) {
  const total = Math.max(0, Math.round(Number(seconds || 0)));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function formatDateTime(value) {
  if (!value) return "未知时间";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function formatBeijingDateTime(value) {
  if (!value) return "未知时间";
  const date = parseBackendDateAsUtc(value);
  if (Number.isNaN(date.getTime())) return value;
  const beijing = new Date(date.getTime() + 8 * 60 * 60 * 1000 + 30 * 1000);
  return [
    beijing.getUTCFullYear(),
    String(beijing.getUTCMonth() + 1).padStart(2, "0"),
    String(beijing.getUTCDate()).padStart(2, "0"),
  ].join("-") + ` ${String(beijing.getUTCHours()).padStart(2, "0")}:${String(beijing.getUTCMinutes()).padStart(2, "0")}`;
}

function parseBackendDateAsUtc(value) {
  const text = String(value ?? "").trim();
  if (!text) return new Date(NaN);
  if (/[zZ]$|[+-]\d{2}:?\d{2}$/.test(text)) return new Date(text);
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?/);
  if (!match) return new Date(text);
  const [, year, month, day, hour, minute, second = "0", fraction = "0"] = match;
  const millisecond = Number(fraction.slice(0, 3).padEnd(3, "0"));
  return new Date(Date.UTC(
    Number(year),
    Number(month) - 1,
    Number(day),
    Number(hour),
    Number(minute),
    Number(second),
    millisecond,
  ));
}

function truncateText(value, maxLength) {
  const text = String(value ?? "");
  if (text.length <= maxLength) return text;
  return `${text.slice(0, Math.max(0, maxLength - 1)).trimEnd()}…`;
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#039;" }[ch]));
}

function domId(value) {
  return String(value ?? "").replace(/[^A-Za-z0-9_-]+/g, "_") || "item";
}

function compactDetails(items, separator = " · ") {
  return items.filter(Boolean).join(separator);
}

function escapeJsString(value) {
  return String(value ?? "").replace(/\\/g, "\\\\").replace(/'/g, "\\'");
}


async function bootstrapVideoApp() {
  setActiveView("videos");
  bindNavigation();
  bindVideoControls();
  await loadVideos();
}

bootstrapVideoApp().catch((error) => {
  console.error("failed to bootstrap video app", error);
});
