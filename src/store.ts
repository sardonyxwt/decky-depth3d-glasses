import { useEffect, useState } from "react";
import { DisplayInfo, GameMatch, getState, StateResult } from "./backend";
import { toAppId } from "./ids";

// Shared between the side panel and the badges on game pages.
let games = new Map<number, GameMatch>();
let lastState: StateResult | null = null;
let display: DisplayInfo | null = null;
const listeners = new Set<() => void>();

function notify() {
  listeners.forEach((l) => l());
}

export function setGames(list: GameMatch[]) {
  games = new Map(list.map((g) => [g.appid, g]));
  notify();
}

export function updateGame(appid: number, patch: Partial<GameMatch>) {
  const g = games.get(appid);
  if (!g) return;
  games.set(appid, { ...g, ...patch });
  notify();
}

export function setDisplay(d: DisplayInfo) {
  display = d;
  notify();
}

export async function refreshState(): Promise<StateResult> {
  const s = await getState();
  lastState = s;
  if (s.ok) {
    display = s.display;
    setGames(s.games);
  } else notify();
  return s;
}

export function getCachedState(): StateResult | null {
  return lastState;
}

export function useStoreVersion(): number {
  const [v, setV] = useState(0);
  useEffect(() => {
    const l = () => setV((x) => x + 1);
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  }, []);
  return v;
}

export function useGame(appid: number | undefined): GameMatch | undefined {
  useStoreVersion();
  return appid === undefined ? undefined : games.get(appid);
}

export function allGamesSnapshot(): GameMatch[] {
  return [...games.values()];
}

export function useAllGames(): GameMatch[] {
  useStoreVersion();
  return [...games.values()].sort((a, b) => a.name.localeCompare(b.name));
}

export function cachedGame(id: string | number | undefined | null): GameMatch | undefined {
  return games.get(toAppId(id));
}

export function cachedDisplay(): DisplayInfo | null {
  return display;
}

export function useDisplay(): DisplayInfo | null {
  useStoreVersion();
  return display;
}

/** What a start of this game will give right now. */
export type LaunchMode = "3d" | "2d-off" | "2d-screen" | "2d-restart" | "none";

/** Same rule as the launch wrapper (py_modules/depth3d/launch.py). */
export function launchMode(game: GameMatch | undefined, d: DisplayInfo | null): LaunchMode {
  if (!game?.installed) return "none";
  if (!game.enabled) return "2d-off";
  if (!d?.is_glasses) return "2d-screen";
  if (d.gamescope_sbs?.needed && !d.gamescope_sbs.ready) return "2d-restart";
  return "3d";
}

export const MODE_TEXT: Record<LaunchMode, [string, string]> = { // [english, russian]
  "3d": ["3D on", "3D включён"],
  "2d-off": ["3D off", "3D выключен"],
  "2d-screen": ["2D · monitor", "2D · монитор"],
  "2d-restart": ["2D · restart needed", "2D · нужен перезапуск"],
  none: ["", ""],
};

export const MODE_COLOR: Record<LaunchMode, string> = {
  "3d": "#1a9fff",
  "2d-off": "#8b929a",
  "2d-screen": "#8b929a",
  "2d-restart": "#e8a33d",
  none: "#8b929a",
};
