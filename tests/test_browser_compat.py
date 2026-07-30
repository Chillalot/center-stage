import asyncio
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import numpy as np

from center_stage.engine import OUTPUT_HEIGHT, OUTPUT_WIDTH, V4L2VirtualOutput

try:
    import websockets
except ImportError:
    websockets = None


BROWSER_ENABLED = os.environ.get("CENTER_STAGE_BROWSER_TESTS") == "1"
USE_RUNNING_CAMERA = os.environ.get("CENTER_STAGE_USE_RUNNING_CAMERA") == "1"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass


@unittest.skipUnless(BROWSER_ENABLED, "set CENTER_STAGE_BROWSER_TESTS=1")
class BrowserCompatibilityTests(unittest.TestCase):
    @staticmethod
    async def _read_result(websocket_url: str) -> dict:
        async with websockets.connect(websocket_url, max_size=2**20) as connection:
            request_id = 0

            async def evaluate(expression: str):
                nonlocal request_id
                request_id += 1
                expected = request_id
                await connection.send(json.dumps({
                    "id": expected,
                    "method": "Runtime.evaluate",
                    "params": {"expression": expression, "returnByValue": True},
                }))
                while True:
                    response = json.loads(await connection.recv())
                    if response.get("id") == expected:
                        return response["result"]["result"].get("value")

            deadline = asyncio.get_running_loop().time() + 18
            while asyncio.get_running_loop().time() < deadline:
                state = await evaluate("document.body && document.body.dataset.testState")
                if state in {"passed", "failed"}:
                    raw_result = await evaluate("document.getElementById('result').textContent")
                    return json.loads(raw_result)
                await asyncio.sleep(0.25)
            raise AssertionError("Chrome compatibility page did not finish within 18 seconds")

    def test_chrome_getusermedia_reads_90_frames(self):
        chrome = shutil.which("google-chrome") or shutil.which("chromium")
        if chrome is None:
            self.skipTest("Chrome/Chromium is not installed")
        if websockets is None:
            self.skipTest("development-only websockets module is not installed")

        frame = np.full((OUTPUT_HEIGHT, OUTPUT_WIDTH, 3), 40, dtype=np.uint8)
        output = None if USE_RUNNING_CAMERA else V4L2VirtualOutput("/dev/video10")
        stop = threading.Event()
        producer_thread = None

        def producer():
            position = 0
            while not stop.wait(1 / 30):
                frame[:, :, :] = 40
                frame[160:360, position : position + 200, 2] = 240
                position = (position + 24) % (OUTPUT_WIDTH - 200)
                output.send(frame)

        handler = lambda *args, **kwargs: QuietHandler(*args, directory=str(FIXTURES), **kwargs)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        try:
            if output is not None:
                output.start(frame)
                producer_thread = threading.Thread(target=producer, daemon=True)
                producer_thread.start()
            server_thread.start()
            with tempfile.TemporaryDirectory(prefix="center-stage-chrome-") as profile:
                page = f"http://127.0.0.1:{server.server_port}/getusermedia.html"
                with socket.socket() as port_socket:
                    port_socket.bind(("127.0.0.1", 0))
                    debugging_port = port_socket.getsockname()[1]
                process = subprocess.Popen(
                    [
                        chrome,
                        "--headless=new",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--disable-background-networking",
                        "--use-fake-ui-for-media-stream",
                        "--autoplay-policy=no-user-gesture-required",
                        "--remote-allow-origins=*",
                        f"--remote-debugging-port={debugging_port}",
                        f"--user-data-dir={profile}",
                        page,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                try:
                    deadline = time.monotonic() + 5
                    targets = []
                    while time.monotonic() < deadline:
                        try:
                            with urlopen(
                                f"http://127.0.0.1:{debugging_port}/json/list", timeout=0.5
                            ) as response:
                                targets = json.load(response)
                            if targets:
                                break
                        except OSError:
                            time.sleep(0.1)
                    self.assertTrue(targets, "Chrome DevTools did not become available")
                    target = next(item for item in targets if item.get("type") == "page")
                    result = asyncio.run(self._read_result(target["webSocketDebuggerUrl"]))
                finally:
                    process.terminate()
                    try:
                        _stdout, stderr = process.communicate(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        _stdout, stderr = process.communicate(timeout=3)
            self.assertTrue(result.get("passed"), result)
        finally:
            server.shutdown()
            server.server_close()
            stop.set()
            if producer_thread is not None:
                producer_thread.join(timeout=1)
            if output is not None:
                output.close()
