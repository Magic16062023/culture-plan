"""WSGI entrypoint: run the existing HTTP handler in a Vercel Function."""

import io
import os
from http.client import HTTPResponse
from http import HTTPStatus

from server import CalendarHandler, init_db


class MemorySocket:
    def __init__(self, request):
        self.request = io.BytesIO(request)
        self.response = io.BytesIO()

    def makefile(self, mode, *args, **kwargs):
        return self.request if "r" in mode else self.response

    def sendall(self, data):
        self.response.write(data)


def app(environ, start_response):
    if not os.getenv("DATABASE_URL"):
        body = b"Persistent database is not configured"
        start_response("503 Service Unavailable", [("Content-Type", "text/plain"), ("Content-Length", str(len(body)))])
        return [body]

    # The schema is idempotent; each warm function process only initializes once.
    if not getattr(app, "initialized", False):
        init_db()
        app.initialized = True

    path = environ.get("PATH_INFO", "/")
    if environ.get("QUERY_STRING"):
        path += "?" + environ["QUERY_STRING"]
    body = environ["wsgi.input"].read(int(environ.get("CONTENT_LENGTH") or 0))
    headers = []
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            headers.append((key[5:].replace("_", "-").title(), value))
    if environ.get("CONTENT_TYPE"):
        headers.append(("Content-Type", environ["CONTENT_TYPE"]))
    headers.append(("Content-Length", str(len(body))))
    if not any(key.lower() == "host" for key, _ in headers):
        headers.append(("Host", environ.get("SERVER_NAME", "localhost")))
    raw_request = f"{environ['REQUEST_METHOD']} {path} HTTP/1.1\r\n".encode("ascii")
    raw_request += b"".join(f"{key}: {value}\r\n".encode("latin-1") for key, value in headers)
    socket = MemorySocket(raw_request + b"\r\n" + body)
    handler_server = type("Server", (), {"server_name": "localhost", "server_port": 443})()
    CalendarHandler(socket, (environ.get("REMOTE_ADDR", "127.0.0.1"), 0), handler_server)
    response_socket = MemorySocket(b"")
    response_socket.request = io.BytesIO(socket.response.getvalue())
    response = HTTPResponse(response_socket)
    response.begin()
    response_body = response.read()
    status = HTTPStatus(response.status)
    start_response(f"{status.value} {status.phrase}", [
        (name, value) for name, value in response.getheaders()
        if name.lower() not in {"connection", "transfer-encoding", "server", "date"}
    ])
    return [response_body]
