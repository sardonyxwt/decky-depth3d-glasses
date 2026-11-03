"""Tiny Chrome DevTools client for Steam's CEF (stdlib only, run on the Steam machine).

    cdp.py <target title> <js expression>          evaluate, print the JSON result
    cdp.py --screenshot <target title> <out.png>   save a PNG of that window
"""
import base64, json, os, socket, struct, sys, urllib.request

def targets():
    with urllib.request.urlopen("http://127.0.0.1:8080/json", timeout=5) as r:
        return json.load(r)

def ws_connect(url):
    rest = url.split("://", 1)[1]
    hostport, path = rest.split("/", 1)
    host, port = hostport.split(":")
    s = socket.create_connection((host, int(port)), timeout=30)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += s.recv(4096)
    assert b" 101 " in buf.split(b"\r\n")[0], buf[:200]
    return s

def send(s, text):
    data = text.encode()
    hdr = bytearray([0x81])
    n = len(data)
    if n < 126:
        hdr.append(0x80 | n)
    elif n < 65536:
        hdr.append(0x80 | 126); hdr += struct.pack(">H", n)
    else:
        hdr.append(0x80 | 127); hdr += struct.pack(">Q", n)
    mask = os.urandom(4)
    s.sendall(bytes(hdr) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

def recv_exact(s, n):
    out = b""
    while len(out) < n:
        chunk = s.recv(n - len(out))
        if not chunk:
            raise EOFError
        out += chunk
    return out

def recv(s):
    msg = b""
    while True:
        b0, b1 = recv_exact(s, 2)
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", recv_exact(s, 2))[0]
        elif n == 127:
            n = struct.unpack(">Q", recv_exact(s, 8))[0]
        msg += recv_exact(s, n)
        if b0 & 0x80:
            return msg.decode("utf-8", errors="replace")

def call(title, method, params):
    t = next(t for t in targets() if t["title"] == title)
    s = ws_connect(t["webSocketDebuggerUrl"])
    send(s, json.dumps({"id": 1, "method": method, "params": params}))
    while True:
        m = json.loads(recv(s))
        if m.get("id") == 1:
            return m.get("result")

def evaluate(title, expr):
    return call(title, "Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True})

if __name__ == "__main__":
    if sys.argv[1] == "--screenshot":
        res = call(sys.argv[2], "Page.captureScreenshot", {"format": "png"})
        with open(sys.argv[3], "wb") as f:
            f.write(base64.b64decode(res["data"]))
        print(sys.argv[3])
    else:
        print(json.dumps(evaluate(sys.argv[1], sys.argv[2]), ensure_ascii=False))
