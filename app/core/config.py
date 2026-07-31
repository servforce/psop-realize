from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    value = env(name, "true" if default else "false").lower()
    return value not in {"", "0", "false", "no", "off"}


DEFAULT_MODEL_OPENAI_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com"


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: str = "dev"
    database_url: str = "sqlite:///./data/workbench.db"

    storage_backend: str = "minio"
    object_store_endpoint: str = "http://10.0.0.20:9000"
    object_store_access_key: str = "minioadmin"
    object_store_secret_key: str = "minioadmin"
    object_store_bucket: str = "servforce-materials"
    object_store_standard_bucket: str = "servforce-standards"
    object_store_region: str = "us-east-1"
    object_store_secure: bool = False

    mcp_client_id: str = "local-dev"

    wireframe_tool_dir: str = "./tools/qwen-image-wireframe"
    video_wireframe_generation_enabled: bool = False
    model_api_key: str = ""
    model_openai_base_url: str = DEFAULT_MODEL_OPENAI_BASE_URL
    model_dashscope_base_url: str = DEFAULT_MODEL_DASHSCOPE_BASE_URL
    wireframe_image_model: str = "qwen-image-2.0-pro"
    wireframe_image_size: str = "auto"
    wireframe_timeout_seconds: float = 900.0
    qwen_text_api_key: str = ""
    qwen_text_base_url: str = DEFAULT_MODEL_OPENAI_BASE_URL
    qwen_text_model: str = "qwen3.7-plus"
    qwen_text_temperature: float = 0.1
    qwen_text_top_p: float = 0.8
    qwen_text_max_tokens: int = 24000
    qwen_text_timeout_seconds: float = 900.0
    qwen_text_max_input_chars: int = 180000
    qwen_text_file_upload_purpose: str = "file-extract"
    transcript_structure_model: str = "qwen3.7-plus"
    qwen_vl_api_key: str = ""
    qwen_vl_base_url: str = DEFAULT_MODEL_OPENAI_BASE_URL
    qwen_vl_model: str = "qwen3-vl-plus"
    qwen_vl_temperature: float = 0.0
    qwen_vl_top_p: float = 0.8
    qwen_vl_max_tokens: int = 6000
    qwen_vl_timeout_seconds: float = 300.0
    qwen_vl_max_input_chars: int = 30000
    video_frame_selection_enabled: bool = True
    video_frame_selection_group_size: int = 8
    video_frame_selection_max_selected_frames: int = 24
    video_frame_selection_image_max_side: int = 768
    video_frame_selection_jpeg_quality: int = 85
    video_frame_selection_min_blur_score: float = 25.0
    video_frame_selection_dark_ratio_threshold: float = 0.9
    video_frame_selection_bright_ratio_threshold: float = 0.9
    video_frame_selection_min_mean_brightness: float = 8.0
    video_frame_selection_max_mean_brightness: float = 248.0
    video_analysis_proxy_height: int = 720
    video_analysis_proxy_crf: int = 28
    video_analysis_proxy_preset: str = "medium"
    video_analysis_proxy_audio_bitrate: str = "128k"
    video_dense_frame_interval_seconds: float = 2.0
    video_scene_change_frame_enabled: bool = True
    video_scene_change_threshold: float = 0.25
    video_candidate_frame_min_spacing_ms: int = 500
    video_dedup_time_window_seconds: float = 10.0
    video_dedup_hsv_similarity_threshold: float = 0.92
    video_dedup_phash_distance_threshold: int = 6
    video_semantic_embedding_api_key: str = ""
    video_semantic_embedding_base_url: str = DEFAULT_MODEL_DASHSCOPE_BASE_URL
    video_semantic_embedding_model: str = "qwen3-vl-embedding"
    video_semantic_embedding_timeout_seconds: float = 300.0
    video_semantic_top_frames_per_section: int = 0
    video_max_visual_operations_per_section: int = 3
    standard_embedding_api_key: str = ""
    standard_embedding_base_url: str = DEFAULT_MODEL_OPENAI_BASE_URL
    standard_embedding_model: str = "text-embedding-v4"
    standard_embedding_dimensions: int = 1024
    standard_embedding_timeout_seconds: float = 120.0
    standard_vector_search_min_score: float = 0.0
    local_asr_api_base_url: str = ""
    local_asr_language: str = "zh"
    local_asr_timeout_seconds: float = 3600.0
    local_asr_max_retries: int = 1
    local_asr_model_label: str = "Qwen/Qwen3-ASR-1.7B"
    video_workdir: str = "./work/videos"
    video_max_upload_bytes: int = 2 * 1024 * 1024 * 1024
    video_max_analyzed_frames: int = 10
    standard_workdir: str = "./work/standards"
    openstd_importer_tool_dir: str = "./tools/openstd-importer"
    openstd_object_store_bucket: str = "openstd"
    openstd_source_url: str = "https://openstd.samr.gov.cn/bzgk/std/"
    openstd_crawl_scope: str = "all_national_standards"
    openstd_allowed_statuses: str = "现行,即将实施"
    openstd_request_interval_seconds: float = 3.0
    openstd_max_retries: int = 2
    openstd_max_pages: int = 0
    openstd_max_items: int = 0
    openstd_download_timeout_seconds: float = 180.0
    worker_poll_interval_seconds: float = 1.0

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        model_api_key = (
            env("MODEL_API_KEY", "")
            or env("QWEN_TEXT_API_KEY", "")
            or env("DASHSCOPE_API_KEY", "")
        )
        model_openai_base_url = (
            env("MODEL_OPENAI_BASE_URL", "")
            or env("QWEN_TEXT_BASE_URL", "")
            or env("QWEN_VL_BASE_URL", "")
            or env("STANDARD_EMBEDDING_BASE_URL", "")
            or DEFAULT_MODEL_OPENAI_BASE_URL
        )
        model_dashscope_base_url = (
            env("MODEL_DASHSCOPE_BASE_URL", "")
            or env("DASHSCOPE_BASE_URL", "")
            or DEFAULT_MODEL_DASHSCOPE_BASE_URL
        )
        qwen_text_api_key = env("QWEN_TEXT_API_KEY", "") or model_api_key
        return cls(
            app_env=env("APP_ENV", "dev"),
            database_url=env("DATABASE_URL", "sqlite:///./data/workbench.db"),
            storage_backend=env("STORAGE_BACKEND", "minio").lower(),
            object_store_endpoint=env("OBJECT_STORE_ENDPOINT", "http://10.0.0.20:9000"),
            object_store_access_key=env("OBJECT_STORE_ACCESS_KEY", "minioadmin"),
            object_store_secret_key=env("OBJECT_STORE_SECRET_KEY", "minioadmin"),
            object_store_bucket=env("OBJECT_STORE_BUCKET", "servforce-materials"),
            object_store_standard_bucket=env("OBJECT_STORE_STANDARD_BUCKET", "servforce-standards"),
            object_store_region=env("OBJECT_STORE_REGION", "us-east-1"),
            object_store_secure=env_bool("OBJECT_STORE_SECURE", False),
            mcp_client_id=env("MCP_CLIENT_ID", "local-dev"),
            wireframe_tool_dir=env("WIREFRAME_TOOL_DIR", "./tools/qwen-image-wireframe"),
            video_wireframe_generation_enabled=env_bool("VIDEO_WIREFRAME_GENERATION_ENABLED", False),
            model_api_key=model_api_key,
            model_openai_base_url=model_openai_base_url,
            model_dashscope_base_url=model_dashscope_base_url,
            wireframe_image_model=env("WIREFRAME_IMAGE_MODEL", "qwen-image-2.0-pro"),
            wireframe_image_size=env("WIREFRAME_IMAGE_SIZE", "auto"),
            wireframe_timeout_seconds=float(env("WIREFRAME_TIMEOUT_SECONDS", "900")),
            qwen_text_api_key=qwen_text_api_key,
            qwen_text_base_url=env("QWEN_TEXT_BASE_URL", model_openai_base_url),
            qwen_text_model=env("QWEN_TEXT_MODEL", "qwen3.7-plus"),
            qwen_text_temperature=float(env("QWEN_TEXT_TEMPERATURE", "0.1")),
            qwen_text_top_p=float(env("QWEN_TEXT_TOP_P", "0.8")),
            qwen_text_max_tokens=int(env("QWEN_TEXT_MAX_TOKENS", "24000")),
            qwen_text_timeout_seconds=float(env("QWEN_TEXT_TIMEOUT_SECONDS", "900")),
            qwen_text_max_input_chars=int(env("QWEN_TEXT_MAX_INPUT_CHARS", "180000")),
            qwen_text_file_upload_purpose=env("QWEN_TEXT_FILE_UPLOAD_PURPOSE", "file-extract"),
            transcript_structure_model=env("TRANSCRIPT_STRUCTURE_MODEL", "qwen3.7-plus"),
            qwen_vl_api_key=env("QWEN_VL_API_KEY", "") or model_api_key,
            qwen_vl_base_url=env("QWEN_VL_BASE_URL", model_openai_base_url),
            qwen_vl_model=env("QWEN_VL_MODEL", "qwen3-vl-plus"),
            qwen_vl_temperature=float(env("QWEN_VL_TEMPERATURE", "0")),
            qwen_vl_top_p=float(env("QWEN_VL_TOP_P", "0.8")),
            qwen_vl_max_tokens=int(env("QWEN_VL_MAX_TOKENS", "6000")),
            qwen_vl_timeout_seconds=float(env("QWEN_VL_TIMEOUT_SECONDS", "300")),
            qwen_vl_max_input_chars=int(env("QWEN_VL_MAX_INPUT_CHARS", "30000")),
            video_frame_selection_enabled=env_bool("VIDEO_FRAME_SELECTION_ENABLED", True),
            video_frame_selection_group_size=int(env("VIDEO_FRAME_SELECTION_GROUP_SIZE", "8")),
            video_frame_selection_max_selected_frames=int(env("VIDEO_FRAME_SELECTION_MAX_SELECTED_FRAMES", "24")),
            video_frame_selection_image_max_side=int(env("VIDEO_FRAME_SELECTION_IMAGE_MAX_SIDE", "768")),
            video_frame_selection_jpeg_quality=int(env("VIDEO_FRAME_SELECTION_JPEG_QUALITY", "85")),
            video_frame_selection_min_blur_score=float(env("VIDEO_FRAME_SELECTION_MIN_BLUR_SCORE", "25")),
            video_frame_selection_dark_ratio_threshold=float(env("VIDEO_FRAME_SELECTION_DARK_RATIO_THRESHOLD", "0.9")),
            video_frame_selection_bright_ratio_threshold=float(env("VIDEO_FRAME_SELECTION_BRIGHT_RATIO_THRESHOLD", "0.9")),
            video_frame_selection_min_mean_brightness=float(env("VIDEO_FRAME_SELECTION_MIN_MEAN_BRIGHTNESS", "8")),
            video_frame_selection_max_mean_brightness=float(env("VIDEO_FRAME_SELECTION_MAX_MEAN_BRIGHTNESS", "248")),
            video_analysis_proxy_height=int(env("VIDEO_ANALYSIS_PROXY_HEIGHT", "720")),
            video_analysis_proxy_crf=int(env("VIDEO_ANALYSIS_PROXY_CRF", "28")),
            video_analysis_proxy_preset=env("VIDEO_ANALYSIS_PROXY_PRESET", "medium"),
            video_analysis_proxy_audio_bitrate=env("VIDEO_ANALYSIS_PROXY_AUDIO_BITRATE", "128k"),
            video_dense_frame_interval_seconds=float(env("VIDEO_DENSE_FRAME_INTERVAL_SECONDS", "2")),
            video_scene_change_frame_enabled=env_bool("VIDEO_SCENE_CHANGE_FRAME_ENABLED", True),
            video_scene_change_threshold=float(env("VIDEO_SCENE_CHANGE_THRESHOLD", "0.25")),
            video_candidate_frame_min_spacing_ms=int(env("VIDEO_CANDIDATE_FRAME_MIN_SPACING_MS", "500")),
            video_dedup_time_window_seconds=float(env("VIDEO_DEDUP_TIME_WINDOW_SECONDS", "10")),
            video_dedup_hsv_similarity_threshold=float(env("VIDEO_DEDUP_HSV_SIMILARITY_THRESHOLD", "0.92")),
            video_dedup_phash_distance_threshold=int(env("VIDEO_DEDUP_PHASH_DISTANCE_THRESHOLD", "6")),
            video_semantic_embedding_api_key=env("VIDEO_SEMANTIC_EMBEDDING_API_KEY", "") or env("QWEN_VL_EMBEDDING_API_KEY", "") or model_api_key,
            video_semantic_embedding_base_url=env("VIDEO_SEMANTIC_EMBEDDING_BASE_URL", model_dashscope_base_url),
            video_semantic_embedding_model=env("VIDEO_SEMANTIC_EMBEDDING_MODEL", "qwen3-vl-embedding"),
            video_semantic_embedding_timeout_seconds=float(env("VIDEO_SEMANTIC_EMBEDDING_TIMEOUT_SECONDS", "300")),
            video_semantic_top_frames_per_section=int(env("VIDEO_SEMANTIC_TOP_FRAMES_PER_SECTION", "0")),
            video_max_visual_operations_per_section=int(env("VIDEO_MAX_VISUAL_OPERATIONS_PER_SECTION", "3")),
            standard_embedding_api_key=env("STANDARD_EMBEDDING_API_KEY", "") or model_api_key,
            standard_embedding_base_url=env("STANDARD_EMBEDDING_BASE_URL", model_openai_base_url),
            standard_embedding_model=env("STANDARD_EMBEDDING_MODEL", "text-embedding-v4"),
            standard_embedding_dimensions=int(env("STANDARD_EMBEDDING_DIMENSIONS", "1024")),
            standard_embedding_timeout_seconds=float(env("STANDARD_EMBEDDING_TIMEOUT_SECONDS", "120")),
            standard_vector_search_min_score=float(env("STANDARD_VECTOR_SEARCH_MIN_SCORE", "0")),
            local_asr_api_base_url=env("LOCAL_ASR_API_BASE_URL", ""),
            local_asr_language=env("LOCAL_ASR_LANGUAGE", "zh"),
            local_asr_timeout_seconds=float(env("LOCAL_ASR_TIMEOUT_SECONDS", "3600")),
            local_asr_max_retries=int(env("LOCAL_ASR_MAX_RETRIES", "1")),
            local_asr_model_label=env("LOCAL_ASR_MODEL_LABEL", "Qwen/Qwen3-ASR-1.7B"),
            video_workdir=env("VIDEO_WORKDIR", "./work/videos"),
            video_max_upload_bytes=int(env("VIDEO_MAX_UPLOAD_BYTES", str(2 * 1024 * 1024 * 1024))),
            video_max_analyzed_frames=int(env("VIDEO_MAX_ANALYZED_FRAMES", "10")),
            standard_workdir=env("STANDARD_WORKDIR", "./work/standards"),
            openstd_importer_tool_dir=env("OPENSTD_IMPORTER_TOOL_DIR", "./tools/openstd-importer"),
            openstd_object_store_bucket=env("OPENSTD_OBJECT_STORE_BUCKET", "openstd"),
            openstd_source_url=env(
                "OPENSTD_SOURCE_URL",
                "https://openstd.samr.gov.cn/bzgk/std/",
            ),
            openstd_crawl_scope=env("OPENSTD_CRAWL_SCOPE", "all_national_standards"),
            openstd_allowed_statuses=env("OPENSTD_ALLOWED_STATUSES", "现行,即将实施"),
            openstd_request_interval_seconds=float(env("OPENSTD_REQUEST_INTERVAL_SECONDS", "3")),
            openstd_max_retries=int(env("OPENSTD_MAX_RETRIES", "2")),
            openstd_max_pages=int(env("OPENSTD_MAX_PAGES", "0")),
            openstd_max_items=int(env("OPENSTD_MAX_ITEMS", "0")),
            openstd_download_timeout_seconds=float(env("OPENSTD_DOWNLOAD_TIMEOUT_SECONDS", "180")),
            worker_poll_interval_seconds=float(env("WORKER_POLL_INTERVAL_SECONDS", "1")),
        )


settings = Settings.from_env()
