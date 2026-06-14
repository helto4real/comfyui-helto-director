export const SCHEMA_VERSION = "1.0";
export const VIDEO_TIMELINE_TYPE = "VIDEO_TIMELINE";

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

export const DEFAULT_DURATION_SECONDS = 5.0;
export const DEFAULT_FRAME_RATE = 24.0;
export const DEFAULT_ASPECT_RATIO = "16:9";
export const DEFAULT_ORIENTATION = "Landscape";
export const DEFAULT_QUALITY_PRESET = "Standard";
export const DEFAULT_ZOOM_LEVEL = 1.0;

export function createDefaultVideoTimeline() {
  return {
    schema_version: SCHEMA_VERSION,
    type: VIDEO_TIMELINE_TYPE,
    project: {
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
        always_normalize: false,
        normalization_mode: "Integrated LUFS",
        target_lufs: -16.0,
        true_peak_limit_db: -1.0,
        default_volume: 100.0,
        default_fade_in_seconds: 0.0,
        default_fade_out_seconds: 0.0,
      },
      privacy: {
        mode: false,
        hide_media_previews: false,
        hide_text_prompts: false,
        encrypt_previews: false,
      },
      display: {
        show_section_labels: true,
        show_thumbnails: true,
        show_audio_waveforms: true,
      },
    },
    ui_state: {
      timeline_display_mode: "Sections",
      section_edit_mode: "Trim Neighbor",
      snap_mode: "Frames",
      zoom_level: DEFAULT_ZOOM_LEVEL,
      scroll_x: 0.0,
      selected_item_id: null,
      state_revision: 0,
    },
    assets: [],
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
