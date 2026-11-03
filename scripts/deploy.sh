#!/usr/bin/env bash
# Builds the plugin and installs it on a gamescope machine over SSH, without sudo:
# the ZIP is served on the machine's localhost and Decky's own installer is
# called through Steam's CEF debugger (127.0.0.1:8080, enabled by Decky).
# The confirmation Decky shows on screen is accepted automatically.
# Usage: ./scripts/deploy.sh user@host
set -euo pipefail
cd "$(dirname "$0")/.."
host=${1:?usage: deploy.sh user@host}
./scripts/package.sh
tmp=/tmp/depth3d-deploy
ssh "$host" "rm -rf $tmp && mkdir -p $tmp"
scp -q out/depth3d-glasses.zip scripts/cdp.py "$host:$tmp/"
ssh "$host" bash -s <<REMOTE
set -euo pipefail
cd $tmp
python3 -I -m http.server 8766 --bind 127.0.0.1 --directory $tmp >/dev/null 2>&1 &
server=\$!
trap 'kill \$server 2>/dev/null; rm -rf $tmp' EXIT
sleep 1
python3 -I cdp.py SharedJSContext 'DeckyBackend.call("utilities/install_plugin", "http://127.0.0.1:8766/depth3d-glasses.zip", "depth3d-glasses").then(() => "requested")'
for i in \$(seq 1 20); do
	sleep 0.5
	out=\$(python3 -I cdp.py "Steam Big Picture Mode" '(() => {
		for (const b of document.querySelectorAll("button, .DialogButton")) {
			let e = b;
			for (let i = 0; i < 8 && e; i++, e = e.parentElement)
				if (/install depth3d-glasses|depth3d-glasses dev/i.test(e.innerText || "") && !/cancel/i.test(b.innerText)) { b.click(); return "clicked " + b.innerText; }
		}
		return "waiting";
	})()')
	case \$out in *clicked*) echo "\$out"; break ;; esac
done
sleep 6
python3 -I cdp.py SharedJSContext 'JSON.stringify(DeckyPluginLoader.plugins.map(p => p.name + " " + p.version))'
REMOTE
