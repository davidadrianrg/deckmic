#!/usr/bin/env python3
"""Driver CDP mínimo sobre websocket (RFC6455 cliente) para verificar la webapp
de DeckMic contra el Chrome headless en 127.0.0.1:9222 — sin dependencias."""
import base64, json, socket, struct, sys, time, urllib.request


class MiniWS:
    def __init__(self, host, port, path):
        self.sock = socket.create_connection((host, port), timeout=30)
        key = base64.b64encode(bytes(12)).decode()
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        assert b"101" in buf.split(b"\r\n")[0], buf[:100]
        self._leftover = buf.split(b"\r\n\r\n", 1)[1]

    def send(self, payload):
        data = json.dumps(payload).encode()
        header = bytearray([0x81])
        n = len(data)
        mask = b"\x00\x00\x00\x00"
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126); header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127); header += struct.pack(">Q", n)
        # máscara cero: XOR neutro, payload legible y RFC-compliant
        self.sock.sendall(bytes(header) + mask + data)

    def recv_raw(self):
        if len(self._leftover) >= 2:
            b1, b2 = self._leftover[0:1], self._leftover[1:2]
            self._leftover = self._leftover[2:]
        else:
            b1, b2 = self.sock.recv(1), self.sock.recv(1)
        op = b1[0] & 0x0F
        ln = b2[0] & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", self.sock.recv(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", self.sock.recv(8))[0]
        data = self._leftover
        self._leftover = b""
        while len(data) < ln:
            chunk = self.sock.recv(ln - len(data))
            if not chunk:
                raise ConnectionError("eof")
            data += chunk
        return op, data

    def wait_json(self, method=None, id_=None, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            op, data = self.recv_raw()
            if op != 0x1:
                continue
            try:
                m = json.loads(data)
            except Exception:
                continue
            if method and m.get("method") == method:
                return m
            if id_ is not None and m.get("id") == id_:
                return m
        raise TimeoutError(method or id_)

    def eval(self, expr):
        self.send({"id": self._id, "method": "Runtime.evaluate",
                   "params": {"expression": expr, "returnByValue": True, "awaitPromise": True}})
        r = self.wait_json(id_=self._id)
        self._id += 1
        res = r.get("result", {}).get("result", {})
        if res.get("subtype") == "error":
            return ("ERR", res.get("description", "")[:300])
        return ("OK", res.get("value"))
    _id = 1


def main():
    pin = sys.argv[1] if len(sys.argv) > 1 else "543696"
    with urllib.request.urlopen("http://127.0.0.1:9222/json/list") as r:
        targets = json.load(r)
    page = next(t for t in targets if t["type"] == "page")
    ws_url = page["webSocketDebuggerUrl"]
    hostport = ws_url.split("/")[2]
    host, port = hostport.split(":")[0], int(hostport.split(":")[1])
    path = ws_url[ws_url.index("/devtools"):]
    c = MiniWS(host, port, path)
    c.send({"id": 0, "method": "Page.enable"})
    c.wait_json(id_=0)

    print("== 1. navegar y conectar ==")
    c.send({"id": 100, "method": "Page.navigate", "params": {"url": "http://127.0.0.1:8799/?auto=0"}})
    c.wait_json(id_=100)
    time.sleep(2)
    print("title:", c.eval("document.title")[1])
    print("app oculta tras cargar:", c.eval("!document.getElementById('screen-app') || document.getElementById('screen-app').hidden")[1])

    print("== 2. meter PIN y conectar ==")
    print(c.eval(f"document.getElementById('pin').value = '{pin}'; document.getElementById('btn-connect').click(); 'clicked'")[1])
    time.sleep(2)
    print("app visible:", c.eval("!document.getElementById('screen-app').hidden")[1])
    print("server-info:", c.eval("document.getElementById('server-info').textContent")[1])
    print("pin en LS:", c.eval("localStorage.getItem('deckmic-pin')")[1])

    print("== 3. ajustes: marcar space, desmarcar keepawake, guardar ==")
    c.eval("document.getElementById('btn-config').click()")
    time.sleep(0.3)
    print("space antes:", c.eval("document.getElementById('cfg-space-key').checked")[1])
    c.eval("document.getElementById('cfg-space-key').checked = true")
    c.eval("document.getElementById('cfg-keep-awake').checked = false")
    c.eval("document.getElementById('btn-cfg-save').click()")
    time.sleep(0.5)
    print("LS tras guardar:", c.eval("JSON.stringify({s: localStorage.getItem('deckmic-space'), k: localStorage.getItem('deckmic-keepawake')})")[1])

    print("== 4. recargar y verificar restauración ==")
    c.send({"id": 200, "method": "Page.navigate", "params": {"url": "http://127.0.0.1:8799/?auto=0"}})
    c.wait_json(id_=200)
    time.sleep(2)
    print("tras recargar:", c.eval("JSON.stringify({spaceChk: document.getElementById('cfg-space-key').checked, keepChk: document.getElementById('cfg-keep-awake').checked})")[1])
    print("wakeLock restaurado:", c.eval("(async()=>{try{return String(typeof wakeLock!=='undefined' && !!wakeLock && wakeLock.released===false)}catch(e){return 'err:'+e}})()")[1])
    print("cachés SW:", c.eval("(async()=>{if(!('caches' in window))return 'no api';return JSON.stringify(await caches.keys())})()")[1])

    print("== 5. error de micro visible ==")
    print(c.eval("(async()=>{try{await navigator.mediaDevices.getUserMedia({audio:true});return 'mic OK (inesperado)'}catch(e){return 'fallo: '+e.name}})()")[1])


if __name__ == "__main__":
    main()
