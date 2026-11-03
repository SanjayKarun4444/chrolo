"""Small local/mobile-friendly trace inspection and sanitization UI."""
from __future__ import annotations

import base64
import hmac
import ipaddress
import json
import secrets
import socket
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import urlsplit

from .safety import filter_trace, redact_trace, scan_trace, summarize_trace

MAX_UPLOAD_BYTES = 11 * 1024 * 1024
_CGNAT_IPV4 = ipaddress.ip_network("100.64.0.0/10")
_PRIVATE_IPV4 = tuple(map(ipaddress.ip_network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><title>Chrolo Trace Desk</title>
<style>
:root{font:16px/1.5 system-ui,sans-serif;color-scheme:light dark;--card:Canvas;--line:color-mix(in srgb,CanvasText 18%,transparent);--tap-size:44px}
*{box-sizing:border-box}body{margin:0;background:color-mix(in srgb,Canvas 94%,#6484d8);color:CanvasText}
main{max-width:760px;margin:auto;padding:24px 16px 48px}h1{font-size:1.7rem;margin:.2rem 0}.sub{opacity:.75;margin:0 0 22px}
section{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin:14px 0;box-shadow:0 3px 16px #0000000b}
label{font-weight:650;display:block;margin:12px 0 5px}input,textarea,button{font:inherit;width:100%;min-height:var(--tap-size);border-radius:9px;border:1px solid var(--line);padding:11px;background:Canvas;color:CanvasText}
textarea{min-height:84px;resize:vertical}input[type=file]{padding:9px}button{background:#4868c7;color:white;border:0;font-weight:700;margin-top:12px;cursor:pointer}button.secondary{background:color-mix(in srgb,CanvasText 12%,Canvas);color:CanvasText}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}.note{font-size:.88rem;opacity:.8}.status{white-space:pre-wrap;overflow-wrap:anywhere;margin-top:12px}.result{display:none}.badge{display:inline-block;border:1px solid var(--line);border-radius:99px;padding:2px 10px;margin:3px;font-size:.85rem}.finding{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:8px 0}.finding span{overflow-wrap:anywhere}.finding button{width:auto;margin:0}
button:focus-visible,input:focus-visible,textarea:focus-visible{outline:3px solid #8ca7ff;outline-offset:2px}
@media(max-width:560px){main{padding:calc(18px + env(safe-area-inset-top)) calc(12px + env(safe-area-inset-right)) calc(32px + env(safe-area-inset-bottom)) calc(12px + env(safe-area-inset-left))}section{padding:15px}.row{grid-template-columns:1fr}.finding button{min-height:var(--tap-size);width:100%}}
</style></head><body><main>
<h1>Chrolo Trace Desk</h1><p class="sub">Inspect and sanitize a trace on this device. Trace contents are not saved by the server.</p>
<section><label for="trace">Choose a JSON trace</label><input id="trace" type="file" accept="application/json,.json">
<p class="note">Maximum file size: 10 MB. Inspection shows metadata only; it does not display prompts, state values, or tool results.</p>
<button id="inspect">Inspect trace</button><button id="scanSecrets" class="secondary">Scan for likely secrets</button>
<button id="clearData" class="secondary">Clear data from this page</button>
<div id="summary" class="result"></div><div id="scanResult" class="result"></div><div id="status" class="status" role="status"></div></section>
<section><h2>Make a sanitized copy</h2><p class="note">Enter one absolute JSON Pointer per line. Use * to match object keys or list entries. Any changed trace is marked non-replayable.</p>
<label for="redact">Redact values</label><textarea id="redact" placeholder="/initial_state/api_token&#10;/interactions/interactions/*/request/authorization"></textarea>
<button id="redactButton">Redact and download</button>
<label for="include">Include fields (optional)</label><textarea id="include" placeholder="/format&#10;/events"></textarea>
<label for="exclude">Exclude fields (optional)</label><textarea id="exclude" placeholder="/observations"></textarea>
<button id="filterButton" class="secondary">Filter and download</button></section>
<p class="note">This tool never runs graph code or makes model/tool calls. For remote access, bind only on a trusted private network and use the generated password.</p>
</main><script>
let trace=null,pageGeneration=0;const file=document.getElementById('trace'),status=document.getElementById('status'),summary=document.getElementById('summary'),scanResult=document.getElementById('scanResult');
function paths(id){return document.getElementById(id).value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean)}
function showError(e){status.textContent=e.message||String(e);summary.style.display='none'}
async function load(generation){if(!file.files.length)throw Error('Choose a trace file first.');const f=file.files[0];if(f.size>10485760)throw Error('Trace exceeds the 10 MB limit.');const parsed=JSON.parse(await f.text());if(generation!==pageGeneration)return null;if(!parsed||Array.isArray(parsed)||typeof parsed!=='object')throw Error('Trace must be a JSON object.');trace=parsed;return f}
async function post(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw Error(data.error||'Request failed.');return data}
file.onchange=()=>{pageGeneration++;trace=null;for(const panel of [summary,scanResult]){panel.replaceChildren();panel.style.display='none'}status.textContent=file.files.length?'New file selected. Choose an action to load it.':'Selected file removed.'};
document.getElementById('inspect').onclick=async()=>{const generation=pageGeneration;try{status.textContent='Inspecting…';const f=await load(generation);if(!f||generation!==pageGeneration)return;const data=await post('/api/inspect',{trace,size_bytes:f.size});if(generation!==pageGeneration)return;summary.innerHTML='';for(const [k,v] of Object.entries(data)){const chip=document.createElement('span');chip.className='badge';chip.textContent=k.replaceAll('_',' ')+': '+(v&&typeof v==='object'?JSON.stringify(v):v);summary.append(chip)}summary.style.display='block';status.textContent='Payload-free summary ready.'}catch(e){if(generation===pageGeneration)showError(e)}};
document.getElementById('clearData').onclick=()=>{pageGeneration++;trace=null;file.value='';for(const id of ['summary','scanResult']){const panel=document.getElementById(id);panel.replaceChildren();panel.style.display='none'}for(const id of ['redact','include','exclude'])document.getElementById(id).value='';status.textContent='Page data cleared.'};
document.getElementById('scanSecrets').onclick=async()=>{const generation=pageGeneration;try{status.textContent='Scanning for likely secrets…';const f=await load(generation);if(!f||generation!==pageGeneration)return;const data=await post('/api/scan',{trace});if(generation!==pageGeneration)return;scanResult.replaceChildren();for(const finding of data.findings){const item=document.createElement('div');item.className='finding';const label=document.createElement('span');label.textContent=finding.path+' · '+finding.reasons.join(', ');const add=document.createElement('button');add.className='secondary';add.textContent='Add to redaction list';add.onclick=()=>{const field=document.getElementById('redact'),selected=paths('redact');if(!selected.includes(finding.path))selected.push(finding.path);field.value=selected.join('\n');status.textContent='Added '+finding.path+' to the redaction list.'};item.append(label,add);scanResult.append(item)}if(!data.findings.length){const item=document.createElement('p');item.textContent='No likely secrets found.';scanResult.append(item)}const note=document.createElement('p');note.className='note';note.textContent=data.note;scanResult.append(note);scanResult.style.display='block';status.textContent='Scan complete. Values are never shown.'}catch(e){if(generation===pageGeneration)showError(e)}};
async function download(endpoint,body,name,generation){const data=await post(endpoint,body);if(generation!==pageGeneration)return;const blob=new Blob([JSON.stringify(data,null,2)+'\n'],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);status.textContent='Sanitized copy downloaded. It is marked non-replayable.'}
document.getElementById('redactButton').onclick=async()=>{const generation=pageGeneration;try{const f=await load(generation);if(!f||generation!==pageGeneration)return;const p=paths('redact');if(!p.length)throw Error('Enter at least one redaction path.');await download('/api/redact',{trace,paths:p},'trace.redacted.json',generation)}catch(e){if(generation===pageGeneration)showError(e)}};
document.getElementById('filterButton').onclick=async()=>{const generation=pageGeneration;try{const f=await load(generation);if(!f||generation!==pageGeneration)return;const include=paths('include'),exclude=paths('exclude');if(!include.length&&!exclude.length)throw Error('Enter at least one include or exclude path.');await download('/api/filter',{trace,include_paths:include,exclude_paths:exclude},'trace.filtered.json',generation)}catch(e){if(generation===pageGeneration)showError(e)}};
</script></body></html>"""


def handle_api(path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Pure request handler shared by HTTP routes and tests."""
    trace = payload.get("trace")
    if not isinstance(trace, Mapping):
        raise ValueError("trace must be a JSON object")
    if path == "/api/inspect":
        size = payload.get("size_bytes")
        if type(size) is not int or size < 0:
            raise ValueError("size_bytes must be a non-negative integer")
        return summarize_trace(trace, trace_size_bytes=size)
    if path == "/api/scan":
        return scan_trace(trace)
    if path == "/api/redact":
        paths = payload.get("paths")
        if not isinstance(paths, list):
            raise ValueError("paths must be an array")
        return redact_trace(trace, paths)
    if path == "/api/filter":
        include = payload.get("include_paths", [])
        exclude = payload.get("exclude_paths", [])
        if not isinstance(include, list) or not isinstance(exclude, list):
            raise ValueError("include_paths and exclude_paths must be arrays")
        return filter_trace(trace, include_paths=include, exclude_paths=exclude)
    raise ValueError("Unknown endpoint")


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _lan_ipv4_addresses() -> list[str]:
    """Return private and CGNAT VPN IPv4 addresses for this machine."""
    try:
        entries = socket.getaddrinfo(
            socket.gethostname(), None, family=socket.AF_INET, type=socket.SOCK_STREAM
        )
    except OSError:
        return []
    addresses = set()
    for entry in entries:
        address = ipaddress.ip_address(entry[4][0])
        if _is_private_or_vpn_ipv4(address):
            addresses.add(str(address))
    return sorted(addresses)


def _is_private_or_vpn_ipv4(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        address.version == 4
        and (address in _CGNAT_IPV4 or any(address in network for network in _PRIVATE_IPV4))
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_unspecified
        and not address.is_multicast
    )


def _validate_advertise_ip(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("advertise_ip must be a private or VPN IPv4 address")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise ValueError("advertise_ip must be a private or VPN IPv4 address") from None
    if not _is_private_or_vpn_ipv4(address):
        raise ValueError("advertise_ip must be a private or VPN IPv4 address")
    return str(address)


def _create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    password: str | None = None,
    tls_certfile: str | None = None,
    tls_keyfile: str | None = None,
) -> tuple[ThreadingHTTPServer, str | None]:
    """Create the trace desk server; non-loopback access requires auth."""
    if (tls_certfile is None) != (tls_keyfile is None):
        raise ValueError("TLS requires both a certificate file and a private key file")
    tls_context = None
    if tls_certfile is not None and tls_keyfile is not None:
        tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls_context.load_cert_chain(tls_certfile, tls_keyfile)

    if not _is_loopback(host) and password is None:
        password = secrets.token_urlsafe(18)
    if password is not None and len(password) < 12:
        raise ValueError("Web UI password must be at least 12 characters")

    auth_value = None
    if password is not None:
        token = base64.b64encode(f"chrolo:{password}".encode()).decode("ascii")
        auth_value = f"Basic {token}"

    class Handler(BaseHTTPRequestHandler):
        server_version = "ChroloTraceDesk/0.1"

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if auth_value is None:
                return True
            supplied = self.headers.get("Authorization", "")
            if hmac.compare_digest(supplied, auth_value):
                return True
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Chrolo Trace Desk"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return False

        def _host_is_allowed(self) -> bool:
            """Block DNS-rebinding hostnames from accessing an unauthenticated loopback server."""
            if not _is_loopback(self.server.server_address[0]):
                return True
            supplied = self.headers.get("Host", "")
            try:
                hostname = urlsplit("//" + supplied).hostname
            except ValueError:
                return False
            return hostname is not None and _is_loopback(hostname.rstrip("."))

        def _reject_invalid_host(self) -> bool:
            if self._host_is_allowed():
                return False
            self._send(421, b"Invalid Host header", "text/plain; charset=utf-8")
            return True

        def do_GET(self) -> None:
            if self._reject_invalid_host():
                return
            if not self._authorized():
                return
            if urlsplit(self.path).path != "/":
                self._send(404, b"Not found", "text/plain; charset=utf-8")
                return
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")

        def do_POST(self) -> None:
            if self._reject_invalid_host():
                return
            if not self._authorized():
                return
            if urlsplit(self.path).path not in {"/api/inspect", "/api/scan", "/api/redact", "/api/filter"}:
                self._send(404, b'{"error":"Not found"}', "application/json")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > MAX_UPLOAD_BYTES:
                    raise ValueError("Request must be between 1 byte and 11 MB")
                raw = self.rfile.read(length)
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, Mapping):
                    raise ValueError("Request body must be a JSON object")
                result = handle_api(urlsplit(self.path).path, payload)
                body = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self._send(400, body, "application/json; charset=utf-8")

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    if tls_context is not None:
        server.socket = tls_context.wrap_socket(server.socket, server_side=True)
    return server, password


def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    password: str | None = None,
    tls_certfile: str | None = None,
    tls_keyfile: str | None = None,
    advertise_ip: str | None = None,
) -> None:
    """Serve the trace desk; non-loopback access requires Basic authentication."""
    if advertise_ip is not None:
        advertise_ip = _validate_advertise_ip(advertise_ip)
    server, password = _create_server(host, port, password, tls_certfile, tls_keyfile)
    actual_host, actual_port = server.server_address[:2]
    scheme = "https" if tls_certfile is not None else "http"
    print(f"Chrolo Trace Desk: {scheme}://{actual_host}:{actual_port}/")
    if password:
        if not _is_loopback(host):
            if host in {"0.0.0.0", "::"}:
                addresses = _lan_ipv4_addresses()
                if advertise_ip is not None:
                    addresses = sorted(set(addresses) | {advertise_ip})
                if addresses:
                    print("On another device on this private network or VPN, open:")
                    for address in addresses:
                        print(f"  {scheme}://{address}:{actual_port}/")
                else:
                    print(f"On another device, use this computer's LAN IP with port {actual_port}.")
            print("Basic auth username: chrolo")
            print(f"Basic auth password: {password}")
            if tls_certfile is None:
                print("Warning: this connection has no TLS; use only on a trusted private network.")
            else:
                print("TLS is enabled. Ensure the certificate is trusted by each client device.")
    try:
        server.serve_forever()
    finally:
        server.server_close()
