from __future__ import annotations

from dataclasses import dataclass

from app.core.env import (
    DEFAULT_MODEL_OPENAI_BASE_URL,
    env,
    env_bool,
    load_dotenv,
)


@dataclass(frozen=True)
class VideoSettings:
    app_env: str = "dev"
    video_database_url: str = "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/octopus_video"

    storage_backend: str = "minio"
    object_store_endpoint: str = "http://10.0.0.20:9000"
    object_store_access_key: str = "minioadmin"
    object_store_secret_key: str = "minioadmin"
    object_store_bucket: str = "servforce-materials"
    object_store_region: str = "us-east-1"
    object_store_secure: bool = False

    model_api_key: str = ""
    model_openai_base_url: str = DEFAULT_MODEL_OPENAI_BASE_URL

    qwen_text_api_key: str = ""
    qwen_text_base_url: str = DEFAULT_MODEL_OPENAI_BASE_URL
    qwen_text_model: str = "qwen3.7-plus"
    qwen_text_temperature: float = 0.1
    qwen_text_top_p: float = 0.8
    qwen_text_max_tokens: int = 24000
    qwen_text_timeout_seconds: float = 900.0
    qwen_text_max_input_chars: int = 600000
    transcript_structure_model: str = "qwen3.7-plus"

    local_asr_api_base_url: str = ""
    local_asr_language: str = "zh"
    local_asr_timeout_seconds: float = 3600.0
    local_asr_max_retries: int = 1
    local_asr_model_label: str = "Qwen/Qwen3-ASR-1.7B"

    video_workdir: str = "./work/videos"
    video_max_upload_bytes: int = 2 * 1024 * 1024 * 1024
    video_max_analyzed_frames: int = 10
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
    video_graph_index_device: str = "cuda"
    video_graph_index_finetuned_yolo_world_model: str = "runs/yolo_world/robot_arm_parts_v1/weights/best.pt"
    video_graph_index_mobile_sam_model: str = "sam2.1_b.pt"
    video_graph_index_yolo_world_confidence: float = 0.10
    video_graph_index_yolo_world_iou: float = 0.35
    video_graph_index_yolo_world_max_det: int = 12
    video_semantic_top_frames_per_section: int = 0
    worker_poll_interval_seconds: float = 1.0

    @classmethod
    def from_env(cls) -> "VideoSettings":
        load_dotenv()
        model_api_key = (
            env("MODEL_API_KEY", "")
            or env("QWEN_TEXT_API_KEY", "")
            or env("DASHSCOPE_API_KEY", "")
        )
        model_openai_base_url = (
            env("MODEL_OPENAI_BASE_URL", "")
            or env("QWEN_TEXT_BASE_URL", "")
            or DEFAULT_MODEL_OPENAI_BASE_URL
        )
        qwen_text_api_key = env("QWEN_TEXT_API_KEY", "") or model_api_key
        return cls(
            app_env=env("APP_ENV", "dev"),
            video_database_url=env(
                "VIDEO_DATABASE_URL",
                "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/octopus_video",
            ),
            storage_backend=env("STORAGE_BACKEND", "minio").lower(),
            object_store_endpoint=env("OBJECT_STORE_ENDPOINT", "http://10.0.0.20:9000"),
            object_store_access_key=env("OBJECT_STORE_ACCESS_KEY", "minioadmin"),
            object_store_secret_key=env("OBJECT_STORE_SECRET_KEY", "minioadmin"),
            object_store_bucket=env("OBJECT_STORE_BUCKET", "servforce-materials"),
            object_store_region=env("OBJECT_STORE_REGION", "us-east-1"),
            object_store_secure=env_bool("OBJECT_STORE_SECURE", False),
            model_api_key=model_api_key,
            model_openai_base_url=model_openai_base_url,
            qwen_text_api_key=qwen_text_api_key,
            qwen_text_base_url=env("QWEN_TEXT_BASE_URL", model_openai_base_url),
            qwen_text_model=env("QWEN_TEXT_MODEL", "qwen3.7-plus"),
            qwen_text_temperature=float(env("QWEN_TEXT_TEMPERATURE", "0.1")),
            qwen_text_top_p=float(env("QWEN_TEXT_TOP_P", "0.8")),
            qwen_text_max_tokens=int(env("QWEN_TEXT_MAX_TOKENS", "24000")),
            qwen_text_timeout_seconds=float(env("QWEN_TEXT_TIMEOUT_SECONDS", "1800")),
            qwen_text_max_input_chars=int(env("QWEN_TEXT_MAX_INPUT_CHARS", "600000")),
            transcript_structure_model=env("TRANSCRIPT_STRUCTURE_MODEL", "qwen3.7-plus"),
            local_asr_api_base_url=env("LOCAL_ASR_API_BASE_URL", ""),
            local_asr_language=env("LOCAL_ASR_LANGUAGE", "zh"),
            local_asr_timeout_seconds=float(env("LOCAL_ASR_TIMEOUT_SECONDS", "3600")),
            local_asr_max_retries=int(env("LOCAL_ASR_MAX_RETRIES", "1")),
            local_asr_model_label=env("LOCAL_ASR_MODEL_LABEL", "Qwen/Qwen3-ASR-1.7B"),
            video_workdir=env("VIDEO_WORKDIR", "./work/videos"),
            video_max_upload_bytes=int(env("VIDEO_MAX_UPLOAD_BYTES", str(2 * 1024 * 1024 * 1024))),
            video_max_analyzed_frames=int(env("VIDEO_MAX_ANALYZED_FRAMES", "10")),
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
            video_graph_index_device=env("VIDEO_GRAPH_INDEX_DEVICE", "cuda"),
            video_graph_index_finetuned_yolo_world_model=env(
                "VIDEO_GRAPH_INDEX_FINETUNED_YOLO_WORLD_MODEL",
                "runs/yolo_world/robot_arm_parts_v1/weights/best.pt",
            ),
            video_graph_index_mobile_sam_model=env("VIDEO_GRAPH_INDEX_MOBILE_SAM_MODEL", "sam2.1_b.pt"),
            video_graph_index_yolo_world_confidence=float(env("VIDEO_GRAPH_INDEX_YOLO_WORLD_CONFIDENCE", "0.10")),
            video_graph_index_yolo_world_iou=float(env("VIDEO_GRAPH_INDEX_YOLO_WORLD_IOU", "0.35")),
            video_graph_index_yolo_world_max_det=int(env("VIDEO_GRAPH_INDEX_YOLO_WORLD_MAX_DET", "12")),
            video_semantic_top_frames_per_section=int(env("VIDEO_SEMANTIC_TOP_FRAMES_PER_SECTION", "0")),
            worker_poll_interval_seconds=float(env("WORKER_POLL_INTERVAL_SECONDS", "1")),
        )


video_settings = VideoSettings.from_env()
