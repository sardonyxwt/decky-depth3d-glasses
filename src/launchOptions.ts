// Pure helpers for Steam launch options. No Steam globals here, so they can be unit tested.
//
// The plugin puts its launch wrapper right in front of %command%:
//   "<runtime>/bin/depth3d-run" <appid> %command%
// so the user's own variables and tools (gamemoderun, mangohud, …) keep working
// and the wrapper decides 3D or 2D on every start.

const COMMAND = "%command%";

function tidy(s: string): string {
  return s.replace(/\s+/g, " ").trim();
}

/** Adds the wrapper token to existing launch options. */
export function addLaunchWrapper(current: string, token: string): string {
  const cur = tidy(current || "");
  if (cur.includes(token)) return cur; // already applied
  if (!cur) return `${token} ${COMMAND}`;
  const i = cur.indexOf(COMMAND);
  if (i >= 0) return tidy(`${cur.slice(0, i)} ${token} ${cur.slice(i)}`);
  // Only game arguments, e.g. "-dx11 -nolauncher"
  return tidy(`${token} ${COMMAND} ${cur}`);
}

/** Removes exactly our token; drops a bare "%command%" that is left over. */
export function removeLaunchWrapper(current: string, token: string): string {
  const cur = tidy((current || "").split(token).join(" "));
  if (cur === COMMAND) return "";
  return cur;
}
