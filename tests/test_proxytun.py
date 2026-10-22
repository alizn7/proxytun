import argparse
import importlib.machinery
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_loader = importlib.machinery.SourceFileLoader("proxytun", os.path.join(HERE, "..", "proxytun"))
_spec = importlib.util.spec_from_loader("proxytun", _loader)
pt = importlib.util.module_from_spec(_spec)
_loader.exec_module(pt)


def opts(**kw):
    base = {"type": None, "user": None, "password": None, "kill_switch": None, "name": None}
    base.update(kw)
    return argparse.Namespace(**base)


def recv_exact(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError
        buf += chunk
    return buf


class FakeSocks5(threading.Thread):
    """Minimal SOCKS5 server: accepts CONNECT without dialing out; UDP ASSOCIATE optional."""

    def __init__(self, udp=False, auth=None):
        super().__init__(daemon=True)
        self.udp, self.auth = udp, auth
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(8)
        self.port = self.srv.getsockname()[1]
        self.start()

    def run(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self.handle, args=(conn,), daemon=True).start()

    def handle(self, conn):
        try:
            with conn:
                _, n = recv_exact(conn, 2)
                recv_exact(conn, n)
                if self.auth:
                    conn.sendall(b"\x05\x02")
                    recv_exact(conn, 1)
                    user = recv_exact(conn, recv_exact(conn, 1)[0]).decode()
                    pwd = recv_exact(conn, recv_exact(conn, 1)[0]).decode()
                    ok = (user, pwd) == self.auth
                    conn.sendall(b"\x01" + (b"\x00" if ok else b"\x01"))
                    if not ok:
                        return
                else:
                    conn.sendall(b"\x05\x00")
                cmd = recv_exact(conn, 4)[1]
                recv_exact(conn, 6)
                if cmd == 1:
                    conn.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("127.0.0.1") + b"\x00\x00")
                elif cmd == 3 and self.udp:
                    self.relay(conn)
                else:
                    conn.sendall(b"\x05\x07\x00\x01" + b"\x00" * 6)
        except (ConnectionError, OSError):
            pass

    def relay(self, conn):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as relay:
            relay.bind(("127.0.0.1", 0))
            relay.settimeout(5)
            conn.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("0.0.0.0")
                         + relay.getsockname()[1].to_bytes(2, "big"))
            data, addr = relay.recvfrom(4096)
            query = data[10:]
            answer = query[:2] + b"\x81\x80" + query[4:]
            relay.sendto(data[:10] + answer, addr)

    def close(self):
        try:
            self.srv.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.srv.close()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TargetParsing(unittest.TestCase):
    def test_ip_only(self):
        self.assertEqual(pt.parse_target("192.168.1.5"), ("192.168.1.5", None))

    def test_ip_and_port(self):
        self.assertEqual(pt.parse_target("10.0.0.2:1090"), ("10.0.0.2", 1090))

    def test_rejects_hostname(self):
        with self.assertRaises(pt.ProxytunError):
            pt.parse_target("proxy.example.com")

    def test_rejects_bad_port(self):
        for bad in ("1.2.3.4:0", "1.2.3.4:70000", "1.2.3.4:x"):
            with self.assertRaises(pt.ProxytunError):
                pt.parse_target(bad)


class Profiles(unittest.TestCase):
    def test_defaults(self):
        prof = pt.make_profile("1.2.3.4", opts())
        self.assertEqual(prof["type"], "socks")
        self.assertEqual(prof["port"], 1080)
        self.assertFalse(prof["kill_switch"])

    def test_http_default_port(self):
        self.assertEqual(pt.make_profile("1.2.3.4", opts(type="http"))["port"], 8080)

    def test_new_ip_keeps_port(self):
        old = pt.make_profile("1.2.3.4:1090", opts())
        new = pt.make_profile("5.6.7.8", opts(), old)
        self.assertEqual((new["host"], new["port"]), ("5.6.7.8", 1090))

    def test_select_profile_by_name_and_address(self):
        store = pt.default_store()
        self.assertEqual(pt.select_profile(store, "1.2.3.4", opts(name="phone")), "phone")
        self.assertEqual(pt.select_profile(store, "phone", opts(kill_switch=True)), "phone")
        self.assertTrue(store["profiles"]["phone"]["kill_switch"])
        self.assertEqual(pt.select_profile(store, "9.9.9.9", opts()), "default")
        store["last"] = "phone"
        self.assertEqual(pt.select_profile(store, None, opts()), "phone")

    def test_unknown_target(self):
        with self.assertRaises(pt.ProxytunError):
            pt.select_profile(pt.default_store(), "phon", opts())


class Config(unittest.TestCase):
    prof = {"type": "socks", "host": "192.168.1.5", "port": 1080, "username": None, "password": None}

    def rules(self, cfg):
        return cfg["route"]["rules"]

    def test_udp_rejected_without_relay(self):
        cfg = pt.singbox_config(self.prof, udp=False)
        self.assertIn({"network": "udp", "action": "reject"}, self.rules(cfg))

    def test_udp_kept_with_relay(self):
        cfg = pt.singbox_config(self.prof, udp=True)
        self.assertNotIn({"network": "udp", "action": "reject"}, self.rules(cfg))

    def test_proxy_excluded_from_tun(self):
        tun = pt.singbox_config(self.prof, udp=False)["inbounds"][0]
        self.assertEqual(tun["route_exclude_address"], ["192.168.1.5/32"])

    def test_auth_and_http(self):
        prof = dict(self.prof, type="http", port=8080, username="u", password="p")
        out = pt.singbox_config(prof, udp=False)["outbounds"][0]
        self.assertEqual((out["type"], out["username"], out["password"]), ("http", "u", "p"))
        self.assertNotIn("version", out)


@unittest.skipUnless(shutil.which("sing-box"), "sing-box not installed")
class SingBoxAcceptsConfig(unittest.TestCase):
    def check(self, prof, udp):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(pt.singbox_config(prof, udp), f)
        try:
            res = subprocess.run(["sing-box", "check", "-c", f.name], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)
        finally:
            os.unlink(f.name)

    def test_socks(self):
        self.check({"type": "socks", "host": "192.168.1.5", "port": 1080}, udp=False)

    def test_socks_udp_auth(self):
        self.check({"type": "socks", "host": "10.0.0.1", "port": 1080, "username": "a", "password": "b"}, udp=True)

    def test_http(self):
        self.check({"type": "http", "host": "22.76.243.49", "port": 8080}, udp=False)


class Probes(unittest.TestCase):
    def prof(self, server, **kw):
        base = {"type": "socks", "host": "127.0.0.1", "port": server.port, "username": None, "password": None}
        base.update(kw)
        return base

    def test_healthy_proxy(self):
        srv = FakeSocks5()
        self.addCleanup(srv.close)
        self.assertIsNone(pt.check_proxy(self.prof(srv)))

    def test_unreachable(self):
        prof = {"type": "socks", "host": "127.0.0.1", "port": free_port()}
        self.assertIn("cannot reach", pt.check_proxy(prof, timeout=1))

    def test_udp_not_supported(self):
        srv = FakeSocks5(udp=False)
        self.addCleanup(srv.close)
        self.assertFalse(pt.probe_udp(self.prof(srv)))

    def test_udp_supported(self):
        srv = FakeSocks5(udp=True)
        self.addCleanup(srv.close)
        self.assertTrue(pt.probe_udp(self.prof(srv)))

    def test_auth(self):
        srv = FakeSocks5(auth=("ali", "secret"))
        self.addCleanup(srv.close)
        self.assertIsNone(pt.check_proxy(self.prof(srv, username="ali", password="secret")))
        self.assertIn("authentication failed", pt.check_proxy(self.prof(srv, username="ali", password="nope")))
        self.assertIn("requires", pt.check_proxy(self.prof(srv)) or "")


class DaemonStateMachine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        fake = os.path.join(self.tmp, "sing-box")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\nexec sleep 60\n")
        os.chmod(fake, 0o755)
        self.srv = FakeSocks5()
        self.store = pt.default_store()
        self.store["profiles"]["t"] = {"type": "socks", "host": "127.0.0.1", "port": self.srv.port,
                                       "username": None, "password": None, "kill_switch": False}
        patches = {
            "STATUS_FILE": os.path.join(self.tmp, "status.json"),
            "SINGBOX_CONF": os.path.join(self.tmp, "sing-box.json"),
            "FAIL_LIMIT": 2,
            "TIMEOUT": 1,
            "load_store": lambda: self.store,
            "save_store": lambda s: None,
            "find_singbox": lambda: fake,
        }
        for key, value in patches.items():
            original = getattr(pt, key)
            setattr(pt, key, value)
            self.addCleanup(setattr, pt, key, original)

    def status(self):
        with open(pt.STATUS_FILE) as f:
            return json.load(f)

    def test_fallback_and_recovery(self):
        d = pt.Daemon("t")
        self.addCleanup(d.stop_singbox)
        d.tick()
        self.assertEqual(self.status()["state"], "active")
        self.assertTrue(d.running())
        self.assertFalse(self.status()["udp"])

        self.srv.close()
        d.tick()
        self.assertEqual(self.status()["state"], "active")
        d.tick()
        self.assertEqual(self.status()["state"], "fallback")
        self.assertFalse(d.running())

        self.srv = FakeSocks5()
        self.store["profiles"]["t"]["port"] = self.srv.port
        d.prof["port"] = self.srv.port
        self.addCleanup(self.srv.close)
        d.tick()
        self.assertEqual(self.status()["state"], "active")
        self.assertTrue(d.running())

    def test_kill_switch_keeps_tunnel(self):
        self.store["profiles"]["t"]["kill_switch"] = True
        d = pt.Daemon("t")
        self.addCleanup(d.stop_singbox)
        d.tick()
        self.srv.close()
        d.tick()
        d.tick()
        self.assertEqual(self.status()["state"], "blocked")
        self.assertTrue(d.running())


if __name__ == "__main__":
    unittest.main()
