// Thin, defensive wrappers around Steam client globals available to Decky plugins.
import { addLaunchWrapper, removeLaunchWrapper } from "./launchOptions";

declare const SteamClient: any;
declare const appDetailsStore: any;
declare const appStore: any;
declare const collectionStore: any;

export const COLLECTION_NAME = "3D Glasses";

/**
 * Current launch options. appDetailsStore only has details of games whose page
 * was opened; for the rest (often non-Steam shortcuts) ask Steam for them.
 */
async function currentLaunchOptions(appid: number): Promise<string | null> {
  try {
    const details = appDetailsStore?.GetAppDetails?.(appid);
    if (details && typeof details.strLaunchOptions === "string") return details.strLaunchOptions;
  } catch (_) {
    /* ignore */
  }
  if (!SteamClient?.Apps?.RegisterForAppDetails) return null;
  return new Promise((resolve) => {
    let reg: any = null;
    const done = (v: string | null) => {
      clearTimeout(timer);
      try {
        reg?.unregister?.();
      } catch (_) {
        /* ignore */
      }
      resolve(v);
    };
    const timer = setTimeout(() => done(null), 3000);
    try {
      reg = SteamClient.Apps.RegisterForAppDetails(appid, (d: any) => {
        if (d && typeof d.strLaunchOptions === "string") done(d.strLaunchOptions);
      });
    } catch (_) {
      done(null);
    }
  });
}

// Launch options as last seen, so the plugin can take its wrapper out synchronously
// while it is being unloaded (there is no time to ask Steam then).
const knownOptions = new Map<number, string>();

function remember(appid: number, value: string) {
  knownOptions.set(appid, value);
}

export type LaunchApplyResult =
  | { applied: true; value: string }
  | { applied: false; reason: string; manual: string };

export async function applyLaunchWrapper(appid: number, token: string): Promise<LaunchApplyResult> {
  const manual = `${token} %command%`;
  const cur = await currentLaunchOptions(appid);
  if (cur === null) {
    return { applied: false, reason: "Could not read the current launch options", manual };
  }
  const next = addLaunchWrapper(cur, token);
  try {
    if (next !== cur) SteamClient.Apps.SetAppLaunchOptions(appid, next);
    remember(appid, next);
    return { applied: true, value: next };
  } catch (e) {
    return { applied: false, reason: String(e), manual };
  }
}

export async function removeLaunchWrapperFromApp(appid: number, token: string): Promise<boolean> {
  const cur = await currentLaunchOptions(appid);
  if (cur === null) return false;
  try {
    const next = removeLaunchWrapper(cur, token);
    SteamClient.Apps.SetAppLaunchOptions(appid, next);
    remember(appid, next);
    return true;
  } catch (_) {
    return false;
  }
}

/** Plugin loaded: every game with 3D installed gets the wrapper (back) in its launch options. */
export async function addWrapperToGames(games: { appid: number; launch_token?: string }[]) {
  for (const g of games) {
    if (g.launch_token) await applyLaunchWrapper(g.appid, g.launch_token);
  }
}

/**
 * Plugin unloaded (uninstall, update, disable): takes the wrapper out of every
 * game right away, so Steam never keeps a launch option that points into a
 * plugin that may be gone. It comes back when the plugin loads again.
 */
export function removeWrapperFromGamesSync(games: { appid: number; launch_token?: string }[]): number {
  let n = 0;
  for (const g of games) {
    if (!g.launch_token) continue;
    let cur: string | undefined;
    try {
      cur = appDetailsStore?.GetAppDetails?.(g.appid)?.strLaunchOptions;
    } catch (_) {
      /* ignore */
    }
    if (typeof cur !== "string") cur = knownOptions.get(g.appid);
    if (typeof cur !== "string" || !cur.includes(g.launch_token)) continue;
    try {
      SteamClient.Apps.SetAppLaunchOptions(g.appid, removeLaunchWrapper(cur, g.launch_token));
      n++;
    } catch (_) {
      /* ignore */
    }
  }
  return n;
}

/** Creates or refills a Steam collection with the given games. Returns how many were added. */
export async function syncCollection(appids: number[]): Promise<number> {
  if (typeof collectionStore === "undefined") throw new Error("collectionStore is not available in this Steam version");
  const overviews = appids
    .map((id) => appStore?.GetAppOverviewByAppID?.(id))
    .filter((o: any) => !!o);
  if (!overviews.length) return 0;

  const existing = (collectionStore.userCollections || []).find(
    (c: any) => c?.displayName === COLLECTION_NAME,
  );
  if (existing) {
    const dd = existing.AsDragDropCollection?.();
    if (!dd?.AddApps) throw new Error("The collection cannot be changed (is it a dynamic collection?)");
    dd.AddApps(overviews);
    await existing.Save?.();
  } else {
    const col = collectionStore.NewUnsavedCollection(COLLECTION_NAME, undefined, overviews);
    await col.Save();
  }
  return overviews.length;
}
