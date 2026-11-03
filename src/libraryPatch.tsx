import {
  afterPatch, appDetailsClasses, createReactTreePatcher, findInReactTree, Focusable,
} from "@decky/ui";
import { routerHook } from "@decky/api";
import { useState } from "react";
import { FaGlasses } from "react-icons/fa";
import { STATUS_COLOR, STATUS_TEXT } from "./backend";
import { openGameModal } from "./GameModal";
import { launchMode, MODE_COLOR, MODE_TEXT, useDisplay, useGame } from "./store";

const ROUTE = "/library/app/:appid";

// Steam's own palette: frosted dark glass at rest, light fill with dark text in focus
// (like its buttons), the status only as a small dot.
const TEXT = "#dcdedf";
const TEXT_FOCUS = "#0e141b";

function GlassesBadge({ appid }: { appid: number }) {
  const game = useGame(appid);
  const display = useDisplay();
  const [focused, setFocused] = useState(false);
  if (!game) return null;
  const mode = launchMode(game, display);
  const dot = game.installed ? MODE_COLOR[mode] : STATUS_COLOR[game.status];
  const fg = focused ? TEXT_FOCUS : TEXT;
  return (
    <div style={{ position: "absolute", top: 52, right: 28, zIndex: 7 }}>
      <Focusable
        noFocusRing
        onActivate={() => openGameModal(appid)}
        onClick={() => openGameModal(appid)}
        onGamepadFocus={() => setFocused(true)}
        onGamepadBlur={() => setFocused(false)}
        onMouseEnter={() => setFocused(true)}
        onMouseLeave={() => setFocused(false)}
        style={{
          display: "flex", alignItems: "center", gap: 10, height: 40, padding: "0 16px 0 14px",
          borderRadius: 8, cursor: "pointer", userSelect: "none",
          color: fg, fontSize: 16, fontWeight: 500, letterSpacing: "0.01em", whiteSpace: "nowrap",
          background: focused ? "linear-gradient(to right, #ffffff, #dcdedf)" : "rgba(14, 20, 27, 0.55)",
          border: `1px solid ${focused ? "transparent" : "rgba(255, 255, 255, 0.08)"}`,
          boxShadow: focused ? "0 4px 14px rgba(0, 0, 0, 0.45)" : "0 2px 10px rgba(0, 0, 0, 0.3)",
          backdropFilter: "blur(12px) saturate(140%)",
          transition: "background 120ms ease, color 120ms ease, box-shadow 120ms ease",
        }}
      >
        <FaGlasses size={20} color={fg} style={{ opacity: focused ? 1 : 0.9 }} />
        <span>{game.installed ? MODE_TEXT[mode] : STATUS_TEXT[game.status]}</span>
        <span
          style={{
            width: 9, height: 9, borderRadius: "50%", background: dot, flex: "none", marginLeft: 2,
            boxShadow: focused ? "none" : `0 0 6px ${dot}`,
          }}
        />
      </Focusable>
    </div>
  );
}

/**
 * Inserts the badge into the game page, the same way ProtonDB Badges does.
 * Steam UI internals change between client updates; if the tree shape stops
 * matching, the patch simply does nothing and the side panel still works.
 */
export function patchLibraryApp() {
  return routerHook.addPatch(ROUTE, (tree: any) => {
    try {
      const routeProps = findInReactTree(tree, (x: any) => x?.renderFunc);
      if (!routeProps) return tree;
      let appid: number | undefined;
      const handler = createReactTreePatcher(
        [
          (t: any) => {
            const node = findInReactTree(t, (x: any) => x?.props?.children?.props?.overview);
            appid = node?.props?.children?.props?.overview?.appid;
            return node?.props?.children;
          },
        ],
        (_: any, ret: any) => {
          if (appid === undefined) return ret;
          const container = findInReactTree(
            ret,
            (x: any) =>
              Array.isArray(x?.props?.children) &&
              typeof x?.props?.className === "string" &&
              x.props.className.includes(appDetailsClasses.InnerContainer),
          );
          if (typeof container !== "object" || !container) return ret;
          const already = container.props.children.some((c: any) => c?.key === "depth3d-glasses-badge");
          if (!already) {
            container.props.children.splice(1, 0, <GlassesBadge key="depth3d-glasses-badge" appid={appid} />);
          }
          return ret;
        },
        "depth3d-glasses",
      );
      afterPatch(routeProps, "renderFunc", handler);
    } catch (e) {
      console.error("[Depth3D Glasses] library patch failed", e);
    }
    return tree;
  });
}

export function unpatchLibraryApp(patch: any) {
  routerHook.removePatch(ROUTE, patch);
}
