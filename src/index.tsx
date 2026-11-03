import {
  ButtonItem, Field, PanelSection, PanelSectionRow, ProgressBarWithInfo, staticClasses, ToggleField,
} from "@decky/ui";
import { addEventListener, definePlugin, removeEventListener, toaster } from "@decky/api";
import { useEffect, useState } from "react";
import { FaGlasses } from "react-icons/fa";
import {
  DisplayInfo, markDisplay, restartSession, sbsOutput, scan, setSetting, STATUS_COLOR, STATUS_TEXT, updateReshade,
  updateShaders,
} from "./backend";
import { openGameModal } from "./GameModal";
import { t, tr } from "./i18n";
import { patchLibraryApp, unpatchLibraryApp } from "./libraryPatch";
import {
  afterLaunch, beforeLaunch, initSteamStores, restoreSteamSettings, restoreSteamSettingsSync, syncOutputMode,
} from "./sbs";
import { addWrapperToGames, COLLECTION_NAME, removeWrapperFromGamesSync, syncCollection } from "./steam";
import {
  allGamesSnapshot, cachedDisplay, cachedGame, getCachedState, launchMode, MODE_COLOR, MODE_TEXT, refreshState, setDisplay,
  useAllGames, useDisplay, useStoreVersion,
} from "./store";

declare const SteamClient: any;

type Progress = { done: number; total: number; current: string } | null;
let progressListener: ((p: Progress) => void) | null = null;

// Rescan in the background when the panel opens and the last scan is older than this.
const AUTO_SCAN_AFTER_S = 10 * 60;
let scanning: Promise<unknown> | null = null;

/** Incremental scan: only new or changed games are read again, so it is cheap. */
function backgroundScan(): Promise<unknown> {
  if (!scanning) {
    scanning = scan(false)
      .then(() => refreshState())
      .catch(() => undefined)
      .finally(() => {
        scanning = null;
        progressListener?.(null);
      });
  }
  return scanning;
}

function displayText(d: DisplayInfo | null): { label: string; description: string } {
  if (!d || !d.connected) return { label: t("not found", "не найден"), description: t("No displays connected", "Нет подключённых экранов") };
  const where = d.connector ? ` (${d.connector})` : "";
  if (d.is_glasses) {
    const gs = d.gamescope_sbs;
    return {
      label: `${d.name}${where}`,
      description: gs?.needed && !gs.ready
        ? t("Glasses · 3D needs a Game Mode restart (once)", "Очки · для 3D нужен перезапуск игрового режима (один раз)")
        : t("Glasses · games with 3D on start in 3D. The glasses' 3D mode is switched with their button",
          "Очки · игры с включённым 3D запустятся в 3D. Режим 3D на очках включается кнопкой"),
    };
  }
  const unsure = d.source === "drm-ambiguous"
    ? t(" Several displays are connected and gamescope did not say which is active.", " Подключено несколько экранов, gamescope не подсказал активный.") : "";
  return { label: `${d.name}${where}`, description: t("Monitor · 2D only.", "Монитор · только 2D.") + unsure };
}

function fmtDate(ts: number | null | undefined) {
  if (!ts) return t("never", "никогда");
  return new Date(ts * 1000).toLocaleString();
}

function Content() {
  useStoreVersion();
  const games = useAllGames();
  const display = useDisplay();
  const state = getCachedState();
  const [busy, setBusy] = useState<string | null>(null);
  const [progress, setProgress] = useState<Progress>(null);

  useEffect(() => {
    progressListener = setProgress;
    refreshState().then((s) => {
      if (s.ok && (!s.scanned_at || Date.now() / 1000 - s.scanned_at > AUTO_SCAN_AFTER_S)) backgroundScan();
    });
    return () => {
      progressListener = null;
    };
  }, []);

  const run = async (label: string, fn: () => Promise<any>) => {
    setBusy(label);
    try {
      const r = await fn();
      if (r && r.ok === false) toaster.toast({ title: "Depth3D Glasses", body: r.error });
    } catch (e) {
      toaster.toast({ title: "Depth3D Glasses", body: String(e) });
    } finally {
      setBusy(null);
      setProgress(null);
      await refreshState();
    }
  };

  const ok = state && state.ok ? state : null;
  const settings = ok?.settings;
  const visible = games.filter((g) => settings?.show_network_games !== false || g.status !== "network");
  const usable = visible.filter((g) => g.status === "ok" || g.status === "network");
  const dt = displayText(display);
  const markAs = (kind: "glasses" | "screen" | "auto") =>
    run("display", async () => {
      if (!display?.name) return;
      const r = await markDisplay(display.name, kind);
      if (r.ok) setDisplay(r.display);
      return r;
    });
  const userMarked = !!display?.name && !!settings &&
    (settings.glasses_names.includes(display.name) || settings.not_glasses_names.includes(display.name));

  return (
    <>
      <PanelSection title={t("Display", "Экран")}>
        <PanelSectionRow>
          <Field label={dt.label} description={dt.description}>
            <FaGlasses color={display?.is_glasses ? "#1a9fff" : "#8b929a"} />
          </Field>
        </PanelSectionRow>
        {display?.connected && !userMarked && (
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={!!busy} onClick={() => markAs(display.is_glasses ? "screen" : "glasses")}>
              {display.is_glasses ? t("These are not glasses, it is a monitor", "Это не очки, а монитор") : t("These are my XR glasses", "Это мои XR-очки")}
            </ButtonItem>
          </PanelSectionRow>
        )}
        {display?.is_glasses && display.gamescope_sbs?.needed && !display.gamescope_sbs.ready && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={!!busy}
              description={t("gamescope picks up the 3840×1080 mode for the glasses. Steam restarts, close games first", "gamescope подхватит режим 3840×1080 для очков. Steam перезапустится, игры закрой заранее")}
              onClick={() => run("restart", restartSession)}
            >
              {t("Restart Game Mode", "Перезапустить игровой режим")}
            </ButtonItem>
          </PanelSectionRow>
        )}
        {userMarked && (
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={!!busy} onClick={() => markAs("auto")}>
              {t("Detect this display automatically", "Определять этот экран автоматически")}
            </ButtonItem>
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection title={t("Depth3D profiles", "Профили Depth3D")}>
        {state && !state.ok && (
          <PanelSectionRow><Field label={t("Error", "Ошибка")}>{state.error}</Field></PanelSectionRow>
        )}
        <PanelSectionRow>
          <Field label="Overwatch" description={ok ? t(`${ok.profiles} profiles`, `${ok.profiles} профилей`) : undefined}>
            {ok?.overwatch ?? t("not downloaded", "не скачан")}
          </Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <Field label="ReShade">{ok?.assets.reshade ?? t("downloaded on install", "скачается при установке")}</Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <Field label={t("Last scan", "Последнее сканирование")}>{fmtDate(ok?.scanned_at)}</Field>
        </PanelSectionRow>
        {progress && (
          <PanelSectionRow>
            <ProgressBarWithInfo
              nProgress={(progress.done / Math.max(progress.total, 1)) * 100}
              sOperationText={progress.current}
              description={`${progress.done}/${progress.total}`}
            />
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={!!busy}
            description={t("Steam and non-Steam games. Updates by itself when Steam starts", "Игры Steam и сторонние (non-Steam) игры. Само обновляется при запуске Steam")}
            onClick={() => run("scan", () => backgroundScan())}
          >
            {busy === "scan" ? t("Looking for games…", "Ищу игры…") : t("Update game list", "Обновить список игр")}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={!!busy}
            onClick={() => run("shaders", async () => {
              const r = await updateShaders();
              return r.ok ? scan(true) : r;
            })}
          >
            {busy === "shaders" ? t("Updating…", "Обновляю…") : t("Update profiles and rescan", "Обновить профили и пересканировать")}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={!!busy} onClick={() => run("reshade", updateReshade)}>
            {busy === "reshade" ? t("Checking…", "Проверяю…") : t("Check for a ReShade update", "Проверить обновление ReShade")}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title={t(`Games with a profile (${usable.length})`, `Игры с профилем (${usable.length})`)}>
        {visible.length === 0 && (
          <PanelSectionRow>
            <Field label={t("Nothing yet", "Пока пусто")}>{t("Run a scan", "Запусти сканирование")}</Field>
          </PanelSectionRow>
        )}
        {visible.map((g) => {
          const mode = launchMode(g, display);
          const desc = tr(g.installed ? MODE_TEXT[mode] : STATUS_TEXT[g.status]) +
            (g.kind === "shortcut" ? t(" · non-Steam", " · сторонняя") : "");
          return (
            <PanelSectionRow key={g.appid}>
              <ButtonItem layout="below" onClick={() => openGameModal(g.appid)} description={desc}>
                <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <FaGlasses color={g.installed ? MODE_COLOR[mode] : STATUS_COLOR[g.status]} />
                  {g.name}
                </span>
              </ButtonItem>
            </PanelSectionRow>
          );
        })}
        {usable.length > 0 && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={!!busy}
              onClick={() => run("collection", async () => {
                const n = await syncCollection(usable.map((g) => g.appid));
                toaster.toast({ title: "Depth3D Glasses", body: t(`Collection "${COLLECTION_NAME}": ${n} games`, `Коллекция «${COLLECTION_NAME}»: ${n} игр`) });
              })}
            >
              {t(`Build the "${COLLECTION_NAME}" collection`, `Собрать коллекцию «${COLLECTION_NAME}»`)}
            </ButtonItem>
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection title={t("Settings", "Настройки")}>
        <PanelSectionRow>
          <ToggleField
            label={t("Show online games", "Показывать онлайн-игры")}
            description={t("Games Overwatch marks as online (anti-cheat risk)", "Игры, которые Overwatch помечает как сетевые (риск античита)")}
            checked={settings?.show_network_games ?? true}
            onChange={(v) => run("setting", () => setSetting("show_network_games", v))}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label={t("3840×1080 output while the glasses are in 3D", "Вывод 3840×1080, когда очки в 3D")}
            description={t("Steam switches the display to the glasses' SBS mode and back in 2D", "Steam сам переключает разрешение экрана на SBS-режим очков и возвращает прежнее в 2D")}
            checked={settings?.auto_sbs_output ?? true}
            onChange={(v) => run("setting", () => setSetting("auto_sbs_output", v))}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label={t("ReShade with add-on support", "ReShade с поддержкой аддонов")}
            description={t("Needed for the Generic Depth mod and recent DX12 fixes. Single-player games only", "Нужно для Generic Depth Mod и свежих фиксов DX12. Только для одиночных игр")}
            checked={settings?.reshade_addon ?? true}
            onChange={(v) => run("setting", () => setSetting("reshade_addon", v))}
          />
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title={t("Glasses (SBS)", "Очки (SBS)")}>
        <PanelSectionRow>
          <div style={{ fontSize: 12, lineHeight: 1.5, color: "#b8bcbf" }}>
            {t("1. Switch the glasses to 3D with their button. Steam switches the output to 3840×1080.",
              "1. Переведи очки в 3D-режим кнопкой на очках. Steam сам переключит вывод на 3840×1080.")}<br />
            {t("2. Start a game with 3D on: the plugin sets it to 1920×1080 with \"Stretch\" scaling and restores it for 2D.",
              "2. Запусти игру с включённым 3D: плагин выставит ей 1920×1080 и масштабирование «Растянуть», а при запуске в 2D вернёт как было.")}<br />
            {t("3. On a monitor, or with the glasses in 2D, the game starts in plain 2D without ReShade.",
              "3. На мониторе или с очками в 2D игра запускается в обычном 2D без ReShade.")}<br />
            {t("4. The ReShade menu is the Home key. The profile loads by itself.",
              "4. Меню ReShade — клавиша Home. Профиль подхватится сам.")}
          </div>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}

export default definePlugin(() => {
  const libraryPatch = patchLibraryApp();
  const onProgress = addEventListener<[done: number, total: number, current: string]>(
    "depth3d_scan_progress",
    (done, total, current) => progressListener?.({ done, total, current }),
  );
  initSteamStores();
  const settingsNow = () => {
    const s = getCachedState();
    return s && s.ok ? s.settings : undefined;
  };
  const onDisplay = addEventListener<[display: DisplayInfo]>("depth3d_display", (d) => {
    setDisplay(d);
    syncOutputMode(d, settingsNow());
  });
  // Fill the badge cache at startup, then pick up new or changed games (also non-Steam ones),
  // so game pages show the icon without opening the panel.
  refreshState()
    .then(async (s) => {
      if (s.ok) await addWrapperToGames(s.games.filter((g) => g.installed));
      syncOutputMode(cachedDisplay(), settingsNow());
    })
    .catch(() => undefined)
    .finally(() => backgroundScan());

  const hooks: any[] = [];
  const running = new Set<number>();
  try {
    hooks.push(SteamClient.Apps.RegisterForGameActionStart((_id: number, appId: string, action: string) => {
      const g = cachedGame(appId);
      if (g?.installed && (!action || action === "LaunchApp")) beforeLaunch(g, cachedDisplay());
    }));
    hooks.push(SteamClient.GameSessions.RegisterForAppLifetimeNotifications((n: any) => {
      const g = cachedGame(n?.unAppID);
      if (!g?.installed) return;
      if (n.bRunning) running.add(g.appid);
      else running.delete(g.appid);
      if (n.bRunning) {
        afterLaunch(g, cachedDisplay());
      } else {
        sbsOutput(false); // the wrapper drops it too; this covers a killed wrapper
        restoreSteamSettings(g);
      }
    }));
  } catch (e) {
    console.error("[Depth3D Glasses] launch hooks failed", e);
  }

  return {
    name: "Depth3D Glasses",
    titleView: <div className={staticClasses.Title}>Depth3D Glasses</div>,
    content: <Content />,
    icon: <FaGlasses />,
    onDismount() {
      removeEventListener("depth3d_scan_progress", onProgress);
      removeEventListener("depth3d_display", onDisplay);
      const games = allGamesSnapshot();
      const removed = removeWrapperFromGamesSync(games);
      // Steam settings of a 3D session that has not been put back yet. The backend keeps
      // the record, so after an update they are set again on the next 3D start.
      games.filter((g) => g.steam && !running.has(g.appid)).forEach(restoreSteamSettingsSync);
      console.log(`[Depth3D Glasses] unloaded, wrapper removed from ${removed} game(s)`);
      hooks.forEach((h) => h?.unregister?.());
      unpatchLibraryApp(libraryPatch);
    },
  };
});
