import { DialogButton, DropdownItem, Focusable, ModalRoot, showModal, ToggleField } from "@decky/ui";
import { toaster } from "@decky/api";
import { useState } from "react";
import { FaGlasses } from "react-icons/fa";
import {
  DLL_TEXT, GameMatch, getGame, installGame, ReshadeDll, SBS_RESOLUTION_TEXT, SbsResolution, setGame3d,
  setSbsResolution, STATUS_COLOR, STATUS_TEXT, uninstallGame,
} from "./backend";
import { applyLaunchWrapper, removeLaunchWrapperFromApp } from "./steam";
import { launchMode, MODE_COLOR, MODE_TEXT, updateGame, useDisplay, useGame } from "./store";

const WARN: Partial<Record<GameMatch["status"], string>> = {
  network:
    "Overwatch помечает игру как сетевую. ReShade с аддонами в онлайне может задеть античит — " +
    "используй только в одиночном режиме.",
  not_compatible: "Автор Depth3D отметил игру как несовместимую: буфер глубины не работает.",
  no_profile: "Игра известна Overwatch, но готового профиля нет — придётся настраивать вручную.",
};

const DLL_OPTIONS: ReshadeDll[] = ["dxgi", "d3d9", "opengl32"];

function GameModalContent({ appid, closeModal }: { appid: number; closeModal?: () => void }) {
  const game = useGame(appid);
  const display = useDisplay();
  const [dll, setDll] = useState<ReshadeDll | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  if (!game) {
    return (
      <ModalRoot closeModal={closeModal}>
        <div>Игры нет в результатах сканирования. Нажми «Обновить список игр» в панели плагина.</div>
      </ModalRoot>
    );
  }

  const chosen = dll ?? (game.dll === "vulkan" ? "dxgi" : game.dll);
  const mode = launchMode(game, display);

  const doInstall = async () => {
    setBusy(true);
    setMessage("Скачиваю ReShade / Depth3D (первый раз может занять минуту)…");
    try {
      const r = await installGame(appid, chosen);
      if (!r.ok) {
        setMessage(`Ошибка: ${r.error}`);
        return;
      }
      updateGame(appid, { installed: true, enabled: true, dll: r.dll });
      const lo = await applyLaunchWrapper(appid, r.launch_token);
      if (lo.applied) {
        setMessage(`Готово. Параметры запуска: ${lo.value}`);
        toaster.toast({ title: "Depth3D установлен", body: game.name });
      } else {
        setMessage(`Установлено, но параметры запуска не выставлены (${lo.reason}). Впиши вручную:\n${lo.manual}`);
      }
    } catch (e) {
      setMessage(`Ошибка: ${String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const doUninstall = async () => {
    setBusy(true);
    try {
      const r = await uninstallGame(appid);
      if (!r.ok) {
        setMessage(`Ошибка: ${r.error}`);
        return;
      }
      updateGame(appid, { installed: false });
      const cleaned = await removeLaunchWrapperFromApp(appid, r.launch_token);
      setMessage(cleaned
        ? "Удалено, параметры запуска очищены."
        : `Удалено. Убери из параметров запуска вручную: ${r.launch_token}`);
    } catch (e) {
      setMessage(`Ошибка: ${String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const doToggle = async (enabled: boolean) => {
    const r = await setGame3d(appid, enabled);
    if (!r.ok) {
      setMessage(`Ошибка: ${r.error}`);
      return;
    }
    updateGame(appid, { enabled: r.enabled });
    // Re-apply the wrapper in case the launch options were edited by hand.
    const g = await getGame(appid);
    if (g.ok && g.launch_token) await applyLaunchWrapper(appid, g.launch_token);
  };

  const modeHint = (() => {
    if (!game.installed) return null;
    if (mode === "3d") {
      return `Игра запустится в 3D на очках ${display?.name}: вывод 3840×1080, ` +
        `игра ${game.sbs_resolution || "1920x1080"} «Растянуть». ` +
        "Включи 3D-режим на самих очках (VITURE Luma: удерживай R1) — до запуска или сразу после.";
    }
    if (mode === "2d-restart") {
      return "Для 3D на этих очках gamescope должен подхватить режим 3840×1080: " +
        "перезапусти игровой режим кнопкой в панели плагина. Пока игра запустится в 2D.";
    }
    if (mode === "2d-screen") {
      return `Сейчас активен монитор (${display?.name || "не определён"}) — игра запустится в обычном 2D, ` +
        "без ReShade. 3D включится само, когда картинка пойдёт на очки.";
    }
    return "Игра запустится в обычном 2D, без ReShade — как будто плагина нет. Применится со следующего запуска.";
  })();

  return (
    <ModalRoot closeModal={closeModal}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
        <FaGlasses size={28} color={game.installed ? MODE_COLOR[mode] : STATUS_COLOR[game.status]} />
        <div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>{game.name}</div>
          <div style={{ color: STATUS_COLOR[game.status] }}>
            {STATUS_TEXT[game.status]}
            {game.kind === "shortcut" ? " · сторонняя игра" : ""}
            {game.installed ? ` · ${MODE_TEXT[mode]}` : ""}
          </div>
        </div>
      </div>

      <div style={{ fontSize: 13, lineHeight: 1.5, color: "#b8bcbf" }}>
        <div>Профиль Overwatch: {game.profile}</div>
        <div>Исполняемый файл: {game.exe_rel} ({game.is_64bit ? "64" : "32"}-bit, {game.app_hash})</div>
        {WARN[game.status] && <div style={{ color: STATUS_COLOR[game.status], marginTop: 6 }}>{WARN[game.status]}</div>}
        {game.dll === "vulkan" && (
          <div style={{ color: "#d94b4b", marginTop: 6 }}>
            Игра на Vulkan: ReShade под Proton с Vulkan не работает. Если в игре есть режим DX11/DX12 —
            переключи его и выбери API ниже.
          </div>
        )}
      </div>

      {game.installed && (
        <ToggleField
          label="3D в этой игре"
          description={modeHint ?? undefined}
          checked={game.enabled}
          disabled={busy}
          onChange={doToggle}
        />
      )}

      {game.installed && game.enabled && (
        <DropdownItem
          label="Разрешение в 3D"
          description="Чем больше, тем чётче картинка и текст в очках, но выше нагрузка на видеокарту"
          rgOptions={(Object.keys(SBS_RESOLUTION_TEXT) as SbsResolution[]).map((r) => ({
            data: r, label: SBS_RESOLUTION_TEXT[r],
          }))}
          selectedOption={game.sbs_resolution || "1920x1080"}
          onChange={async (o) => {
            const r = await setSbsResolution(appid, o.data as SbsResolution);
            if (r.ok) updateGame(appid, { sbs_resolution: r.sbs_resolution });
            else setMessage(`Ошибка: ${r.error}`);
          }}
        />
      )}

      {!game.installed && (
        <DropdownItem
          label="Графический API"
          description="Определён по импортам exe; поменяй, если ReShade не появляется"
          rgOptions={DLL_OPTIONS.map((d) => ({ data: d, label: DLL_TEXT[d] }))}
          selectedOption={chosen}
          onChange={(o) => setDll(o.data as ReshadeDll)}
        />
      )}

      {message && (
        <div style={{ whiteSpace: "pre-wrap", margin: "10px 0", fontSize: 13 }}>{message}</div>
      )}

      <Focusable style={{ display: "flex", gap: 8, marginTop: 12 }} flow-children="horizontal">
        {game.installed ? (
          <DialogButton disabled={busy} onClick={doUninstall}>Удалить ReShade из игры</DialogButton>
        ) : (
          <DialogButton disabled={busy || game.status === "not_compatible"} onClick={doInstall}>
            Установить 3D
          </DialogButton>
        )}
        <DialogButton onClick={closeModal}>Закрыть</DialogButton>
      </Focusable>
    </ModalRoot>
  );
}

export function openGameModal(appid: number) {
  showModal(<GameModalContent appid={appid} />);
}
