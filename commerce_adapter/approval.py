import hmac
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
from .contracts import Denied, require


def make_server(gateway, eid, port):
    require(1024 <= port <= 65535, "CALLBACK_PORT")
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # No query tokens, payer identifiers or session state in logs.

        def do_GET(self):
            if self.headers.get("Host") != f"127.0.0.1:{port}":
                self.send_error(403)
                return
            parsed = urlparse(self.path)
            if parsed.path == "/cancel":
                payload = {"status": "CANCELLED", "payment_complete": False, "reservation_retained": True}
            elif parsed.path == "/return":
                with gateway.connect() as db:
                    row = gateway.row(db, eid)
                q = parse_qs(parsed.query)
                valid = (len(q.get("state", [])) == len(q.get("token", [])) == 1
                         and hmac.compare_digest(q["state"][0].encode(), row["callback_state"].encode())
                         and q["token"][0] == row["order_id"])
                if not valid:
                    self.send_error(403)
                    return
                try:
                    # URL PayerID and return navigation are not consent evidence.
                    # capture() first requires authenticated GET APPROVED plus payer.
                    state = gateway.capture(eid)
                    payload = {"payment_state": state, "mission_complete": False}
                except Denied as error:
                    payload = {"status": "BLOCKED", "code": str(error), "mission_complete": False}
            else:
                self.send_error(404)
                return
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    return HTTPServer(("127.0.0.1", port), Handler)


def serve(gateway, eid, port):
    with make_server(gateway, eid, port) as server:
        print(json.dumps({"approval_listener": "READY", "port": port}))
        server.serve_forever()
