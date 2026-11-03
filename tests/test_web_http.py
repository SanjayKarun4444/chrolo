import base64
import http.client
import json
import ssl
import socket
import threading
from pathlib import Path
from types import SimpleNamespace

import chrolo.web as web
from chrolo.web import _create_server

FIXTURES = Path(__file__).parent / "fixtures"


def test_trace_desk_http_auth_headers_and_payload_free_inspection():
    password = "local-test-password-123"
    server, _ = _create_server("127.0.0.1", 0, password)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    auth = "Basic " + base64.b64encode(f"chrolo:{password}".encode()).decode("ascii")

    def request(method, path, body=None, *, authorized=False):
        connection = http.client.HTTPConnection(host, port, timeout=5)
        headers = {}
        if authorized:
            headers["Authorization"] = auth
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = response.status, response.getheaders(), response.read()
        connection.close()
        return result

    try:
        status, headers, _ = request("GET", "/")
        assert status == 401
        assert ("WWW-Authenticate", 'Basic realm="Chrolo Trace Desk"') in headers

        status, headers, page = request("GET", "/", authorized=True)
        assert status == 200
        assert ("Cache-Control", "no-store") in headers
        assert any(name == "Content-Security-Policy" and "object-src 'none'" in value for name, value in headers)
        assert b"Chrolo Trace Desk" in page

        private = "do not expose this prompt"
        payload = json.dumps({
            "size_bytes": 900,
            "trace": {
                "format": "chrolo.langgraph.v1",
                "initial_state": {"token": "secret-token"},
                "events": [{"id": "e1", "kind": "message", "payload": {"text": private}}],
            },
        })
        status, _, summary = request("POST", "/api/inspect", payload, authorized=True)
        assert status == 200
        assert b"event_count" in summary
        assert private.encode() not in summary
        assert b"secret-token" not in summary

        scan_value = "Bearer should-never-be-returned-123456"
        scan_payload = json.dumps({
            "trace": {"initial_state": {"authorization": scan_value}},
        })
        status, _, scan_result = request("POST", "/api/scan", scan_payload, authorized=True)
        assert status == 200
        assert b"/initial_state/authorization" in scan_result
        assert b"bearer-token" in scan_result
        assert scan_value.encode() not in scan_result
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_trace_desk_https_serves_authenticated_requests():
    certfile = FIXTURES / "trace_desk_test_cert.pem"
    keyfile = FIXTURES / "trace_desk_test_key.pem"
    password = "local-test-password-456"
    server, _ = _create_server(
        "127.0.0.1", 0, password, str(certfile), str(keyfile)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    context = ssl.create_default_context(cafile=str(certfile))
    connection = http.client.HTTPSConnection(host, port, timeout=5, context=context)
    auth = "Basic " + base64.b64encode(f"chrolo:{password}".encode()).decode("ascii")
    try:
        connection.request("GET", "/", headers={"Authorization": auth})
        response = connection.getresponse()
        page = response.read()
        assert response.status == 200
        assert b"Chrolo Trace Desk" in page
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_trace_desk_tls_requires_certificate_and_key_pair():
    certfile = FIXTURES / "trace_desk_test_cert.pem"
    try:
        _create_server("127.0.0.1", 0, tls_certfile=str(certfile))
    except ValueError as exc:
        assert "both a certificate file and a private key file" in str(exc)
    else:
        raise AssertionError("TLS accepted an incomplete certificate/key pair")


def test_lan_address_discovery_filters_loopback_and_public_addresses(monkeypatch):
    monkeypatch.setattr(web.socket, "gethostname", lambda: "trace-desk-host")
    monkeypatch.setattr(
        web.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.25", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.25", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.1.20", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0)),
        ],
    )

    assert web._lan_ipv4_addresses() == ["192.168.1.25"]


def test_remote_startup_prints_private_url_and_fixed_basic_auth_username(monkeypatch, capsys):
    class StubServer:
        server_address = ("0.0.0.0", 9443)

        def serve_forever(self):
            pass

        def server_close(self):
            pass

    monkeypatch.setattr(web, "_create_server", lambda *args, **kwargs: (StubServer(), "long-test-password"))
    monkeypatch.setattr(web, "_lan_ipv4_addresses", lambda: ["192.168.1.25"])

    web.serve("0.0.0.0", 9443, tls_certfile="cert.pem", tls_keyfile="key.pem")

    output = capsys.readouterr().out
    assert "https://192.168.1.25:9443/" in output
    assert "Basic auth username: chrolo" in output
    assert "Basic auth password: long-test-password" in output
    assert "TLS is enabled" in output
