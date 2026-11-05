import concurrent.futures
import socket
import tempfile
import unittest
import urllib.error
import urllib.request
from commerce_adapter.approval import make_server
from tests.helpers import Fixture


class ApprovalTests(unittest.TestCase):
    def callback(self, approved, forged=False, host=None):
        with tempfile.TemporaryDirectory() as root:
            f=Fixture(root); eid=f.create()
            if approved: f.provider.approve()
            with f.gateway.connect() as db: row=f.gateway.row(db,eid)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1",0)); port=sock.getsockname()[1]
            with make_server(f.gateway,eid,port) as server, concurrent.futures.ThreadPoolExecutor(1) as pool:
                server.timeout=3
                future=pool.submit(server.handle_request)
                state="forged" if forged else row["callback_state"]
                request=urllib.request.Request(f"http://127.0.0.1:{port}/return?state={state}&token=ORDER1&PayerID=FAKE")
                if host: request.add_header("Host",host)
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                try:
                    with opener.open(request,timeout=3) as response: status=response.status; body=response.read()
                except urllib.error.HTTPError as error: status=error.code; body=error.read()
                future.result(timeout=4)
            return status,body,f.provider.capture_count()

    def test_forged_callback_no_capture(self):
        status,_,captures=self.callback(True,forged=True)
        self.assertEqual((status,captures),(403,0))

    def test_callback_does_not_replace_buyer_approval(self):
        status,body,captures=self.callback(False)
        self.assertEqual((status,captures),(200,0))
        self.assertIn(b"APPROVAL_PENDING",body)

    def test_approved_callback_captures_and_reads_back(self):
        status,body,captures=self.callback(True)
        self.assertEqual((status,captures),(200,1))
        self.assertIn(b"CAPTURE_CONFIRMED",body)
        self.assertIn(b'"mission_complete": false',body)

    def test_foreign_host_rejected(self):
        status,_,captures=self.callback(True,host="attacker.invalid")
        self.assertEqual((status,captures),(403,0))
