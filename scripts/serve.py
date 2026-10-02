"""Static server with HTTP Range support (like GitHub Pages) for local development and e2e tests: python scripts/serve.py [port]"""
import os
import re
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class RangeHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        rng = self.headers.get("Range")
        path = self.translate_path(self.path)
        if not rng or not os.path.isfile(path):
            return super().send_head()
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        size = os.path.getsize(path)
        start = int(m.group(1)) if m.group(1) else max(0, size - int(m.group(2)))
        end = min(int(m.group(2)), size - 1) if m.group(1) and m.group(2) else size - 1
        f = open(path, "rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.range = (start, end)
        return f

    def copyfile(self, source, outputfile):
        if getattr(self, "range", None):
            start, end = self.range
            self.range = None
            outputfile.write(source.read(end - start + 1))
        else:
            super().copyfile(source, outputfile)

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        if os.environ.get("SERVE_LOG"):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    os.chdir(os.path.join(os.path.dirname(__file__), "..", "web"))
    ThreadingHTTPServer(("127.0.0.1", port), RangeHandler).serve_forever()
