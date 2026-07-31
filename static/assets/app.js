const state = {
  videos: [],
  standards: [],
  selectedStandardId: null,
  selectedStandardDetail: null,
  activeStandardMarkdownKind: null,
  standardSearchText: "",
  standardSortOrder: "desc",
  standardLatestUploadActive: false,
  standardMarkdownView: "rendered",
  activeStandardView: "directory",
  activeStandardSearchView: "home",
  selectedVideoId: null,
  activeVideoView: "directory",
  activeVideoTab: "transcript",
  videoSearchText: "",
  videoSortOrder: "desc",
  videoLatestUploadActive: false,
  videoMarkdownView: "rendered",
  activeParseMode: null,
  videoStatusPoll: null,
  wireframeJobPoll: null,
  standardMaterializePoll: null,
  openstdCrawlPoll: null,
  openstdCrawlJob: null,
  logRefreshTimer: null,
  logRefreshInFlight: false,
};

const stageText = {
  uploaded: "已上传到 MinIO，等待解析",
  downloading_source: "正在从 MinIO 读取源视频",
  probing_video: "正在分析视频信息",
  preparing_analysis_proxy: "正在生成 720P H.265 解析代理视频",
  extracting_keyframes: "正在使用本地 FFmpeg 抽取候选业务帧",
  filtering_frames: "正在进行关键操作时间窗内图像质量过滤",
  deduplicating_frames: "正在进行 HSV+pHash 去重",
  semantic_matching: "正在调用 qwen3-vl-embedding 进行语义匹配",
  generating_wireframes: "正在筛选业务帧并生成线框图",
  transcribing: "正在调用本地 ASR 模型进行原始转写",
  transcribing_asr: "正在调用本地 ASR 模型进行原始转写",
  structuring_transcript: "正在调用 qwen3.7-plus 生成语义结构化转写",
  generating_markdown: "正在根据结构化转写生成视频分析 Markdown",
  uploading_artifacts: "正在上传产物到 MinIO",
  completed: "已完成",
  failed: "处理失败",
};

const standardMaterializeStageText = {
  idle: "尚未开始解析",
  starting: "准备解析标准 PDF",
  downloading_pdf: "正在读取源 PDF",
  extracting_pdf_text: "正在本地抽取 PDF 原文",
  preparing_pdf_upload: "准备上传 PDF 给模型",
  uploading_pdf_to_qwen: "正在上传 PDF 给 qwen3.7-plus",
  generating_body: "正在生成 standard_body.md",
  generating_structure: "正在生成 standard_structure.md",
  generating_logic: "正在生成 standard_logic.md",
  generating_overview: "正在生成 standard_overview.md",
  validating_markdown: "已通过校验，准备保存",
  uploading_artifacts: "正在保存四个 Markdown 文件",
  completed: "四个 Markdown 已生成",
  failed: "生成失败",
};

document.querySelectorAll(".sidebar button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".sidebar button").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    button.classList.add("active");
    const viewId = button.dataset.view;
    document.getElementById(viewId).classList.add("active");
    if (viewId === "videos") {
      setVideoWorkspaceView("directory");
    }
    if (viewId === "standards") {
      setStandardWorkspaceView("directory");
      loadLatestOpenstdCrawl();
    }
    if (viewId === "standardSearch") {
      setStandardSearchWorkspaceView("home");
      loadStandardIndexSummary();
      loadStandardSearchHistory();
    }
    if (viewId === "logs") {
      startLogAutoRefresh();
    } else {
      stopLogAutoRefresh();
    }
  });
});

document.getElementById("uploadVideo").addEventListener("click", uploadVideo);
document.getElementById("videoFile").addEventListener("change", (event) => {
  const file = event.target.files?.[0];
  if (file) uploadFile(file);
});
document.getElementById("reloadVideos")?.addEventListener("click", loadVideos);
document.getElementById("videoSearch").addEventListener("input", updateVideoSearchChrome);
document.getElementById("videoSearch").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    applyVideoSearch();
  }
});
document.getElementById("videoSearchButton").addEventListener("click", applyVideoSearch);
document.getElementById("videoSearchClear").addEventListener("click", clearVideoSearch);
document.getElementById("backToVideoDirectory")?.addEventListener("click", () => {
  setVideoWorkspaceView("directory");
});
document.getElementById("videoLatestUpload").addEventListener("click", () => {
  state.videoSortOrder = "desc";
  state.videoLatestUploadActive = true;
  updateVideoSortButton();
  renderVideoList();
});
document.getElementById("uploadStandards").addEventListener("click", uploadStandards);
document.getElementById("standardFiles").addEventListener("change", () => {
  uploadSelectedStandards();
});
document.getElementById("standardDirectorySearch").addEventListener("input", updateStandardSearchChrome);
document.getElementById("standardDirectorySearch").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    applyStandardSearch();
  }
});
document.getElementById("standardSearchButton").addEventListener("click", applyStandardSearch);
document.getElementById("standardSearchClear").addEventListener("click", clearStandardSearch);
document.getElementById("backToStandardDirectory")?.addEventListener("click", () => {
  setStandardWorkspaceView("directory");
});
document.getElementById("standardLatestUpload").addEventListener("click", () => {
  state.standardSortOrder = "desc";
  state.standardLatestUploadActive = true;
  updateStandardSortButton();
  renderStandardList();
});
document.getElementById("startOpenstdCrawl")?.addEventListener("click", startOpenstdCrawl);
document.getElementById("rebuildStandardIndex").addEventListener("click", rebuildStandardIndex);
document.getElementById("searchStandards").addEventListener("click", () => searchStandards("standardSearchText"));
document.getElementById("searchStandardsFromResult")?.addEventListener("click", () => searchStandards("standardSearchResultText"));
document.getElementById("backToStandardSearchHome")?.addEventListener("click", () => {
  syncStandardSearchInputs(document.getElementById("standardSearchResultText")?.value || "");
  setStandardSearchWorkspaceView("home");
});
document.getElementById("reloadStandardSearchHistory").addEventListener("click", loadStandardSearchHistory);

const dropZone = document.getElementById("dropZone");
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

const standardDropZone = document.getElementById("standardDropZone");
standardDropZone.addEventListener("click", (event) => {
  if (event.target.closest("button")) return;
  uploadStandards();
});
standardDropZone.addEventListener("dragover", (event) => {
  event.preventDefault();
  standardDropZone.classList.add("drag-over");
});
standardDropZone.addEventListener("dragleave", () => standardDropZone.classList.remove("drag-over"));
standardDropZone.addEventListener("drop", (event) => {
  event.preventDefault();
  standardDropZone.classList.remove("drag-over");
  const files = event.dataTransfer.files;
  if (files?.length) {
    document.getElementById("standardFiles").files = files;
    uploadSelectedStandards();
  }
});

async function uploadVideo() {
  const input = document.getElementById("videoFile");
  input.value = "";
  input.click();
}

function uploadFile(file) {
  const form = new FormData();
  form.append("file", file);
  form.append("title", document.getElementById("videoTitle")?.value || file.name);
  const xhr = new XMLHttpRequest();
  const uploadInfo = document.getElementById("uploadInfo");
  const uploadProgressBar = document.getElementById("uploadProgressBar");
  uploadProgressBar.classList.remove("indeterminate");
  uploadInfo.textContent = `${file.name} · ${formatBytes(file.size)} · 正在上传到服务器`;
  uploadProgressBar.style.width = "2%";
  xhr.upload.onprogress = (event) => {
    if (!event.lengthComputable) return;
    const percent = Math.max(2, Math.round((event.loaded / event.total) * 100));
    uploadProgressBar.style.width = `${percent}%`;
    if (percent >= 100) {
      uploadProgressBar.classList.add("indeterminate");
      uploadInfo.textContent = `${file.name} · 服务器已接收，正在保存到 MinIO`;
    } else {
      uploadInfo.textContent = `${file.name} · ${formatBytes(file.size)} · 上传到服务器 ${percent}%`;
    }
  };
  xhr.onload = async () => {
    if (xhr.status >= 200 && xhr.status < 300) {
      uploadProgressBar.classList.remove("indeterminate");
      uploadProgressBar.style.width = "100%";
      uploadInfo.textContent = `${file.name} · 已保存到 MinIO，等待解析`;
      const job = JSON.parse(xhr.responseText);
      await loadVideos();
      await showVideo(job.id);
      return;
    }
    uploadProgressBar.classList.remove("indeterminate");
    uploadProgressBar.style.width = "0%";
    uploadInfo.textContent = `上传失败：${xhr.responseText}`;
  };
  xhr.onerror = () => {
    uploadProgressBar.classList.remove("indeterminate");
    uploadProgressBar.style.width = "0%";
    uploadInfo.textContent = "上传失败：网络错误";
  };
  xhr.open("POST", "/api/videos");
  xhr.send(form);
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
        <span>按转写文本、抽取候选业务帧/语义帧、线框图、Markdown 顺序完成解析。</span>
      </div>
      <div class="analysis-section-label">单步解析</div>
      <div class="step-analysis-actions" aria-label="单步解析">
        <button onclick="parseVideo('${video.id}', 'transcript')">转写文本（包括结构化文本）</button>
        <button onclick="parseVideo('${video.id}', 'keyframes')">抽取候选业务帧/语义帧</button>
        <button id="generateVideoWireframes" onclick="parseVideo('${video.id}', 'wireframes')">转为线框图</button>
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
      <button class="result-tab" data-video-tab="frames" onclick="switchVideoTab('frames')">语义帧</button>
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
      <div id="wireframesGrid" class="frames-grid"></div>
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
        <div id="videoMarkdownRendered" class="standard-markdown-rendered video-markdown-rendered">
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
    keyframes: "抽取候选业务帧/语义帧",
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
    keyframes: "正在抽取候选业务帧并生成语义帧",
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
  const report = await fetchJson(`/api/videos/${id}/semantic-frames`).catch(() => null);
  if (report?.sections) {
    renderSemanticFrameReport(report);
    return;
  }
  const frames = await fetchJson(`/api/videos/${id}/frames`).catch(() => []);
  renderFrames(frames);
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
  const wireframes = await fetchJson(`/api/videos/${id}/wireframes`).catch(() => []);
  renderWireframes(wireframes);
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
    target.innerHTML = '<div class="empty">暂无语义帧结果。请先点击“转写文本（包括结构化文本）”，再点击“抽取候选业务帧/语义帧”。</div>';
    return;
  }
  const summary = report.summary || {};
  target.innerHTML = `
    <div class="semantic-frame-summary">
      <span>候选业务帧：${Number(summary.raw_frame_count || 0)}</span>
      <span>候选语义帧：${Number(summary.candidate_frame_count || 0)}</span>
      <span>文本段落：${Number(summary.section_count || sections.length)}</span>
      <span>关键操作：${Number(summary.visual_operation_count || summary.frame_query_count || 0)}</span>
      <span>模型：${escapeHtml(report.embedding_model || "qwen3-vl-embedding")}</span>
    </div>
    <div class="semantic-section-list">
      ${sections.map(renderSemanticSection).join("")}
    </div>
  `;
}

function renderSemanticSection(section) {
  const rawFrames = Array.isArray(section.raw_frames) ? section.raw_frames : [];
  const semanticFrames = Array.isArray(section.semantic_frames) ? section.semantic_frames : [];
  const frameQueryMatches = Array.isArray(section.frame_query_matches) ? section.frame_query_matches : [];
  const timeRange = `${section.start_time || formatTimestamp(section.start_seconds || 0)} - ${section.end_time || formatTimestamp(section.end_seconds || 0)}`;
  return `
    <section class="semantic-section">
      <div class="semantic-section-head">
        <div>
          <h3>${escapeHtml(section.index || "")}. ${escapeHtml(section.title || "文本段落")}</h3>
          <div class="muted">${escapeHtml(timeRange)}</div>
        </div>
      </div>
      <p class="semantic-section-text">${escapeHtml(section.text || "")}</p>
      <div class="semantic-frame-block">
        <h4>候选业务帧</h4>
        ${rawFrames.length ? `<div class="frames-grid compact-frames-grid">${rawFrames.map((frame) => renderFrameCard(frame, { score: false })).join("")}</div>` : '<div class="empty">该段落时间范围内暂无候选业务帧。</div>'}
      </div>
      <div class="semantic-frame-block">
        <h4>关键操作相似度复核</h4>
        ${frameQueryMatches.length ? renderFrameQueryMatches(frameQueryMatches) : (semanticFrames.length ? `<div class="frames-grid semantic-frames-grid">${semanticFrames.map((frame) => renderFrameCard(frame, { score: true })).join("")}</div>` : '<div class="empty">暂无语义帧匹配结果。</div>')}
      </div>
    </section>
  `;
}

function renderFrameQueryMatches(matches) {
  return `
    <div class="frame-query-match-list">
      ${matches.map((match) => {
        const frames = Array.isArray(match.frames) ? match.frames : [];
        const source = match.frame_queries_source || match.source || "";
        const sourceLabel = frameQuerySourceLabel(source);
        const operationTime = match.operation_start_time && match.operation_end_time ? `${match.operation_start_time} - ${match.operation_end_time}` : "";
        const operationText = match.operation_text || `关键操作 ${Number(match.operation_index || match.query_index || 0)}`;
        return `
          <section class="frame-query-match">
            <div class="frame-query-text">
              <strong>${Number(match.operation_index || match.query_index || 0)}. ${escapeHtml(operationText)}</strong>
              ${sourceLabel ? `<em class="frame-query-source ${source === "fallback_title_text" ? "fallback" : ""}">${escapeHtml(sourceLabel)}</em>` : ""}
              ${operationTime ? `<small>${escapeHtml(operationTime)}</small>` : ""}
              <span>${escapeHtml(match.query_text || match.embedding_text || "")}</span>
            </div>
            ${frames.length ? `<div class="frames-grid semantic-frames-grid">${frames.map((frame) => renderFrameCard(frame, { score: true })).join("")}</div>` : '<div class="empty">该关键操作暂无候选帧相似度。</div>'}
          </section>
        `;
      }).join("")}
    </div>
  `;
}

function frameQuerySourceLabel(source) {
  if (source === "fallback_title_text") return "fallback";
  if (source === "model") return "model";
  if (source === "visual_operations") return "关键操作";
  return "";
}

function renderFrameCard(frame, options = {}) {
  const url = frame.url || "";
  const timestamp = frame.timestamp_time || formatTimestamp(frame.timestamp_seconds || 0);
  const similarity = Number(frame.similarity);
  const scoreText = Number.isFinite(similarity) ? similarity.toFixed(4) : "";
  const filterLabel = frame.filter_label || rawFrameFilterLabel(frame.filter_status);
  return `
    <figure class="frame-card">
      <img src="${escapeHtml(url)}" alt="候选业务帧 ${escapeHtml(timestamp)}" loading="lazy" onclick="openImagePreview('${escapeJsString(url)}')" />
      <figcaption>
        <strong>${escapeHtml(timestamp)}</strong>
        ${options.score ? `<span>相似度：${escapeHtml(scoreText)}</span>` : (filterLabel ? `<span class="frame-filter-label">${escapeHtml(filterLabel)}</span>` : "")}
      </figcaption>
    </figure>
  `;
}

function rawFrameFilterLabel(status) {
  if (status === "quality_rejected") return "质量不通过";
  if (status === "duplicate_rejected") return "重复";
  return "";
}

function renderWireframes(wireframes) {
  const target = document.getElementById("wireframesGrid");
  if (!target) return;
  if (wireframes.some((item) => item.kind === "selection_preview")) {
    if (!wireframes.length) {
      target.innerHTML = '<div class="empty">暂无入选业务帧。点击生成线框图后会先完成业务帧筛选。</div>';
      return;
    }
    target.innerHTML = wireframes.map(renderFrameSelectionPreview).join("");
    return;
  }
  if (!wireframes.length) {
    target.innerHTML = '<div class="empty">暂无线框图。</div>';
    return;
  }
  target.innerHTML = wireframes.map((item) => `
    <figure class="frame-card">
      <img src="${item.url}" alt="线框图" loading="lazy" onclick="openImagePreview('${item.url}')" />
      <figcaption>
        <strong>线框图</strong>
        <span>${escapeHtml(item.object_key || "")}</span>
      </figcaption>
    </figure>
  `).join("");
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
    full: ["全部工作已完成", "候选业务帧、转写文本、业务帧筛选/线框图和 Markdown 都已完成。"],
    keyframes: ["语义帧抽取已完成", "已按文本段落输出候选业务帧、语义帧和相似度分数。"],
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
  const index = state.videos.findIndex((item) => item.id === video.id);
  if (index >= 0) state.videos[index] = video;
  else state.videos.unshift(video);
}

function startVideoStatusPolling(id) {
  if (state.videoStatusPoll) clearInterval(state.videoStatusPoll);
  let timer = null;
  const poll = async () => {
    if (state.selectedVideoId !== id) {
      if (timer) clearInterval(timer);
      state.videoStatusPoll = null;
      return;
    }
    const video = await fetchJson(`/api/videos/${id}`).catch(() => null);
    if (!video) return;
    updateVideoInState(video);
    renderVideoList();
    updateVideoProgress(video);
    if (!isVideoTaskIndeterminate(video)) {
      if (timer) clearInterval(timer);
      state.videoStatusPoll = null;
      renderVideoShell(video);
      applyParseCompletionMessage(state.activeParseMode, video);
      await refreshWireframeJob(id);
      await loadActiveVideoTab(id);
    }
  };
  poll();
  timer = setInterval(poll, 1000);
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

function uploadStandards() {
  const input = document.getElementById("standardFiles");
  input.value = "";
  input.click();
}

function uploadSelectedStandards() {
  const files = Array.from(document.getElementById("standardFiles").files || []);
  if (!files.length) return alert("请选择一个或多个 PDF");
  const form = new FormData();
  files.forEach((file) => form.append("files", file));
  const xhr = new XMLHttpRequest();
  const info = document.getElementById("standardUploadInfo");
  const bar = document.getElementById("standardUploadProgressBar");
  const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
  bar.classList.remove("indeterminate");
  info.textContent = `${files.length} 个 PDF · ${formatBytes(totalBytes)} · 正在上传到服务器`;
  bar.style.width = "2%";
  xhr.upload.onprogress = (event) => {
    if (!event.lengthComputable) return;
    const percent = Math.max(2, Math.round((event.loaded / event.total) * 100));
    bar.style.width = `${percent}%`;
    if (percent >= 100) {
      bar.classList.add("indeterminate");
      info.textContent = `${files.length} 个 PDF · 服务器已接收，正在保存到标准 MinIO bucket`;
    } else {
      info.textContent = `${files.length} 个 PDF · 上传到服务器 ${percent}%`;
    }
  };
  xhr.onload = async () => {
    if (xhr.status >= 200 && xhr.status < 300) {
      const payload = JSON.parse(xhr.responseText);
      bar.classList.remove("indeterminate");
      bar.style.width = "100%";
      info.textContent = `上传完成：${payload.count} 个 PDF 已写入标准 bucket`;
      await loadStandards();
      return;
    }
    bar.classList.remove("indeterminate");
    bar.style.width = "0%";
    info.textContent = `上传失败：${xhr.responseText}`;
  };
  xhr.onerror = () => {
    bar.classList.remove("indeterminate");
    bar.style.width = "0%";
    info.textContent = "上传失败：网络错误";
  };
  xhr.open("POST", "/api/standards/upload");
  xhr.send(form);
}

async function startOpenstdCrawl() {
  const button = document.getElementById("startOpenstdCrawl");
  const status = document.getElementById("openstdCrawlStatus");
  if (button) button.disabled = true;
  if (status) status.textContent = "正在创建采集任务...";
  try {
    const job = await fetchJson("/api/standards/openstd/crawl", { method: "POST" });
    state.openstdCrawlJob = job;
    renderOpenstdCrawlStatus(job);
    startOpenstdCrawlPolling(job.id);
  } catch (error) {
    if (status) status.textContent = `创建采集任务失败：${error.message}`;
    if (button) button.disabled = false;
  }
}

async function loadLatestOpenstdCrawl() {
  try {
    const job = await fetchJson("/api/standards/openstd/crawl/latest");
    if (!job || job.status === "none") {
      renderOpenstdCrawlStatus(null);
      return;
    }
    state.openstdCrawlJob = job;
    renderOpenstdCrawlStatus(job);
    if (isOpenstdCrawlRunning(job)) startOpenstdCrawlPolling(job.id);
  } catch {
    renderOpenstdCrawlStatus(null);
  }
}

function startOpenstdCrawlPolling(jobId) {
  if (state.openstdCrawlPoll) {
    clearInterval(state.openstdCrawlPoll);
    state.openstdCrawlPoll = null;
  }
  state.openstdCrawlPoll = setInterval(async () => {
    try {
      const job = await fetchJson(`/api/standards/openstd/crawl/${jobId}`);
      state.openstdCrawlJob = job;
      renderOpenstdCrawlStatus(job);
      if (!isOpenstdCrawlRunning(job)) {
        clearInterval(state.openstdCrawlPoll);
        state.openstdCrawlPoll = null;
        document.getElementById("startOpenstdCrawl").disabled = false;
        await loadStandards();
      }
    } catch (error) {
      const status = document.getElementById("openstdCrawlStatus");
      if (status) status.textContent = `读取采集进度失败：${error.message}`;
    }
  }, 3000);
}

function isOpenstdCrawlRunning(job) {
  return ["queued", "running"].includes(job?.status);
}

function renderOpenstdCrawlStatus(job) {
  const status = document.getElementById("openstdCrawlStatus");
  const bar = document.getElementById("openstdCrawlProgressBar");
  const button = document.getElementById("startOpenstdCrawl");
  if (!status || !bar) return;
  if (!job) {
    status.textContent = "暂无采集任务。";
    bar.style.width = "0%";
    bar.classList.remove("indeterminate");
    if (button) button.disabled = false;
    return;
  }
  const discovered = Number(job.total_discovered || 0);
  const done = Number(job.uploaded_count || 0)
    + Number(job.skipped_duplicate_count || 0)
    + Number(job.skipped_unavailable_count || 0)
    + Number(job.failed_count || 0);
  const percent = discovered > 0 ? Math.min(100, Math.round((done / discovered) * 100)) : 2;
  bar.style.width = `${percent}%`;
  bar.classList.toggle("indeterminate", isOpenstdCrawlRunning(job) && discovered === 0);
  const pageText = job.total_pages ? `页数 ${job.current_page || 0}/${job.total_pages}` : `页数 ${job.current_page || 0}`;
  const statusCounts = job.standard_status_counts || {};
  const currentStatus = Number(statusCounts.current || 0);
  const upcomingStatus = Number(statusCounts.upcoming || 0);
  const scrappedStatus = Number(statusCounts.scrapped || 0);
  const otherStatus = Number(statusCounts.other || 0);
  const otherStatusText = otherStatus ? ` · 其他 ${otherStatus}` : "";
  const current = job.current_item ? `当前：${job.current_item}` : "当前：暂无";
  const error = job.error_message ? `<div class="openstd-crawl-line openstd-crawl-error">错误：${escapeHtml(job.error_message)}</div>` : "";
  status.innerHTML = `
    <div class="openstd-crawl-line">${escapeHtml(openstdStatusLabel(job.status))} · ${escapeHtml(pageText)} · 发现 ${discovered} · 现行 ${currentStatus} · 即将实施 ${upcomingStatus} · 废止 ${scrappedStatus}${otherStatusText}</div>
    <div class="openstd-crawl-line">已上传 ${Number(job.uploaded_count || 0)} · 重复 ${Number(job.skipped_duplicate_count || 0)} · 不可下载 ${Number(job.skipped_unavailable_count || 0)} · 失败 ${Number(job.failed_count || 0)}</div>
    <div class="openstd-crawl-line">${escapeHtml(current)}</div>
    ${error}
  `;
  if (button) button.disabled = job.status === "running";
}

function openstdStatusLabel(status) {
  return {
    queued: "等待采集任务启动",
    running: "正在采集",
    completed: "采集完成",
    completed_with_errors: "采集完成，有失败项",
    failed: "采集失败",
  }[status] || "未知状态";
}

async function loadStandards() {
  state.standards = await fetchJson("/api/standards");
  renderStandardList();
  if (state.selectedStandardId) {
    const selected = state.standards.find((item) => item.id === state.selectedStandardId);
    if (selected) state.selectedStandardDetail = selected;
  }
};

window.showStandard = async (id) => {
  if (state.standardMaterializePoll) {
    state.standardMaterializePoll();
    state.standardMaterializePoll = null;
  }
  state.selectedStandardId = id;
  setStandardWorkspaceView("detail");
  state.activeStandardMarkdownKind = "overview";
  renderStandardList();
  const standard = await fetchJson(`/api/standards/${id}`);
  state.selectedStandardDetail = standard;
  renderStandardDetail(standard);
  await refreshStandardMaterializeStatus(id);
  await loadMarkdown(id, "overview");
};

function setStandardWorkspaceView(view) {
  state.activeStandardView = view === "detail" ? "detail" : "directory";
  const workspace = document.querySelector(".standard-workspace");
  const directoryPage = document.querySelector(".standard-directory-page");
  const detailPage = document.querySelector(".standard-detail-page");
  if (!workspace || !directoryPage || !detailPage) return;
  workspace.dataset.standardView = state.activeStandardView;
  directoryPage.classList.toggle("hidden", state.activeStandardView !== "directory");
  detailPage.classList.toggle("hidden", state.activeStandardView !== "detail");
  document.getElementById("standards")?.scrollIntoView({ block: "start" });
}

function renderStandardList() {
  const list = document.getElementById("standardList");
  updateStandardSortButton();
  const visibleStandards = getVisibleStandards();
  list.innerHTML = visibleStandards.map((s) => `
    <div class="item ${state.selectedStandardId === s.id ? "active" : ""}" onclick="showStandard('${escapeJsString(s.id)}')">
      <div class="video-list-item-head">
        <strong class="video-list-title">${escapeHtml(sourcePdfFilename(s))}</strong>
        <div class="muted video-list-uploaded-at">${escapeHtml(formatStandardUploadedAt(s))}</div>
      </div>
      <div class="video-list-item-meta">
        <span>状态：${escapeHtml(standardStatusLabel(s))}</span>
      </div>
    </div>`).join("") || `<div class='muted'>${state.standardSearchText.trim() ? "没有匹配的标准" : "暂无标准，先上传 PDF"}</div>`;
}

function applyStandardSearch() {
  state.standardSearchText = document.getElementById("standardDirectorySearch")?.value || "";
  state.standardLatestUploadActive = false;
  updateStandardSearchChrome();
  updateStandardSortButton();
  renderStandardList();
}

function clearStandardSearch() {
  const input = document.getElementById("standardDirectorySearch");
  if (input) {
    input.value = "";
    input.focus();
  }
  state.standardSearchText = "";
  state.standardLatestUploadActive = false;
  updateStandardSearchChrome();
  updateStandardSortButton();
  renderStandardList();
}

function updateStandardSearchChrome() {
  const input = document.getElementById("standardDirectorySearch");
  const hasValue = Boolean(input?.value.trim());
  document.querySelector(".standard-search-box")?.classList.toggle("has-value", hasValue);
  document.getElementById("standardSearchClear")?.classList.toggle("hidden", !hasValue);
}

function updateStandardSortButton() {
  document.getElementById("standardLatestUpload")?.classList.toggle("active", state.standardLatestUploadActive);
}

function getVisibleStandards() {
  const keyword = state.standardSearchText.trim().toLowerCase();
  return [...state.standards]
    .filter((standard) => {
      if (!keyword) return true;
      return `${standard.name || ""} ${standard.id || ""} ${sourcePdfFilename(standard)}`.toLowerCase().includes(keyword);
    })
    .sort((left, right) => {
      if (!state.standardLatestUploadActive) return 0;
      const leftTime = standardUploadedTime(left);
      const rightTime = standardUploadedTime(right);
      return state.standardSortOrder === "asc" ? leftTime - rightTime : rightTime - leftTime;
    });
}

function standardUploadedTime(standard) {
  const date = parseBackendDateAsUtc(standard.created_at || standard.updated_at);
  const time = date.getTime();
  return Number.isNaN(time) ? 0 : time;
}

function formatStandardUploadedAt(standard) {
  const value = standard.created_at || standard.updated_at;
  return `上传时间：${formatVideoDateTime(value)}`;
}

function standardStatusLabel(standard) {
  return {
    registered: "已上传，等待解析",
    processing: "正在解析",
    materialized: "已解析",
    failed: "解析失败",
  }[standard.status] || standard.status || "未知状态";
}

function renderStandardDetail(standard) {
  const standardMarkdownLabels = {
    overview: "standard_overview.md",
    structure: "standard_structure.md",
    logic: "standard_logic.md",
    body: "standard_body.md",
  };
  const tabs = ["overview", "structure", "logic", "body"].map((kind) =>
    `<button class="result-tab ${state.activeStandardMarkdownKind === kind ? "active" : ""}" data-standard-kind="${kind}" onclick="loadMarkdown('${standard.id}','${kind}')">${standardMarkdownLabels[kind]}</button>`).join("");
  document.getElementById("standardDetail").innerHTML = `
    <div class="video-analysis-head">
      <div>
        <h3>${escapeHtml(standard.name)}</h3>
        <div class="video-meta-row">
          <span>${escapeHtml(standardStatusLabel(standard))}</span>
          <span>${escapeHtml(formatStandardUploadedAt(standard).replace("上传时间：", ""))}</span>
          <span>${escapeHtml(standard.id)}</span>
        </div>
      </div>
    </div>
    <div class="analysis-actions">
      <div class="primary-analysis-action">
        <button onclick="materializeStandard('${standard.id}')">解析标准 PDF</button>
        <span>解析当前标准 PDF，并生成 overview、structure、logic、body 四个 Markdown。</span>
      </div>
      <div class="standard-action-row">
        <div class="analysis-section-label">批量解析</div>
        <button class="secondary" onclick="materializeAllStandards()">全部解析</button>
      </div>
    </div>
    <div class="analysis-progress">
      <div class="progress-row">
        <span id="standardStageText">等待解析 Markdown</span>
        <span id="standardProgressText">0%</span>
      </div>
      <div class="progress"><div id="standardMaterializeProgressBar"></div></div>
      <div id="standardMaterializeInfo" class="analysis-hint">选择解析方式后将在这里显示进度。</div>
    </div>
    <div class="analysis-section-label">解析结果</div>
    <div class="result-tabs">${tabs}</div>
    <div class="standard-markdown-tab">
      <div class="markdown-result">
        <div class="video-markdown-toolbar">
          <div class="video-markdown-switch" aria-label="Markdown 显示模式">
            <button type="button" data-standard-markdown-view="rendered" onclick="setStandardMarkdownView('rendered')">渲染视图</button>
            <button type="button" data-standard-markdown-view="source" onclick="setStandardMarkdownView('source')">源码视图</button>
          </div>
        </div>
        <button class="markdown-copy-button standard-markdown-copy-button" onclick="copyActiveStandardMarkdown()" aria-label="复制当前 Markdown">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <rect x="9" y="9" width="10" height="10" rx="2" />
            <rect x="5" y="5" width="10" height="10" rx="2" />
          </svg>
          <span>复制</span>
        </button>
        <div id="standardMarkdownRendered" class="standard-markdown-rendered">
          <div class="empty">正在加载 standard_overview.md...</div>
        </div>
        <pre id="standardMarkdown" class="hidden">正在加载 standard_overview.md...</pre>
      </div>
    </div>
  `;
  applyStandardMarkdownView();
}

window.materializeStandard = async (id) => {
  await materializeStandards([id]);
  await showStandard(id);
};

async function materializeAllStandards() {
  if (!state.standards.length) return alert("暂无标准可解析");
  await materializeStandards(state.standards.map((item) => item.id));
  if (state.selectedStandardId) await showStandard(state.selectedStandardId);
}

async function rebuildStandardIndex() {
  const resultTarget = document.getElementById("standardIndexResult");
  setStandardIndexProgress("正在重建标准向量索引...", true, 20);
  resultTarget.innerHTML = "<div class='muted'>正在重建标准向量索引...</div>";
  try {
    const result = await fetchJson("/api/standards/index/rebuild", { method: "POST" });
    setStandardIndexProgress(`向量索引已重建：${Number(result.indexed_count || 0)} 个标准`, false, 100);
    resultTarget.innerHTML = renderStandardIndexSummary(result, "向量索引已重建");
  } catch (error) {
    setStandardIndexProgress("重建向量索引失败", false, 0);
    resultTarget.innerHTML = `<div class="error">重建向量索引失败：${escapeHtml(error.message)}</div>`;
  }
}

async function loadStandardIndexSummary() {
  const resultTarget = document.getElementById("standardIndexResult");
  if (!resultTarget) return;
  const indexed = state.standards.filter((item) => item.index_status === "indexed").length;
  const failed = state.standards.filter((item) => item.index_status === "failed").length;
  setStandardIndexProgress(`已索引 ${indexed} 个标准`, false, indexed ? 100 : 0);
  resultTarget.innerHTML = renderStandardIndexSummary(
    {
      indexed_count: indexed,
      failed_count: failed,
      failed: state.standards
        .filter((item) => item.index_status === "failed")
        .map((item) => ({ standard_id: item.id, standard_name: item.name, reason: item.index_error || "" })),
    },
    "当前向量索引状态",
  );
}

function renderStandardIndexSummary(result, title) {
  const failed = result.failed || [];
  return `
    <h3>${escapeHtml(title)}</h3>
    <p class="muted">已索引：${Number(result.indexed_count || 0)} 个 · 失败：${Number(result.failed_count || 0)} 个</p>
    ${result.embedding_model ? `<p class="muted">模型：${escapeHtml(result.embedding_model)} · 维度：${Number(result.embedding_dimensions || 0)}</p>` : ""}
    ${failed.length ? `
      <h4>失败的标准</h4>
      <div class="list">
        ${failed.map((item) => `
          <div class="item">
            <strong>${escapeHtml(item.standard_name || item.standard_id)}</strong>
            <div class="muted">${escapeHtml(item.reason || "")}</div>
          </div>`).join("")}
      </div>` : "<h4>失败的标准</h4><div class='muted'>无</div>"}
  `;
}

async function materializeStandards(ids) {
  const info = document.getElementById("standardMaterializeInfo");
  const bar = document.getElementById("standardMaterializeProgressBar");
  if (!info || !bar) {
    alert("请先选择一个标准，在右侧详情中启动解析。");
    return;
  }
  bar.style.width = "2%";
  info.textContent = `开始解析：0/${ids.length}`;
  for (let index = 0; index < ids.length; index += 1) {
    const id = ids[index];
    const standard = state.standards.find((item) => item.id === id);
    const standardName = standard ? standard.name : id;
    info.textContent = `正在解析 ${index + 1}/${ids.length}：${standardName}`;
    try {
      const submitted = await fetchJson(`/api/standards/${id}/materialize`, { method: "POST" });
      updateStandardMaterializeProgress({ status: submitted, index, total: ids.length, name: standardName, info, bar });
      await waitForStandardMaterializeCompletion({ id, index, total: ids.length, name: standardName, info, bar });
    } catch (error) {
      const status = await fetchJson(`/api/standards/${id}/materialize-status`).catch(() => null);
      if (status) {
        updateStandardMaterializeProgress({ status, index, total: ids.length, name: standardName, info, bar });
      }
      info.textContent = `解析失败 ${index + 1}/${ids.length}：${standardName} · ${error.message}`;
      throw error;
    }
    const status = await fetchJson(`/api/standards/${id}/materialize-status`).catch(() => null);
    if (status) {
      updateStandardMaterializeProgress({ status, index, total: ids.length, name: standardName, info, bar });
    }
    const percent = Math.round(((index + 1) / ids.length) * 100);
    bar.style.width = `${percent}%`;
    info.textContent = `解析完成：${index + 1}/${ids.length}`;
    const progressTextEl = document.getElementById("standardProgressText");
    if (progressTextEl) progressTextEl.textContent = `${percent}%`;
  }
  await loadStandards();
}

async function waitForStandardMaterializeCompletion({ id, index, total, name, info, bar }) {
  while (true) {
    const status = await fetchJson(`/api/standards/${id}/materialize-status`);
    updateStandardMaterializeProgress({ status, index, total, name, info, bar });
    if (status.status === "completed" || status.stage === "completed") return status;
    if (status.status === "failed" || status.stage === "failed") {
      throw new Error(status.error || status.message || "解析失败");
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
}

async function refreshStandardMaterializeStatus(id) {
  const info = document.getElementById("standardMaterializeInfo");
  const bar = document.getElementById("standardMaterializeProgressBar");
  const standard = state.standards.find((item) => item.id === id) || state.selectedStandardDetail || { id };
  if (!info || !bar) return;
  const status = await fetchJson(`/api/standards/${id}/materialize-status`).catch(() => null);
  if (!status) return;
  if (status.status === "running" || status.status === "processing") {
    updateStandardMaterializeProgress({ status, index: 0, total: 1, name: standard.name || standard.id || id, info, bar });
    state.standardMaterializePoll = startStandardMaterializePolling({
      id,
      index: 0,
      total: 1,
      name: standard.name || standard.id || id,
      info,
      bar,
    });
  } else if (status.status === "completed" || status.stage === "completed" || status.status === "failed" || status.stage === "failed") {
    updateStandardMaterializeProgress({ status, index: 0, total: 1, name: standard.name || standard.id || id, info, bar });
  }
}

function startStandardMaterializePolling({ id, index, total, name, info, bar }) {
  let stopped = false;
  let timer = null;
  const poll = async () => {
    if (stopped) return;
    const status = await fetchJson(`/api/standards/${id}/materialize-status`).catch(() => null);
    if (status) updateStandardMaterializeProgress({ status, index, total, name, info, bar });
    if (status && (status.status === "completed" || status.stage === "completed" || status.status === "failed" || status.stage === "failed")) {
      stopped = true;
      if (timer) clearInterval(timer);
      if (state.standardMaterializePoll) state.standardMaterializePoll = null;
      await loadStandards();
    }
  };
  poll();
  timer = setInterval(poll, 1000);
  return () => {
    stopped = true;
    if (timer) clearInterval(timer);
  };
}

function updateStandardMaterializeProgress({ status, index, total, name, info, bar }) {
  const stepPercent = Math.max(0, Math.min(100, Number(status.progress_percent || 0)));
  const overallPercent = Math.round(((index + stepPercent / 100) / total) * 100);
  const fallbackError = "服务中断，后台任务未完成，请重新发起解析。";
  const messageText = readableStatusText(status.message, fallbackError);
  const errorText = readableStatusText(status.error, fallbackError);
  const stageLabel = standardMaterializeStageText[status.stage] || messageText || status.stage || "正在解析";
  bar.style.width = `${overallPercent}%`;
  const detail = errorText ? ` · ${errorText}` : messageText ? ` · ${messageText}` : "";
  info.textContent = `正在解析 ${index + 1}/${total}：${name} · ${stageLabel} · ${stepPercent}%${detail}`;
  const stageTextEl = document.getElementById("standardStageText");
  const progressTextEl = document.getElementById("standardProgressText");
  if (stageTextEl) stageTextEl.textContent = stageLabel;
  if (progressTextEl) progressTextEl.textContent = `${overallPercent}%`;
}

function readableStatusText(value, fallback) {
  const text = String(value || "").trim();
  if (!text) return "";
  return /^\?{4,}$/.test(text) ? fallback : text;
}

window.loadMarkdown = async (id, kind) => {
  state.activeStandardMarkdownKind = kind;
  document.querySelectorAll("[data-standard-kind]").forEach((button) => {
    button.classList.toggle("active", button.dataset.standardKind === kind);
  });
  const standard = state.selectedStandardDetail && state.selectedStandardDetail.id === id ? state.selectedStandardDetail : state.standards.find((item) => item.id === id);
  const label = standardMarkdownLabel(kind);
  setStandardMarkdownContent(`正在加载 ${label}...`, { placeholder: true });
  const response = await fetch(`/api/standards/${id}/markdown/${kind}`).catch(() => null);
  if (!response || !response.ok) {
    setStandardMarkdownContent(`还未解析【${sourcePdfFilename(standard)}】`, { placeholder: true });
    return;
  }
  const markdown = await response.text();
  setStandardMarkdownContent(markdown || `还未解析【${sourcePdfFilename(standard)}】`, { placeholder: !markdown });
};

window.setStandardMarkdownView = (view) => {
  state.standardMarkdownView = view === "source" ? "source" : "rendered";
  applyStandardMarkdownView();
};

function applyStandardMarkdownView() {
  const renderedEl = document.getElementById("standardMarkdownRendered");
  const sourceEl = document.getElementById("standardMarkdown");
  const sourceActive = state.standardMarkdownView === "source";
  if (renderedEl) renderedEl.classList.toggle("hidden", sourceActive);
  if (sourceEl) sourceEl.classList.toggle("hidden", !sourceActive);
  document.querySelectorAll("[data-standard-markdown-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.standardMarkdownView === state.standardMarkdownView);
  });
}

function setStandardMarkdownContent(markdown, options = {}) {
  const rawTarget = document.getElementById("standardMarkdown");
  const renderedTarget = document.getElementById("standardMarkdownRendered");
  if (rawTarget) rawTarget.textContent = markdown;
  if (!renderedTarget) {
    applyStandardMarkdownView();
    return;
  }
  if (options.placeholder) {
    renderedTarget.innerHTML = `<div class="empty">${escapeHtml(markdown)}</div>`;
    applyStandardMarkdownView();
    return;
  }
  renderedTarget.innerHTML = renderMarkdownDocument(markdown);
  applyStandardMarkdownView();
}

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
  const fieldClass = keyValue ? standardFieldRowClass(normalized[0]) : "";
  const rowClass = fieldClass ? ` class="${fieldClass}"` : "";
  return `<tr${rowClass}>${normalized.slice(0, columnCount).map((cell, index) => {
    const className = keyValue ? (index === 0 ? " class=\"markdown-kv-field\"" : " class=\"markdown-kv-value\"") : "";
    return `<td${className}>${renderMarkdownTableCell(cell, normalized[0], index, keyValue)}</td>`;
  }).join("")}</tr>`;
}

function renderMarkdownTableCell(cell, field, index, keyValue) {
  return renderInlineMarkdown(cell);
}

function standardFieldRowClass(field) {
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

function standardMarkdownLabel(kind) {
  return {
    overview: "standard_overview.md",
    structure: "standard_structure.md",
    logic: "standard_logic.md",
    body: "standard_body.md",
  }[kind] || `${kind}.md`;
}

function sourcePdfFilename(standard) {
  const objectKey = standard?.source_pdf_object_key || "";
  const filename = objectKey.replace(/\\/g, "/").split("/").filter(Boolean).pop();
  return filename || `${standard?.name || "标准"}.pdf`;
}

window.copyActiveStandardMarkdown = async () => {
  await copyTextResult({
    textId: "standardMarkdown",
    buttonSelector: ".standard-markdown-copy-button",
    emptyTexts: [
      "正在加载 standard_overview.md...",
      "正在加载 standard_structure.md...",
      "正在加载 standard_logic.md...",
      "正在加载 standard_body.md...",
      "还未解析",
    ],
  });
};

function setStandardSearchWorkspaceView(view) {
  state.activeStandardSearchView = view === "result" ? "result" : "home";
  const workspace = document.querySelector(".standard-search-workspace");
  const homePage = document.querySelector(".standard-search-home-page");
  const resultPage = document.querySelector(".standard-search-result-page");
  if (!workspace || !homePage || !resultPage) return;
  workspace.dataset.standardSearchView = state.activeStandardSearchView;
  homePage.classList.toggle("hidden", state.activeStandardSearchView !== "home");
  resultPage.classList.toggle("hidden", state.activeStandardSearchView !== "result");
  document.getElementById("standardSearch")?.scrollIntoView({ block: "start" });
}

function syncStandardSearchInputs(query) {
  const value = String(query ?? "");
  const homeInput = document.getElementById("standardSearchText");
  const resultInput = document.getElementById("standardSearchResultText");
  if (homeInput) homeInput.value = value;
  if (resultInput) resultInput.value = value;
}

async function searchStandards(sourceInputId = "standardSearchText") {
  const query = document.getElementById(sourceInputId)?.value || "";
  if (!query) return alert("请输入检索文本");
  syncStandardSearchInputs(query);
  setStandardSearchWorkspaceView("result");
  const resultTarget = document.getElementById("standardSearchResult");
  resultTarget.innerHTML = "<div class='muted'>正在进行标准向量检索...</div>";
  setStandardSearchProgress("准备检索：向量化输入文本", true, 12);
  const progressTimers = [
    setTimeout(() => setStandardSearchProgress("正在调用 embedding 模型", true, 38), 500),
    setTimeout(() => setStandardSearchProgress("正在通过 pgvector 匹配标准索引", true, 68), 1800),
  ];
  try {
    const result = await fetchJson(`/api/standards/search?query=${encodeURIComponent(query)}&limit=10`, { method: "POST" });
    progressTimers.forEach((timer) => clearTimeout(timer));
    setStandardSearchProgress(`检索完成：${(result.matches || []).length} 个匹配结果`, false, 100);
    resultTarget.innerHTML = renderStandardSearchResult(result);
    loadStandardSearchHistory();
  } catch (error) {
    progressTimers.forEach((timer) => clearTimeout(timer));
    setStandardSearchProgress("检索失败", false, 0);
    resultTarget.innerHTML = `<div class="error">检索失败：${escapeHtml(error.message)}</div>`;
    loadStandardSearchHistory();
  }
}

async function loadStandardSearchHistory() {
  const target = document.getElementById("standardSearchHistory");
  if (!target) return;
  target.innerHTML = "<div class='muted'>正在加载检索历史...</div>";
  try {
    const history = await fetchJson("/api/standards/search/history?limit=0");
    target.innerHTML = renderStandardSearchHistory(history);
  } catch (error) {
    target.innerHTML = `<div class="error">加载检索历史失败：${escapeHtml(error.message)}</div>`;
  }
}

function renderStandardSearchHistory(history) {
  if (!history.length) return "<div class='muted'>暂无检索历史</div>";
  return `
    <div class="history-list">
      <div class="muted">共 ${history.length} 条检索历史</div>
      ${history.map((item) => {
        const matches = item.matches || [];
        const encodedQuery = encodeURIComponent(item.query || "").replace(/'/g, "%27");
        return `
          <div class="history-card">
            <div class="history-card-head">
              <strong>${escapeHtml(formatDateTime(item.created_at))}</strong>
              <span class="status-pill ${item.status === "success" ? "ok" : "bad"}">${escapeHtml(item.status || "")}</span>
            </div>
            <p>${escapeHtml(truncateText(item.query || "无检索文本", 180))}</p>
            <div class="muted">匹配 ${Number(item.match_count || 0)} · 排除 ${Number(item.excluded_count || 0)} · ${Number(item.duration_ms || 0)} ms</div>
            ${item.error_message ? `<div class="error-text">${escapeHtml(item.error_message)}</div>` : ""}
            ${matches.length ? `
              <div class="history-matches">
                ${matches.map((match) => `
                  <span>${escapeHtml(match.standard_name || match.standard_id || "")} · ${escapeHtml(match.decision || "")} · ${Number(match.score || 0).toFixed(2)}</span>
                `).join("")}
              </div>` : ""}
            <div class="toolbar compact">
              <button class="secondary small" onclick="reuseStandardSearchQuery('${encodedQuery}')">复用检索文本</button>
            </div>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

window.reuseStandardSearchQuery = (encodedQuery) => {
  syncStandardSearchInputs(decodeURIComponent(encodedQuery || ""));
  setStandardSearchWorkspaceView("home");
  document.getElementById("standardSearchText").focus();
};

function setStandardSearchProgress(message, indeterminate = false, percent = 0) {
  const width = `${Math.max(0, Math.min(100, Number(percent || 0)))}%`;
  document.querySelectorAll("[data-standard-search-info]").forEach((info) => {
    info.textContent = message;
  });
  document.querySelectorAll("[data-standard-search-progress-bar]").forEach((bar) => {
    bar.classList.toggle("indeterminate", Boolean(indeterminate));
    bar.style.width = width;
  });
}

function setStandardIndexProgress(message, indeterminate = false, percent = 0) {
  const info = document.getElementById("standardIndexInfo");
  const bar = document.getElementById("standardIndexProgressBar");
  if (!info || !bar) return;
  info.textContent = message;
  bar.classList.toggle("indeterminate", Boolean(indeterminate));
  bar.style.width = `${Math.max(0, Math.min(100, Number(percent || 0)))}%`;
}

function renderStandardSearchResult(result) {
  const matches = result.matches || [];
  const strong = matches.filter((item) => item.decision === "应返回");
  const weak = matches.filter((item) => item.decision !== "应返回");
  return `
    <h3>标准检索结果</h3>
    <p class="muted">${escapeHtml(result.mode || "")}${result.embedding_model ? ` · ${escapeHtml(result.embedding_model)} · ${Number(result.embedding_dimensions || 0)} 维` : ""}</p>
    ${result.message ? `<p class="error">${escapeHtml(result.message)}</p>` : ""}
    <h4>强匹配</h4>
    <div class="search-result-list">${strong.map((item) => renderSearchStandardCard(item, "strong")).join("") || "<div class='muted'>暂无强匹配</div>"}</div>
    <h4>候选匹配</h4>
    <div class="search-result-list">${weak.map((item) => renderSearchStandardCard(item, "candidate")).join("") || "<div class='muted'>暂无候选匹配</div>"}</div>
  `;
}

function renderSearchStandardCard(item, group) {
  const standardId = item.standard_id || "";
  const previewId = `search-md-${domId(group)}-${domId(standardId || item.standard_name || Math.random())}`;
  const markdownKinds = ["overview", "structure", "logic", "body"];
  const buttons = markdownKinds.map((kind) => {
    const disabled = standardId ? "" : " disabled";
    return `<button class="tab" data-search-md="${previewId}-${kind}" onclick="previewSearchMarkdown('${escapeJsString(standardId)}','${kind}','${previewId}')"${disabled}>${kind}.md</button>`;
  }).join("");
  const downloadHref = standardId ? `/api/standards/${encodeURIComponent(standardId)}/markdown.zip` : "#";
  const downloadClass = `button-link small-download${standardId ? "" : " disabled"}`;
  return `
    <div class="search-standard-card">
      <div class="search-standard-head">
        <div>
          <strong>${escapeHtml(item.standard_name || standardId || "未知标准")}</strong>
          <div class="muted">${escapeHtml(item.decision || "")} · ${escapeHtml(item.match_level || "")} · score ${Number(item.score || 0).toFixed(2)} · ${escapeHtml(standardId)}</div>
        </div>
        <a class="${downloadClass}" href="${downloadHref}">下载全部</a>
      </div>
      ${item.reason ? `<p>${escapeHtml(item.reason)}</p>` : ""}
      ${(item.evidence || []).length ? `<ul>${item.evidence.map((e) => `<li>${escapeHtml(e)}</li>`).join("")}</ul>` : ""}
      <div class="toolbar compact">${buttons}</div>
      <pre id="${previewId}" class="search-markdown-preview">请选择一个 Markdown 文档查看内容。</pre>
    </div>`;
}

window.previewSearchMarkdown = async (standardId, kind, previewId) => {
  const preview = document.getElementById(previewId);
  if (!preview) return;
  if (!standardId) {
    preview.textContent = "当前检索结果没有对应的标准记录，无法读取 Markdown。";
    return;
  }
  document.querySelectorAll(`[data-search-md^="${previewId}-"]`).forEach((button) => {
    button.classList.toggle("active", button.dataset.searchMd === `${previewId}-${kind}`);
  });
  preview.textContent = `正在加载 ${kind}.md...`;
  try {
    const markdown = await fetch(`/api/standards/${encodeURIComponent(standardId)}/markdown/${kind}`).then((r) => {
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      return r.text();
    });
    preview.textContent = markdown || "暂无内容。";
  } catch (error) {
    preview.textContent = `加载失败：${error.message}`;
  }
};

async function loadLogs() {
  const logs = (await fetchJson("/api/call-logs")).filter(shouldShowCallLog);
  document.getElementById("logList").innerHTML = renderLogTable(logs);
}

function startLogAutoRefresh() {
  if (state.logRefreshTimer) return;
  refreshLogsSafely();
  state.logRefreshTimer = setInterval(refreshLogsSafely, 10000);
}

function stopLogAutoRefresh() {
  if (!state.logRefreshTimer) return;
  clearInterval(state.logRefreshTimer);
  state.logRefreshTimer = null;
}

async function refreshLogsSafely() {
  const list = document.getElementById("logList");
  if (!list) return;
  if (state.logRefreshInFlight) return;
  state.logRefreshInFlight = true;
  try {
    await loadLogs();
  } catch (error) {
    list.innerHTML = `<div class="error">调用记录刷新失败：${escapeHtml(error.message)}</div>`;
  } finally {
    state.logRefreshInFlight = false;
  }
}

function renderTable(rows, cols) {
  if (!rows.length) return "<div class='muted'>暂无记录</div>";
  return `<table><thead><tr>${cols.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>` +
    rows.map((r) => `<tr>${cols.map((c) => `<td>${escapeHtml(String(r[c] ?? ""))}</td>`).join("")}</tr>`).join("") +
    "</tbody></table>";
}

function renderLogTable(rows) {
  if (!rows.length) return "<div class='muted'>暂无调用记录</div>";
  const columns = [
    { key: "created_at", label: "创建时间 (Beijing)", render: (row) => formatBeijingDateTime(row.created_at) },
    { key: "caller", label: "调用方", render: (row) => row.caller || "anonymous" },
    { key: "interface_type", label: "调用类型", render: (row) => interfaceTypeLabel(row.interface_type) },
    { key: "tool_or_endpoint", label: "操作/步骤", render: (row) => formatLogAction(row), html: true },
    { key: "model_usage", label: "模型/总用量", render: (row) => formatModelUsage(row), html: true },
    { key: "status", label: "状态", render: (row) => statusText(row.status) },
    { key: "duration_ms", label: "耗时 (ms)", render: (row) => formatDuration(row.duration_ms) },
  ];
  return `<table class="log-table"><thead><tr>${columns.map((c) => `<th>${c.label}</th>`).join("")}</tr></thead><tbody>` +
    rows.map((row) => `<tr>${columns.map((column) => `<td>${formatLogTableCell(column, row)}</td>`).join("")}</tr>`).join("") +
    "</tbody></table>";
}

function formatLogTableCell(column, row) {
  const value = column.render(row);
  return column.html ? String(value || "") : escapeHtml(String(value ?? ""));
}

function shouldShowCallLog(row) {
  const target = row.tool_or_endpoint || "";
  if (row.interface_type === "rest" && /^GET \/api\/standards\/\{standard_id\}\/markdown\//.test(target)) {
    return false;
  }
  if (row.interface_type === "rest" && /^GET \/api\/standards\/[^/]+\/markdown\//.test(target)) {
    return false;
  }
  return true;
}

function formatLogAction(row) {
  const target = row.tool_or_endpoint || "";
  const request = parseJsonObject(row.request_summary);
  const videoId = row.video_id || request.video_id || "";
  const standardId = row.standard_id || request.standard_id || "";
  const mode = request.mode || "";

  if (row.interface_type === "rest") {
    if (target === "POST /api/videos") {
      return renderLogAction("视频解析 · 上传视频", "POST /api/videos");
    }
    if (target === "POST /api/videos/{video_id}/parse") {
      return renderLogAction(
        `视频解析 · ${videoParseSubmissionLabel(mode)}`,
        formatVideoParseEndpoint(videoId, mode),
      );
    }
    if (target === "POST /api/standards/upload") {
      return renderLogAction("标准库 · 上传 PDF", "POST /api/standards/upload");
    }
    if (target === "POST /api/standards/openstd/crawl") {
      return renderLogAction("标准库 · 一键爬取国家标准 PDF", "POST /api/standards/openstd/crawl");
    }
    if (target === "POST /api/standards/{standard_id}/materialize") {
      return renderLogAction("标准库 · 提交解析任务", formatStandardEndpoint(target, standardId));
    }
    if (target === "POST /api/standards/index/rebuild") {
      return renderLogAction("标准库检索 · 重建向量索引", "POST /api/standards/index/rebuild");
    }
    if (target === "POST /api/standards/{standard_id}/index/rebuild") {
      return renderLogAction("标准库检索 · 重建单标准索引", formatStandardEndpoint(target, standardId));
    }
    if (target === "POST /api/standards/search") {
      return renderLogAction("标准库检索 · 检索标准", "POST /api/standards/search");
    }
    if (target === "POST /api/standards/match-video/{video_id}") {
      return renderLogAction("标准库检索 · 匹配视频标准", formatVideoEndpoint(target, videoId));
    }
    if (target === "POST /api/wireframes/image") {
      return renderLogAction("线框图 · 生成图片", "POST /api/wireframes/image");
    }
  }

  if (row.interface_type === "background") {
    if (target === "video_parse_job") {
      return renderLogAction(
        `视频解析 · 后台执行：${videoParseStepLabel(mode)}`,
        compactDetails(["video_parse_job", mode ? `mode=${mode}` : "", videoId ? `video_id=${videoId}` : ""]),
      );
    }
    if (target === "standard_materialize_job") {
      return renderLogAction(
        "标准库 · 后台解析 PDF",
        compactDetails(["standard_materialize_job", standardId ? `standard_id=${standardId}` : ""]),
      );
    }
    if (target === "openstd_crawl_job") {
      return renderLogAction(
        "标准库 · 后台采集国家标准 PDF",
        compactDetails(["openstd_crawl_job", request.job_id ? `job_id=${request.job_id}` : ""]),
      );
    }
  }

  if (row.interface_type === "model") {
    if (target === "local_asr.audio_transcriptions") {
      return renderLogAction("模型调用 · 语音转写 ASR", target);
    }
    if (target === "qwen.chat.completions.transcript_tree") {
      return renderLogAction("模型调用 · 结构化转写", target);
    }
    if (target === "qwen.chat.completions.frame_selection") {
      return renderLogAction("模型调用 · 业务帧筛选", target);
    }
    if (target === "qwen.chat.completions.standard_markdown") {
      return renderLogAction("模型调用 · 生成标准 Markdown", target);
    }
    if (target === "qwen.chat.completions.standard_search") {
      return renderLogAction("模型调用 · 标准库检索判定", target);
    }
    if (target === "qwen.image.wireframe") {
      return renderLogAction("模型调用 · 生成线框图", target);
    }
  }

  const technicalTarget = formatTechnicalTarget(row, request);
  return renderLogAction(interfaceTypeLabel(row.interface_type), technicalTarget);
}

function renderLogAction(title, subtitle) {
  return `
    <div class="log-action-title">${escapeHtml(title || "未知操作")}</div>
    ${subtitle ? `<div class="muted log-action-subtitle">${escapeHtml(subtitle)}</div>` : ""}
  `;
}

function formatTechnicalTarget(row, request = parseJsonObject(row.request_summary)) {
  let target = row.tool_or_endpoint || "";
  const standardId = row.standard_id || request.standard_id || "";
  const videoId = row.video_id || request.video_id || "";
  if (standardId) target = target.replace("{standard_id}", standardId);
  if (videoId) target = target.replace("{video_id}", videoId);
  const details = [];
  if (standardId && !target.includes(standardId)) details.push(`standard_id=${standardId}`);
  if (videoId && !target.includes(videoId)) details.push(`video_id=${videoId}`);
  if (!target && details.length) return details.join(" · ");
  return details.length ? `${target} · ${details.join(" · ")}` : target;
}

function formatVideoEndpoint(target, videoId) {
  return videoId ? target.replace("{video_id}", videoId) : target;
}

function formatStandardEndpoint(target, standardId) {
  return standardId ? target.replace("{standard_id}", standardId) : target;
}

function formatVideoParseEndpoint(videoId, mode) {
  const base = videoId ? `POST /api/videos/${videoId}/parse` : "POST /api/videos/{video_id}/parse";
  return mode ? `${base}?mode=${mode}` : base;
}

function videoParseSubmissionLabel(mode) {
  if (mode === "full") return "一键解析";
  return `单步解析：${videoParseStepLabel(mode)}`;
}

function videoParseStepLabel(mode) {
  return {
    full: "一键解析",
    keyframes: "业务帧",
    wireframes: "线框图",
    transcript: "转写文本",
    markdown: "Markdown",
  }[mode] || mode || "解析";
}

function compactDetails(items) {
  return items.filter(Boolean).join(" · ");
}

function formatModelUsage(row) {
  if (row.interface_type !== "model") return "";
  const request = parseJsonObject(row.request_summary);
  const response = parseJsonObject(row.response_summary);
  const model = response.model || request.model || "";
  const usage = response.usage && typeof response.usage === "object" ? response.usage : {};
  const totalTokens = usage.total_tokens ?? usage.totalTokens ?? sumTokenUsage(usage);
  const modelText = formatModelLink(model);
  if (totalTokens !== null) return `${modelText} · ${escapeHtml(String(totalTokens))}`;
  return modelText;
}

function formatModelLink(model) {
  const name = String(model || "").trim();
  if (!name) return "";
  const url = modelOfficialUrl(name);
  if (!url) return escapeHtml(name);
  return `<a class="model-official-link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" title="打开阿里云百炼模型页">${escapeHtml(name)}<span aria-hidden="true">↗</span></a>`;
}

function modelOfficialUrl(model) {
  const normalized = String(model || "").trim().toLowerCase();
  if (!normalized || normalized.includes("/")) return "";
  if (/^(text-embedding|qwen.*embedding|tongyi.*embedding|gte.*embedding|gte-rerank|text-rerank)/.test(normalized)) {
    return bailianModelDetailUrl(normalized);
  }
  if (/^(qwen|tongyi|deepseek)/.test(normalized)) {
    return bailianModelDetailUrl(normalized);
  }
  return "";
}

function bailianModelDetailUrl(modelId) {
  return `https://bailian.console.aliyun.com/cn-beijing/?tab=model#/model-market/detail/${encodeURIComponent(modelId)}`;
}

function sumTokenUsage(usage) {
  const input = usage.prompt_tokens ?? usage.input_tokens;
  const output = usage.completion_tokens ?? usage.output_tokens;
  if (input === undefined && output === undefined) return null;
  return Number(input || 0) + Number(output || 0);
}

function parseJsonObject(value) {
  if (!value) return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function interfaceTypeLabel(value) {
  const labels = {
    rest: "REST 接口",
    mcp: "MCP 工具",
    background: "后台任务",
    model: "模型调用",
  };
  return labels[value] || value || "未知";
}

function statusText(value) {
  const labels = {
    running: "运行中",
    success: "成功",
    failed: "失败",
  };
  return labels[value] || value || "未知";
}

function formatDuration(value) {
  const ms = Number(value || 0);
  if (!Number.isFinite(ms) || ms <= 0) return "0";
  return String(Math.round(ms));
}

function statusLabel(task) {
  if (task.status === "failed") return "处理失败";
  return stageText[task.current_stage] || stageText[task.status] || task.status || "未知状态";
}

function isVideoTaskIndeterminate(task) {
  return ["queued", "processing"].includes(task?.status);
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
  if (stage === "semantic_matching") return "语义匹配中";
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

function escapeJsString(value) {
  return String(value ?? "").replace(/\\/g, "\\\\").replace(/'/g, "\\'");
}

loadVideos();
loadStandards();
loadLatestOpenstdCrawl();

