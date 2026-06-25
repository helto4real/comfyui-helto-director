export const SCHEMA_VERSION = "2.0";
export const VIDEO_TIMELINE_TYPE = "VIDEO_TIMELINE";
export const PROJECT_STORAGE_SCHEMA_VERSION = 1;
export const DEFAULT_PROJECT_NAME = "Untitled Project";

export const SECTION_TYPE_IMAGE = "Image";
export const SECTION_TYPE_TEXT = "Text";
export const SECTION_TYPE_VIDEO = "Video";
export const SECTION_TYPES = [
  SECTION_TYPE_IMAGE,
  SECTION_TYPE_TEXT,
  SECTION_TYPE_VIDEO,
];

export const ASSET_TYPE_IMAGE = "Image";
export const ASSET_TYPE_VIDEO = "Video";
export const ASSET_TYPE_AUDIO = "Audio";
export const ASSET_TYPES = [
  ASSET_TYPE_IMAGE,
  ASSET_TYPE_VIDEO,
  ASSET_TYPE_AUDIO,
];

export const ASSET_SOURCE_FILE_PATH = "FilePath";
export const ASSET_SOURCE_UPLOADED_FILE = "UploadedFile";
export const ASSET_SOURCE_GENERATED = "Generated";
export const ASSET_SOURCE_COMFYUI_INPUT = "ComfyUIInput";
export const ASSET_SOURCE_KINDS = [
  ASSET_SOURCE_FILE_PATH,
  ASSET_SOURCE_UPLOADED_FILE,
  ASSET_SOURCE_GENERATED,
  ASSET_SOURCE_COMFYUI_INPUT,
];

export const CROP_MODE_PROJECT_DEFAULT = "Project Default";
export const CROP_MODES = [
  CROP_MODE_PROJECT_DEFAULT,
  "Crop",
  "Pad",
  "Stretch to Fit",
  "Keep Aspect Ratio",
];

export const VIDEO_TIMING_MODES = [
  "Fit to Section",
  "Use Source Timing",
  "Loop",
  "Freeze Last Frame",
];

export const VIDEO_GUIDANCE_RANGES = [
  "Last Frames",
  "Full Source Range",
];

export const VIDEO_GUIDANCE_FRAME_COUNTS = [
  1,
  9,
  17,
  25,
  33,
  49,
  65,
];

export const TIMELINE_DISPLAY_MODES = [
  "Sections",
  "Media",
  "Prompts",
];

export const SECTION_EDIT_MODES = [
  "Trim Neighbor",
  "Ripple Edit",
];

export const SNAP_MODES = [
  "None",
  "Seconds",
  "Frames",
];

export const GLOBAL_PROMPT_POSITIONS = [
  "Prefix",
  "Suffix",
];

export const AUDIO_NORMALIZATION_MODES = [
  "Integrated LUFS",
  "Peak",
];
export const QUALITY_PRESETS = [
  "Quick Draft",
  "Draft",
  "Standard",
  "High",
  "Native Resolution",
];

export const SEQUENCE_ID_MAIN = "main";
export const SEQUENCE_NAME_MAIN = "Main Timeline";

export const SHOT_TYPES = [
  "Generated",
  "Imported",
  "Extended",
  "Edited",
  "Placeholder",
];

export const BOUNDARY_MODES = [
  "Hard Cut",
  "Continuous Shot",
  "Blend Seam",
  "Transition",
];

export const TAKE_STATUSES = [
  "Candidate",
  "Accepted",
  "Rejected",
];

export const LORA_MERGE_MODES = [
  "Inherit Global",
  "Add To Global",
  "Replace Global",
  "Disable LoRAs",
];

export const MODEL_LORA_SCHEMA_VERSION = 2;
export const MODEL_LORA_MODEL_LTX_2_3 = "ltx_2_3";
export const MODEL_LORA_MODEL_WAN_2_2 = "wan_2_2";
export const MODEL_LORA_TARGET_MAIN = "main";
export const MODEL_LORA_TARGET_HIGH_NOISE = "high_noise";
export const MODEL_LORA_TARGET_LOW_NOISE = "low_noise";

export const DEFAULT_DURATION_SECONDS = 5.0;
export const DEFAULT_FRAME_RATE = 24.0;
export const DEFAULT_ASPECT_RATIO = "16:9";
export const DEFAULT_ORIENTATION = "Landscape";
export const DEFAULT_QUALITY_PRESET = "Standard";

export function createDefaultLoraStack() {
  return {
    version: 1,
    loras: [],
    ui: { show_strengths: "single", match: "" },
  };
}

export function createDefaultProjectModelLoras() {
  return {
    schema_version: MODEL_LORA_SCHEMA_VERSION,
    global: {
      [MODEL_LORA_MODEL_LTX_2_3]: {
        [MODEL_LORA_TARGET_MAIN]: createDefaultLoraStack(),
      },
      [MODEL_LORA_MODEL_WAN_2_2]: {
        [MODEL_LORA_TARGET_HIGH_NOISE]: createDefaultLoraStack(),
        [MODEL_LORA_TARGET_LOW_NOISE]: createDefaultLoraStack(),
      },
    },
  };
}

export function createDefaultSequence() {
  return {
    sequence_id: SEQUENCE_ID_MAIN,
    name: SEQUENCE_NAME_MAIN,
    shots: [],
    boundaries: [],
  };
}

export function createDefaultShot(index = 1) {
  return {
    shot_id: `shot_${String(index).padStart(3, "0")}`,
    name: "",
    type: "Generated",
    start_time: 0.0,
    end_time: 0.0,
    section_ids: [],
    lora_overrides: {
      enabled: false,
      merge_mode: "Inherit Global",
      targets: {},
    },
    takes: [],
    accepted_take_id: null,
    clip_instance: null,
    metadata: {},
  };
}

export function createDefaultBoundary(index = 1) {
  return {
    boundary_id: `boundary_${String(index).padStart(3, "0")}`,
    left_shot_id: null,
    right_shot_id: null,
    mode: "Hard Cut",
    tail_frames: 5,
    blend_frames: 3,
    transition_prompt: "",
    reuse_character_refs: true,
    reuse_style: true,
    metadata: {},
  };
}

export function createDefaultTake(index = 1) {
  return {
    take_id: `take_${String(index).padStart(3, "0")}`,
    asset_id: null,
    status: "Candidate",
    seed: null,
    model_family: "",
    model_version: "",
    plan_hash: "",
    prompt_hash: "",
    resolved_loras: null,
    metadata: {},
  };
}

export function createDefaultClipInstance() {
  return {
    asset_id: null,
    source_in: 0.0,
    source_out: null,
    speed: 1.0,
    enabled: true,
  };
}

export function createDefaultProjectIdentity() {
  return {
    project_id: createProjectId(),
    name: DEFAULT_PROJECT_NAME,
  };
}

export function createDefaultProjectStorage(identity = createDefaultProjectIdentity()) {
  return {
    schema_version: PROJECT_STORAGE_SCHEMA_VERSION,
    asset_root_directory: "",
    project_directory_name: projectDirectoryName(identity.name, identity.project_id),
  };
}

export function createDefaultVideoTimeline() {
  const identity = createDefaultProjectIdentity();
  return {
    schema_version: SCHEMA_VERSION,
    type: VIDEO_TIMELINE_TYPE,
    project: {
      identity,
      duration_seconds: DEFAULT_DURATION_SECONDS,
      frame_rate: DEFAULT_FRAME_RATE,
      aspect_ratio: DEFAULT_ASPECT_RATIO,
      orientation: DEFAULT_ORIENTATION,
      quality_preset: DEFAULT_QUALITY_PRESET,
      default_crop_mode: CROP_MODE_PROJECT_DEFAULT,
      settings: {
        allow_gaps: true,
        auto_close_gaps: false,
        minimum_section_duration_seconds: 0.25,
        show_resolved_model_output: false,
      },
      global_prompt: {
        enabled: false,
        prompt: "",
        position: "Prefix",
        show_effective_prompt: false,
      },
      audio: {
        use_native_audio: false,
        always_normalize: false,
        normalization_mode: "Integrated LUFS",
        target_lufs: -16.0,
        true_peak_limit_db: -1.0,
        default_volume: 100.0,
        default_fade_in_seconds: 0.0,
        default_fade_out_seconds: 0.0,
      },
      privacy: {
        mode: true,
      },
      display: {
        show_section_labels: true,
        show_thumbnails: true,
        show_audio_waveforms: true,
      },
      metadata: {
        character_references_enabled: true,
        character_references: [],
      },
      storage: createDefaultProjectStorage(identity),
      model_loras: createDefaultProjectModelLoras(),
    },
    ui_state: {
      timeline_display_mode: "Sections",
      section_edit_mode: "Trim Neighbor",
      snap_mode: "Frames",
      view_start_seconds: 0,
      view_end_seconds: Math.ceil(DEFAULT_DURATION_SECONDS),
      selected_item_id: null,
      selected_item_ids: [],
      state_revision: 0,
    },
    assets: [],
    sequence: createDefaultSequence(),
    director_track: {
      track_id: "director",
      sections: [],
    },
    audio_tracks: [],
    model_outputs: {},
    validation: {
      is_valid: true,
      errors: [],
      warnings: [],
      info: [],
    },
  };
}

export function deepClone(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value));
}

export function createProjectId() {
  const random = Math.random().toString(16).slice(2, 10);
  const time = Date.now().toString(16).slice(-4);
  return `proj_${random}${time}`.slice(0, 17);
}

export function projectDirectoryName(name, projectId) {
  const base = safePathPart(name).toLowerCase() || "project";
  const id = safeProjectId(projectId) || createProjectId();
  return safePathPart(`${base}_${id}`).toLowerCase();
}

export function safeProjectId(value) {
  const text = String(value ?? "").trim();
  return /^[A-Za-z0-9_.-]{3,80}$/.test(text) ? text : "";
}

function safePathPart(value) {
  return String(value ?? "")
    .trim()
    .replace(/[^A-Za-z0-9_.-]+/g, "_")
    .replace(/^[._-]+|[._-]+$/g, "")
    .slice(0, 96);
}
