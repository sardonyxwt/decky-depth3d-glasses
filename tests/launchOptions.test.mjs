// Run: npx tsc src/launchOptions.ts src/ids.ts --outDir /tmp/lo --module es2020 --target es2020 --skipLibCheck --typeRoots ./none && node tests/launchOptions.test.mjs /tmp/lo
import assert from "node:assert/strict";
const dir = process.argv[2];
const { addLaunchWrapper, removeLaunchWrapper } = await import(`${dir}/launchOptions.js`);
const T = '"/home/deck/homebrew/data/depth3d-glasses/bin/depth3d-run" 3087367981';
assert.equal(addLaunchWrapper("", T), `${T} %command%`);
assert.equal(addLaunchWrapper("gamemoderun %command% -dx11", T), `gamemoderun ${T} %command% -dx11`);
assert.equal(addLaunchWrapper("-dx11 -nolauncher", T), `${T} %command% -dx11 -nolauncher`);
assert.equal(addLaunchWrapper(`${T} %command%`, T), `${T} %command%`);
assert.equal(
  addLaunchWrapper('STEAM_COMPAT_DATA_PATH="/mnt/x y/1" %command%', T),
  `STEAM_COMPAT_DATA_PATH="/mnt/x y/1" ${T} %command%`,
);
assert.equal(
  addLaunchWrapper('WINEDLLOVERRIDES="winmm=n,b" %command%', T),
  `WINEDLLOVERRIDES="winmm=n,b" ${T} %command%`,
);
assert.equal(removeLaunchWrapper(`${T} %command%`, T), "");
assert.equal(removeLaunchWrapper(`gamemoderun ${T} %command% -dx11`, T), "gamemoderun %command% -dx11");
assert.equal(removeLaunchWrapper(`${T} %command% -dx11`, T), "%command% -dx11");
const { toAppId } = await import(`${dir}/ids.js`);
assert.equal(toAppId("13587629224230912000"), 3163616458); // Stellar Blade shortcut game id
assert.equal(toAppId("3163616458"), 3163616458);
assert.equal(toAppId(1583230), 1583230);
assert.equal(toAppId(undefined), 0);
assert.equal(toAppId("not a number"), 0);
console.log("launchOptions: all ok");
