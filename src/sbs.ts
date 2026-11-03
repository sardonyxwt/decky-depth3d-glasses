// What Steam itself has to do for side-by-side 3D on full-SBS glasses (VITURE, XREAL, …):
//
//  * Output: while the glasses are in their 3D mode they expect a 3840-wide
//    signal. gamescope keeps the mode Steam saved for the display (often
//    1920x1080), so we switch it the same way Settings → Display → Resolution
//    does, and go back to the remembered 2D mode when the glasses return to 2D.
//  * Game: SuperDepth3D draws half-SBS, so the game has to render 16:9
//    (1920x1080) and gamescope has to stretch it over 3840 pixels. On a 3D
//    launch the game gets the chosen resolution override and the "Stretch"
//    scaling mode; when the game closes its own settings come back, so outside
//    a 3D session Steam always holds the user's settings (and an uninstalled
//    plugin leaves nothing behind).
//
// These are Steam client internals (SystemDisplayManagerStore, SystemPerfStore);
// every call is defensive and a failure only costs the automation, not the game.
import { findModuleExport } from "@decky/ui";
import { DisplayInfo, GameMatch, OutputMode, remember2dMode, setGameSteam, Settings, SteamRestore } from "./backend";
import { launchMode, updateGame } from "./store";

declare const SteamClient: any;

const SBS_WIDTH = 3840;
const SCALER_STRETCH = 5; // ESplitScalingScaler: 1 auto, 2 integer, 3 fit, 4 fill, 5 stretch

interface SteamMode extends OutputMode { id: number }
interface SteamDisplay {
  id: number;
  description: string;
  is_primary: boolean;
  is_enabled: boolean;
  current_mode_id: number;
  rgb_range?: number;
  modes: SteamMode[];
}

function log(...args: any[]) {
  console.log("[Depth3D Glasses]", ...args);
}

function displayStore(): any {
  const w = window as any;
  if (w.SystemDisplayManagerStore) return w.SystemDisplayManagerStore;
  try {
    return findModuleExport((e: any) => e?.prototype?.SetMode && e?.prototype?.ClearModeOverride && e?.Get)?.Get();
  } catch (_) {
    return null;
  }
}

function perfStore(): any {
  const w = window as any;
  if (w.SystemPerfStore) return w.SystemPerfStore;
  try {
    return findModuleExport((e: any) => e?.prototype?.SetSplitScalingScaler && e?.Get)?.Get();
  } catch (_) {
    return null;
  }
}

/** Steam creates these stores lazily; creating them early keeps their state fresh for us. */
export function initSteamStores() {
  displayStore();
  perfStore();
}

async function activeSteamDisplay(): Promise<SteamDisplay | null> {
  try {
    const state = await displayStore()?.GetState();
    const list: SteamDisplay[] = state?.displays ?? [];
    return list.find((d) => d.is_primary && d.is_enabled) ?? list.find((d) => d.is_enabled) ?? null;
  } catch (e) {
    log("display state failed", e);
    return null;
  }
}

function sameMode(a: OutputMode, b: OutputMode) {
  return a.width === b.width && a.height === b.height && a.refresh_hz === b.refresh_hz;
}

/** Highest refresh rate first; 1080 lines before 1200 (glasses such as the Luma offer both). */
function best(modes: SteamMode[], width: number): SteamMode | null {
  const list = modes.filter((m) => m.width === width);
  list.sort((a, b) => Number(b.height === 1080) - Number(a.height === 1080) || b.refresh_hz - a.refresh_hz);
  return list[0] ?? null;
}

function modeText(m: OutputMode) {
  return `${m.width}x${m.height}@${m.refresh_hz}`;
}

const SETTLE_MS = 1500; // let gamescope finish its own mode pick after the glasses re-connect
let syncing = false;
let pending: [DisplayInfo | null, Settings | undefined] | null = null;
let lastSbs: boolean | null = null;
const remembered: Record<string, OutputMode> = {};

/**
 * Called whenever the active display changes. While the glasses are in 3D the
 * output goes to their SBS mode; when they are back in 2D it returns to the 2D
 * mode Steam used before (remembered while the glasses sat in 2D), or else to
 * their best 1920x1080 mode.
 */
export async function syncOutputMode(display: DisplayInfo | null, settings: Settings | undefined) {
  if (!display?.is_glasses || settings?.auto_sbs_output === false) {
    if (display && !display.is_glasses) lastSbs = null;
    return;
  }
  if (syncing) {
    pending = [display, settings]; // handled right after the current pass
    return;
  }
  syncing = true;
  try {
    await new Promise((r) => setTimeout(r, SETTLE_MS));
    const d = await activeSteamDisplay();
    if (!d) return;
    const current = d.modes.find((m) => m.id === d.current_mode_id) ?? null;
    const set = async (target: SteamMode, why: string) => {
      if (current && sameMode(current, target)) return;
      log(`${why}: output ${modeText(target)} (was ${current ? modeText(current) : "?"})`);
      await displayStore().SetMode(d.id, target.id, d.rgb_range ?? 0);
    };
    const name = display.name;
    if (display.sbs) {
      lastSbs = true;
      const target = best(d.modes, SBS_WIDTH);
      if (target) await set(target, "glasses in 3D");
      return;
    }
    const wasSbs = lastSbs === true;
    lastSbs = false;
    const want = remembered[name] ?? settings?.glasses_2d_modes?.[name];
    if (!current) return; // a mode Steam does not list, e.g. the gamescope-generated SBS one
    if (wasSbs || current.width === SBS_WIDTH) {
      // Back from 3D: gamescope fell back to some mode of its own, put the 2D one back.
      const target = (want && d.modes.find((m) => sameMode(m, want))) || best(d.modes, 1920);
      if (target) await set(target, "glasses back in 2D");
    } else if (!want || !sameMode(current, want)) {
      remembered[name] = { width: current.width, height: current.height, refresh_hz: current.refresh_hz };
      await remember2dMode(name, remembered[name]);
    }
  } catch (e) {
    log("output mode switch failed", e);
  } finally {
    syncing = false;
    if (pending) {
      const [nd, ns] = pending;
      pending = null;
      syncOutputMode(nd, ns);
    }
  }
}

// ---------------- per-game settings around a launch ----------------

/** Before the game starts: the resolution override has to be in place when Steam launches it. */
export async function beforeLaunch(game: GameMatch, display: DisplayInfo | null) {
  const mode = launchMode(game, display);
  if (mode === "none") return;
  try {
    if (mode === "3d") {
      const want = game.sbs_resolution || "1920x1080";
      const prev: string = await SteamClient.Apps.GetResolutionOverrideForApp(game.appid);
      const saved: SteamRestore = { ...(game.steam ?? {}) };
      if (saved.resolution === undefined && prev !== want) saved.resolution = prev || "Default";
      if (prev !== want) SteamClient.Apps.SetAppResolutionOverride(game.appid, want);
      await saveRestore(game, saved);
      log(`${game.name}: 3D launch, game resolution ${want} (was ${prev})`);
    } else {
      await restoreSteamSettings(game); // normally already done when the last 3D session ended
    }
  } catch (e) {
    log("resolution override failed", e);
  }
}

/** Once the game runs: Steam's performance profile only applies to the running game. */
export async function afterLaunch(game: GameMatch, display: DisplayInfo | null) {
  const mode = launchMode(game, display);
  if (mode !== "3d") return;
  const store = perfStore();
  if (!store?.CreateSettingsUpdateRequest || !SteamClient?.System?.Perf?.UpdateSettings) return;
  const gameId = await waitForCurrentGame(store);
  if (!gameId) return;
  const update = (fill: (perApp: any) => void) => {
    const req = store.CreateSettingsUpdateRequest(gameId);
    fill(req.settings_delta().per_app(true));
    SteamClient.System.Perf.UpdateSettings(req.serializeBase64String());
  };
  try {
    const state = store.msgState ?? {};
    const hadProfile = String(state.active_profile_game_id) === String(gameId);
    const scaler: number | undefined = state.settings?.per_app?.split_scaling_scaler;
    if (mode === "3d") {
      if (scaler === SCALER_STRETCH && hadProfile) return;
      const saved: SteamRestore = { ...(game.steam ?? {}) };
      if (saved.profile_enabled === undefined) {
        saved.profile_enabled = hadProfile;
        saved.scaler = hadProfile ? scaler : undefined;
        saved.game_id = gameId;
      }
      if (!hadProfile) update((p) => p.set_is_game_perf_profile_enabled(true));
      update((p) => p.set_split_scaling_scaler(SCALER_STRETCH));
      await saveRestore(game, saved);
      log(`${game.name}: scaling set to Stretch`);
    }
  } catch (e) {
    log("scaling mode change failed", e);
  }
}

/** Puts back the game's own resolution and scaling (when a 3D session ends). */
export async function restoreSteamSettings(game: GameMatch) {
  if (!game.steam) return;
  restoreSteamSettingsSync(game);
  await saveRestore(game, {});
}

/**
 * The Steam calls only, synchronous, so they also run while the plugin is being
 * unloaded (uninstall, update): Steam never keeps a setting the plugin made.
 */
export function restoreSteamSettingsSync(game: GameMatch) {
  const s = game.steam;
  if (!s) return;
  try {
    if (s.resolution !== undefined) {
      SteamClient.Apps.SetAppResolutionOverride(game.appid, s.resolution);
    }
    const store = perfStore();
    if (s.profile_enabled !== undefined && s.game_id && store?.CreateSettingsUpdateRequest) {
      const req = store.CreateSettingsUpdateRequest(s.game_id);
      const perApp = req.settings_delta().per_app(true);
      if (!s.profile_enabled) perApp.set_is_game_perf_profile_enabled(false);
      else if (s.scaler !== undefined) perApp.set_split_scaling_scaler(s.scaler);
      SteamClient.System.Perf.UpdateSettings(req.serializeBase64String());
    }
    log(`${game.name}: Steam settings restored (resolution ${s.resolution ?? "unchanged"})`);
  } catch (e) {
    log("restoring Steam settings failed", e);
  }
}

async function waitForCurrentGame(store: any): Promise<string | null> {
  for (let i = 0; i < 40; i++) {
    const id = store.msgState?.current_game_id;
    if (id && String(id) !== "0" && String(id) !== "769") return String(id); // 769: Steam itself
    await new Promise((r) => setTimeout(r, 250));
  }
  return null;
}

async function saveRestore(game: GameMatch, data: SteamRestore) {
  const empty = Object.values(data).every((v) => v === undefined);
  const value = empty ? null : data;
  updateGame(game.appid, { steam: value });
  await setGameSteam(game.appid, value);
}
