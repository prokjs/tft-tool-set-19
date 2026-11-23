"""The Node preview publishes only generated assets, never calculation endpoints."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class PreviewServerTests(unittest.TestCase):
    def setUp(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('Node is required by the Rust browser frontend.')
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        (root / 'tools').mkdir()
        public = root / 'frontend-dist'
        (public / 'web').mkdir(parents=True)
        (public / 'web/module.js').write_bytes(b'export const value = 1;\n')
        (public / 'web/index.html').write_text('<h1>Local preview</h1>', encoding='utf-8')
        (root / 'private.txt').write_text('private workspace file', encoding='utf-8')
        script = root / 'tools/serve_frontend.mjs'
        shutil.copyfile(Path(__file__).parent / 'tools/serve_frontend.mjs', script)
        self.process = subprocess.Popen([node, str(script), '--port', '0'], stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, encoding='utf-8')
        line = self.process.stdout.readline()
        match = re.search(r'http://127\.0\.0\.1:(\d+)/web/', line)
        if match is None:
            self.process.terminate()
            self.process.wait(timeout=5)
            self.directory.cleanup()
            self.fail('Node static preview did not start: ' + line)
        self.base = 'http://127.0.0.1:' + match.group(1)

    def tearDown(self):
        if hasattr(self, 'process'):
            self.process.terminate()
            self.process.wait(timeout=5)
            self.process.stdout.close()
            self.process.stderr.close()
            self.directory.cleanup()

    def test_browser_modules_have_an_executable_javascript_mime_type(self):
        with urlopen(self.base + '/web/module.js?version=1', timeout=3) as response:
            self.assertEqual(response.headers.get_content_type(), 'text/javascript')
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')

    def test_workspace_files_and_directory_listings_are_not_exposed(self):
        for path in ('/private.txt', '/web/../private.txt', '/%2e%2e/private.txt',
                     '/%252e%252e/private.txt', '/web/', '/data/'):
            if path == '/web/':
                continue  # The application index is intentionally served here.
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                urlopen(self.base + path, timeout=3)
            self.assertEqual(error.exception.code, 404)

    def test_calculation_posts_are_not_accepted(self):
        request = Request(self.base + '/calculate', data=b'{"team":["private input"]}',
                          headers={'Content-Type': 'application/json'}, method='POST')
        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=3)
        self.assertEqual(error.exception.code, 405)


if __name__ == '__main__':
    unittest.main()
