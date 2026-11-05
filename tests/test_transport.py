import contextlib
import io
import unittest
import urllib.error
from unittest.mock import patch
from commerce_adapter.contracts import Denied
from commerce_adapter.gateway import ProviderError
from commerce_adapter.paypal import NoRedirect, PayPalSandbox


class TransportTests(unittest.TestCase):
    def test_oauth_cache_and_renewal(self):
        now=[0]
        client=PayPalSandbox(credential_loader=lambda:("fixture-id","fixture-secret"),clock=lambda:now[0])
        with patch.object(client,"_wire",return_value=(200,{"token_type":"Bearer","access_token":"fixture-token","expires_in":300})) as wire:
            client.oauth(); client.oauth()
            self.assertEqual(wire.call_count,1)
            now[0]=241; client.oauth()
            self.assertEqual(wire.call_count,2)

    def test_401_invalidates_without_post_retry(self):
        client=PayPalSandbox(read_only=False,credential_loader=lambda:("fixture-id","fixture-secret"))
        client._token="fixture-token"; client._expires=1e20
        with patch.object(client,"_wire",side_effect=ProviderError(401,"HTTP_REJECTED")) as wire:
            with self.assertRaises(ProviderError): client.request("POST","/v2/checkout/orders",{},"12345678-1234-1234-1234-123456789abc")
            self.assertEqual(wire.call_count,1)
            self.assertIsNone(client._token)

    def test_read_only_disallows_post_before_credentials(self):
        client=PayPalSandbox()
        with patch.object(client,"loader") as loader:
            with self.assertRaises(Denied): client.request("POST","/v2/checkout/orders",{},"12345678-1234-1234-1234-123456789abc")
            loader.assert_not_called()

    def test_path_injection_disallowed(self):
        client=PayPalSandbox()
        for path in ("https://attacker.invalid/", "/v2/checkout/orders/../x", "/v2/checkout/orders/ABC?token=secret"):
            with self.assertRaises(Denied): client.request("GET",path)

    def test_redirect_never_forwards_auth(self):
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,"redirect",{},"https://attacker.invalid"))

    def test_http_errors_redacted(self):
        client=PayPalSandbox()
        for status in (401,403,500):
            with self.subTest(status=status):
                error=urllib.error.HTTPError("https://api-m.sandbox.paypal.com/",status,"sensitive-provider-error",{},io.BytesIO(b"sensitive-response"))
                with patch.object(client.opener,"open",side_effect=error):
                    with self.assertRaises(ProviderError) as caught:
                        client._wire("GET","/v2/checkout/orders/ABC",None,{"Authorization":"fixture-token"})
                self.assertEqual(caught.exception.status,status)
                self.assertNotIn("sensitive",str(caught.exception))

    def test_cli_preflight_missing_config_no_network(self):
        from commerce_adapter.__main__ import main
        with patch("sys.argv",["commerce_adapter","preflight"]), patch("commerce_adapter.__main__.runtime",side_effect=FileNotFoundError), patch.object(PayPalSandbox,"oauth") as oauth, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(),2)
            oauth.assert_not_called()
            self.assertIn("BLOCKED",output.getvalue())
