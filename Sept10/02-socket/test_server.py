"""Run with python3 test_server.py; requires gcc and openssl, no Python packages.

Compile a temporary copy using loopback/high ports and a temporary certificate.
The repository's port numbers, certificate files and sample files are unchanged.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import time
import unittest


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="cnlab-business-")
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        source_dir = Path(__file__).resolve().parent
        cls.html = (source_dir / "index.html").read_bytes()
        cls.binary = bytes(range(256)) * 32768
        (root / "index.html").write_bytes(cls.html)
        (root / "dir").mkdir()
        (root / "dir/index.html").write_bytes(cls.html)
        (root / "space name.txt").write_bytes(b"a file with spaces\n")
        (root / "binary.bin").write_bytes(cls.binary)
        (root / "empty.txt").write_bytes(b"")
        (root / "link").symlink_to("/etc/passwd")
        (root / "keys").mkdir()
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(root / "keys/cnlab.prikey"),
             "-out", str(root / "keys/cnlab.cert"),
             "-days", "1", "-subj", "/CN=localhost"],
            check=True, capture_output=True,
        )
        reservations = [socket.socket(), socket.socket()]
        try:
            for sock in reservations:
                sock.bind(("127.0.0.1", 0))
            cls.ports = [sock.getsockname()[1] for sock in reservations]
            cls.origin = f"https://127.0.0.1:{cls.ports[1]}"
            code = (source_dir / "https-server.c").read_text()
            replacements = {
                "make_listener(80)": f"make_listener({cls.ports[0]})",
                "make_listener(443)": f"make_listener({cls.ports[1]})",
                '#define HTTPS_ORIGIN "https://10.0.0.1"':
                    f'#define HTTPS_ORIGIN "{cls.origin}"',
                "addr.sin_addr.s_addr = htonl(INADDR_ANY);":
                    "addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);",
            }
            for old, new in replacements.items():
                assert old in code, f"Update test substitution: {old}"
                code = code.replace(old, new)
            (root / "server.c").write_text(code)
            subprocess.run(
                ["gcc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                 str(root / "server.c"), "-o", str(root / "server"),
                 "-lssl", "-lcrypto"], check=True,
            )
        finally:
            for sock in reservations:
                sock.close()
        cls.server = subprocess.Popen(
            [str(root / "server")], cwd=root,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        cls.addClassCleanup(cls.stop_server)
        cls.context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        cls.context.check_hostname = False
        cls.context.verify_mode = ssl.CERT_NONE
        for _ in range(100):
            if cls.server.poll() is not None:
                raise RuntimeError(cls.server.stderr.read().decode())
            try:
                with socket.create_connection(("127.0.0.1", cls.ports[0]), timeout=.1):
                    return
            except ConnectionRefusedError:
                time.sleep(.02)
        raise RuntimeError("server startup timeout")

    @classmethod
    def stop_server(cls):
        cls.server.terminate()
        cls.server.communicate(timeout=5)

    def connect(self, tls=True):
        sock = socket.create_connection(("127.0.0.1", self.ports[int(tls)]), timeout=5)
        if tls:
            sock = self.context.wrap_socket(sock, server_hostname="localhost")
        return sock

    def receive(self, sock):
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
        header, body = b"".join(chunks).split(b"\r\n\r\n", 1)
        lines = header.decode("ascii").split("\r\n")
        status = int(lines[0].split()[1])
        fields = dict(line.split(":", 1) for line in lines[1:])
        fields = {key.lower(): value.strip() for key, value in fields.items()}
        self.assertEqual(int(fields["content-length"]), len(body))
        self.assertEqual(fields["connection"], "close")
        return status, fields, body

    def request(self, target="/index.html", method="GET", headers=(), body=b"", tls=True):
        text = f"{method} {target} HTTP/1.1\r\nHost: localhost\r\n"
        text += "".join(f"{name}: {value}\r\n" for name, value in headers)
        with self.connect(tls) as sock:
            sock.sendall(text.encode() + b"\r\n" + body)
            return self.receive(sock)

    def test_http_all_methods_redirect(self):
        for method in ("GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS", "PATCH", "CUSTOM"):
            with self.subTest(method=method):
                status, fields, body = self.request(
                    "/dir/index.html?x=1", method, [("Content-Length", "4")], b"data", tls=False,
                )
                self.assertEqual(status, 301)
                self.assertEqual(fields["location"], self.origin + "/dir/index.html?x=1")
                self.assertEqual(body, b"")
        for target, expected in (("*", "/"), ("http://other/index.html?q=1", "/index.html?q=1"),
                                 ("http://other?q=1", "/?q=1")):
            with self.subTest(target=target):
                status, fields, _ = self.request(target, "OPTIONS", tls=False)
                self.assertEqual(status, 301)
                self.assertEqual(fields["location"], self.origin + expected)

    def test_full_files_and_queries(self):
        for target, expected in (("/", self.html), ("/index.html", self.html),
                                 ("/dir/index.html?cache=1", self.html),
                                 ("/space%20name.txt", b"a file with spaces\n"),
                                 ("/empty.txt", b""), ("/binary.bin", self.binary)):
            with self.subTest(target=target):
                status, fields, body = self.request(target)
                self.assertEqual(status, 200)
                self.assertEqual(body, expected)
                self.assertEqual(fields["accept-ranges"], "bytes")

    def test_single_ranges(self):
        size = len(self.binary)
        for value, first, last in (("bytes=100-200", 100, 200), ("bytes=100-", 100, size - 1),
                                   ("bytes=-100", size - 100, size - 1), ("bytes=0-0", 0, 0),
                                   (f"bytes={size - 3}-{size + 10}", size - 3, size - 1),
                                   (f"bytes=-{size + 1}", 0, size - 1)):
            with self.subTest(value=value):
                status, fields, body = self.request("/binary.bin", headers=[("rAnGe", "\t" + value + " \t")])
                self.assertEqual(status, 206)
                self.assertEqual(fields["content-range"], f"bytes {first}-{last}/{size}")
                self.assertEqual(body, self.binary[first:last + 1])

    def test_unsatisfiable_ranges(self):
        for target, size, value in (("/binary.bin", len(self.binary), f"bytes={len(self.binary)}-"),
                                    ("/binary.bin", len(self.binary), "bytes=-0"),
                                    ("/binary.bin", len(self.binary), "bytes=" + "9" * 100 + "-"),
                                    ("/empty.txt", 0, "bytes=0-")):
            with self.subTest(value=value):
                status, fields, body = self.request(target, headers=[("Range", value)])
                self.assertEqual(status, 416)
                self.assertEqual(fields["content-range"], f"bytes */{size}")
                self.assertEqual(body, b"")

    def test_ignored_ranges(self):
        for headers in ([('Range', 'bytes=200-100')], [('Range', 'bytes=abc')],
                        [('Range', 'items=0-1')], [('Range', 'bytes=0-1,3-4')],
                        [('Range', 'bytes=0-1'), ('Range', 'bytes=3-4')],
                        [('Range', 'bytes=0-1'), ('If-Range', '"unverified"')]):
            with self.subTest(headers=headers):
                status, fields, body = self.request(headers=headers)
                self.assertEqual(status, 200)
                self.assertNotIn('content-range', fields)
                self.assertEqual(body, self.html)

    def test_missing_or_outside_files(self):
        for target in ("/notfound.html", "/dir", "/dir/", "/../etc/passwd",
                       "/%2e%2e/etc/passwd", "/link", "/bad%00name", "/bad%zz"):
            with self.subTest(target=target):
                status, _, body = self.request(target)
                self.assertEqual(status, 404)
                self.assertEqual(body, b"")

    def test_non_get_https(self):
        status, fields, _ = self.request(method="POST")
        self.assertEqual(status, 405)
        self.assertEqual(fields["allow"], "GET")

    def test_malformed_headers(self):
        for request in (b"GET / HTTP/1.1\r\n\r\n", b"GET / HTTP/1.1\r\nHost: a\r\nHost: b\r\n\r\n",
                        b"GET / HTTP/1.1\r\nHost: a\r\nBad header: x\r\n\r\n"):
            with self.subTest(request=request), self.connect() as sock:
                sock.sendall(request)
                self.assertEqual(self.receive(sock)[0], 400)
        with self.connect() as sock:
            sock.sendall(b"GET / HTTP/1.1\r\nHost: a\r\nX: " + b"a" * 4100)
            self.assertEqual(self.receive(sock)[0], 431)

    def test_fragmented_headers_and_concurrency(self):
        held = []
        try:
            held.append(self.connect(False))
            held.append(socket.create_connection(("127.0.0.1", self.ports[1]), timeout=5))
            for tls in (False, True):
                sock = self.connect(tls)
                sock.sendall(b"GET /index.html HTTP/1.1\r\nHost: localhost\r\nRange: bytes=100-")
                held.append(sock)
            slow = self.connect()
            held.append(slow)
            slow.sendall(b"GET /binary.bin HTTP/1.1\r\nHost: localhost\r\n\r\n")

            def fetch(index):
                tls = bool(index % 2)
                status, _, body = self.request(tls=tls)
                self.assertEqual(status, 200 if tls else 301)
                self.assertEqual(body, self.html if tls else b"")

            with ThreadPoolExecutor(max_workers=24) as pool:
                list(pool.map(fetch, range(96)))
            for index, sock in enumerate(held[2:4]):
                sock.sendall(b"200\r\n\r\n")
                status, _, body = self.receive(sock)
                self.assertEqual(status, 301 if index == 0 else 206)
                self.assertEqual(body, b"" if index == 0 else self.html[100:201])
            self.assertEqual(self.receive(slow)[2], self.binary)
        finally:
            for sock in held:
                sock.close()
        self.assertIsNone(self.server.poll())


if __name__ == "__main__":
    unittest.main(verbosity=2)
