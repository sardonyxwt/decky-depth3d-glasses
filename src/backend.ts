import { callable } from "@decky/api";

export type ProfileStatus = "ok" | "network" | "not_compatible" | "no_profile";
export type ReshadeDll = "dxgi" | "d3d9" | "opengl32" | "vulkan";

export interface GameMatch {
  appid: number;
  name: string;
  exe: string;
  exe_rel: string;
  is_64bit: boolean;
  app_hash: string;
  profile: string;
  status: ProfileStatus;
  dll: ReshadeDll;
  installed: boolean;
  kind: "steam" | "shortcut";
  enabled: boolean; // 3D switched on for this game (only meaningful when installed)
  steam?: SteamRestore | null;
  sbs_resolution: SbsResolution; // what the game renders in 3D
  launch_token?: string;         // the wrapper in front of %command% (installed games only)
}

export type SbsResolution = "1920x1080" | "2560x1440" | "3200x1800" | "3840x2160";

export const SBS_RESOLUTION_TEXT: Record<SbsResolution, string> = {
  "1920x1080": "1920×1080 — 960 px на глаз, быстро",
  "2560x1440": "2560×1440 — 1280 px на глаз, ~1,8× нагрузки",
  "3200x1800": "3200×1800 — 1600 px на глаз, ~2,8× нагрузки",
  "3840x2160": "3840×2160 — 1920 px на глаз, ~4× нагрузки",
};

/** Steam settings of a game the plugin changed for a 3D launch, to put back for 2D. */
export interface SteamRestore {
  resolution?: string;       // previous resolution override, e.g. "Default"
  profile_enabled?: boolean; // the game had its own performance profile before
  scaler?: number;           // previous scaling mode (ESplitScalingScaler)
  game_id?: string;          // Steam game id of the performance profile we changed
}

export interface OutputMode { width: number; height: number; refresh_hz: number }

export interface DisplayInfo {
  connected: boolean;
  source: "gamescope" | "drm" | "drm-ambiguous" | "none";
  name: string;
  vendor: string;
  connector: string;
  is_glasses: boolean;
  sbs: boolean; // the EDID itself lists a 3840-wide side-by-side mode
  modes: string[];
  rates_1080: number[];
  others: string[];
  // Glasses without a 3840-wide EDID mode (VITURE): gamescope generates it from our script.
  gamescope_sbs?: { needed: boolean; ready: boolean };
}

export interface AssetSummary {
  reshade: string | null;
  shaders: boolean;
  shaders_updated: number | null;
  d3dcompiler64: boolean;
  d3dcompiler32: boolean;
  has_7z: boolean;
}

export interface Settings {
  reshade_addon: boolean;
  show_network_games: boolean;
  auto_sbs_output: boolean;
  glasses_names: string[];
  not_glasses_names: string[];
  glasses_2d_modes: Record<string, OutputMode>;
}

export type ToggleSetting = "reshade_addon" | "show_network_games" | "auto_sbs_output";

interface Fail { ok: false; error: string }

export type StateResult =
  | { ok: true; assets: AssetSummary; overwatch: string | null; profiles: number;
      scanned_at: number | null; games: GameMatch[]; settings: Settings; display: DisplayInfo }
  | Fail;

export type GameResult =
  | { ok: true; game: GameMatch | null; launch_token?: string }
  | Fail;

export type InstallResult =
  | { ok: true; launch_token: string; dll: ReshadeDll }
  | Fail;

export type DisplayResult = { ok: true; display: DisplayInfo } | Fail;

export const getState = callable<[], StateResult>("get_state");
export const scan = callable<[force: boolean], { ok: true; games: GameMatch[] } | Fail>("scan");
export const getGame = callable<[appid: number], GameResult>("get_game");
export const updateShaders = callable<[], { ok: true; assets: AssetSummary } | Fail>("update_shaders");
export const updateReshade = callable<[], { ok: true; reshade: string } | Fail>("update_reshade");
export const installGame = callable<[appid: number, dll: string], InstallResult>("install_game");
export const uninstallGame = callable<[appid: number], { ok: true; launch_token: string } | Fail>("uninstall_game");
export const setGame3d = callable<[appid: number, enabled: boolean], { ok: true; enabled: boolean } | Fail>("set_game_3d");
export const getDisplay = callable<[], DisplayResult>("get_display");
export const setSbsResolution = callable<[appid: number, resolution: SbsResolution], { ok: true; sbs_resolution: SbsResolution } | Fail>("set_sbs_resolution");
export const setGameSteam = callable<[appid: number, data: SteamRestore | null], { ok: true } | Fail>("set_game_steam");
export const sbsOutput = callable<[on: boolean], { ok: true } | Fail>("sbs_output");
export const restartSession = callable<[], { ok: true; unit: string } | Fail>("restart_session");
export const remember2dMode = callable<[name: string, mode: OutputMode], { ok: true } | Fail>("remember_2d_mode");
export const markDisplay = callable<[name: string, kind: "glasses" | "screen" | "auto"], DisplayResult>("mark_display");
export const setSetting = callable<[key: ToggleSetting, value: boolean], { ok: true; settings: Settings } | Fail>("set_setting");

export const STATUS_TEXT: Record<ProfileStatus, string> = {
  ok: "Профиль Depth3D есть",
  network: "Профиль есть · онлайн-игра",
  not_compatible: "Depth3D: не совместима",
  no_profile: "Известна, но без профиля",
};

export const STATUS_COLOR: Record<ProfileStatus, string> = {
  ok: "#59bf40",
  network: "#e8a33d",
  not_compatible: "#d94b4b",
  no_profile: "#8b929a",
};

export const DLL_TEXT: Record<ReshadeDll, string> = {
  dxgi: "DirectX 10/11/12 (dxgi.dll)",
  d3d9: "DirectX 9 (d3d9.dll)",
  opengl32: "OpenGL (opengl32.dll)",
  vulkan: "Vulkan — под Proton не поддерживается",
};
