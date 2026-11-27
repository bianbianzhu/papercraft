import contextlib
import http.client
import http.server
import io
import json
import multiprocessing
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import ab_sheet
import capture
import measure
import output_paths
import paper_textures
from PIL import Image


def stalled_worker():
    if os.name != 'nt':
        os.setsid()
    time.sleep(60)


def worker_with_child(port_file, ready):
    os.setsid()
    child = subprocess.Popen(
        [sys.executable, '-c',
         'import signal,socket,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
         's=socket.socket(); s.bind(("127.0.0.1",0)); s.listen(); '
         'print(s.getsockname()[1],flush=True); time.sleep(60)'],
        stdout=subprocess.PIPE, text=True)
    port_file.write_text(child.stdout.readline().strip())
    ready.set()
    time.sleep(60)


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='papercraft-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()

    def test_public_server_boundaries_and_nested_pages(self):
        root = self.base / 'public'
        root.mkdir()
        out = root / 'frames'
        out.mkdir()
        (root / 'nested').mkdir()
        (root / 'empty').mkdir()
        for path, body in [
            (root / 'index.html', 'home'), (root / 'nested/index.html', 'nested'),
            (root / '.hidden', 'private fixture'), (out / 'frame.png', 'output fixture'),
            (self.base / 'outside.txt', 'outside fixture'), (root / 'data.json', '{}'),
        ]:
            path.write_text(body)
        server = capture.serve(root, out)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def request(path, headers=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            try:
                connection.request('GET', path, headers=headers or {})
                response = connection.getresponse()
                return response.status, response.read(), dict(response.getheaders())
            finally:
                connection.close()

        for path in ['/.hidden', '/%2ehidden', '/frames/frame.png', '/empty/',
                     '/../outside.txt', '/%2e%2e/outside.txt', '/nested%5c..%5coutside.txt']:
            with self.subTest(path=path):
                self.assertEqual(request(path)[0], 404)
        if os.name != 'nt':
            (root / 'link.txt').symlink_to(self.base / 'outside.txt')
            self.assertEqual(request('/link.txt')[0], 404)
        self.assertEqual(request('/nested')[0], 301)
        self.assertEqual(request('/nested/')[1], b'nested')
        self.assertEqual(request('/data.json')[1], b'{}')
        self.assertEqual(request('/', {'Host': 'untrusted.invalid'})[0], 404)
        self.assertIn("connect-src 'self'", request('/')[2]['Content-Security-Policy'])
        self.assertEqual(capture.preview_page('nested/?mood=day', root, out),
                         ('nested/', [('mood', 'day')]))
        for page in ['https://example.invalid/', '../outside.txt', 'empty/']:
            with self.subTest(page=page), self.assertRaises(ValueError):
                capture.preview_page(page, root, out)

    def test_output_names_and_symlinks(self):
        for name in ['../outside', '/absolute', 'x/y', 'x\\y', '.hidden', 'CON', 'NUL.txt',
                     'bad\nname', 'trailing.', 'a' * 161]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                output_paths.filename_stem(name)
        for name in ['bridge', 'mood-golden', 'at-1_2']:
            self.assertEqual(output_paths.filename_stem(name), name)
        if os.name == 'nt':
            return
        out = output_paths.output_directory(self.base / 'out')
        sentinel = self.base / 'sentinel.txt'
        sentinel.write_text('unchanged')
        (out / 'shot.png').symlink_to(sentinel)
        with self.assertRaises(ValueError):
            output_paths.output_file(out, 'shot.png')
        (self.base / 'alias').symlink_to(out, target_is_directory=True)
        with self.assertRaises(ValueError):
            output_paths.output_directory(self.base / 'alias/nested')
        self.assertEqual(sentinel.read_text(), 'unchanged')

    def test_shot_values_cannot_inject_javascript_or_paths(self):
        for shot in ['spot=bridge,NaN', 'spot=bridge,Infinity', 'spot=bridge,0);anything()',
                     '../escape:overview', 'video=121,30']:
            with self.subTest(shot=shot), self.assertRaises(ValueError):
                capture.plan(shot)
        self.assertEqual(capture.plan('bridge:spot=bridge,30')[2:], (('bridge', 30.0), 'spot'))
        for shot in ['mood=../escape', 'spot=../escape']:
            with self.subTest(shot=shot), self.assertRaises(ValueError):
                output_paths.filename_stem(capture.plan(shot)[0])

    def test_page_messages_are_bounded_and_escaped(self):
        logs = capture.PageMessages()
        for _ in range(50):
            logs.add('\x1b[31m\n' + 'X' * 1000)
        self.assertEqual(logs.count, 50)
        self.assertEqual(len(logs.samples), 40)
        self.assertNotIn('\x1b', logs.samples[0])
        self.assertNotIn('\n', logs.samples[0])
        self.assertNotIn('X' * 401, logs.samples[0])
        self.assertIn('[truncated:', logs.samples[0])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            logs.report('console')
        self.assertIn('untrusted page console (50 total, 40 shown, 10 omitted from context', output.getvalue())

    def test_diagnostic_log_retains_long_and_late_messages_privately(self):
        long_message = '\x1b[31m\n' + 'x' * 2000 + 'debug-tail'
        with contextlib.redirect_stdout(io.StringIO()) as output, capture.DiagnosticLog(self.base) as log:
            for i in range(50):
                log.record('console.warning', f'warning-{i}', 'overview')
            log.record('pageerror', long_message, 'overview')
        raw = log.path.read_text()
        records = [json.loads(line) for line in raw.splitlines()]
        self.assertTrue(records[0]['untrusted'])
        self.assertEqual(records[-2]['text'], long_message)
        self.assertIn('warning-49', raw)
        self.assertNotIn('\x1b', raw)
        self.assertEqual(records[-1]['omitted'], 0)
        self.assertEqual(records[-1]['truncated'], 0)
        self.assertIn(str(log.path), output.getvalue())
        if os.name != 'nt':
            self.assertEqual(log.path.stat().st_mode & 0o077, 0)

    def test_diagnostic_log_reports_event_and_file_limits(self):
        with mock.patch.object(capture.DiagnosticLog, 'MAX_BYTES', 4096), \
                mock.patch.object(capture.DiagnosticLog, 'MAX_TEXT', 100), \
                contextlib.redirect_stdout(io.StringIO()) as output, capture.DiagnosticLog(self.base) as log:
            for i in range(50):
                log.record('console.warning', '\u2603' * 120, 'overview')
            log.record('build', 'already bounded upstream', truncated=True)
        records = [json.loads(line) for line in log.path.read_text().splitlines()]
        self.assertLessEqual(log.path.stat().st_size, 4096)
        self.assertEqual(records[-1]['type'], 'summary')
        self.assertGreater(records[-1]['omitted'], 0)
        self.assertGreater(records[-1]['truncated'], 0)
        self.assertEqual(records[-1]['written'] + records[-1]['omitted'], 51)
        self.assertTrue(all(record['truncated'] for record in records[1:-1]))
        self.assertTrue(all(len(record['text']) <= 100 for record in records[1:-1]))
        self.assertIn('omitted', output.getvalue())

    def test_diagnostic_log_preserves_failure_trace_and_marks_interruption(self):
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'fixture failure'):
            with capture.DiagnosticLog(self.base) as log:
                raise RuntimeError('fixture failure')
        records = [json.loads(line) for line in log.path.read_text().splitlines()]
        self.assertEqual(records[-2]['type'], 'capture.error')
        self.assertIn('RuntimeError: fixture failure', records[-2]['text'])
        self.assertTrue(records[-1]['interrupted'])

    def test_capture_rejects_resource_overruns_before_serving(self):
        root = self.base / 'public'
        root.mkdir()
        (root / 'index.html').write_text('fixture')
        for args in [
            ['--size', '9000x10'], ['--size', '4k', '--ss', '3'],
            ['video=120,120'], ['video=1,121'],
            ['--page', 'index.html?ss=NaN'], ['--page', 'index.html?res=1000'],
        ]:
            with self.subTest(args=args), mock.patch.object(sys, 'argv', ['capture.py', str(root), *args]), \
                    mock.patch.object(capture, 'serve') as serve, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    capture.main()
                self.assertEqual(result.exception.code, 2)
                serve.assert_not_called()

    def test_supervisor_terminates_stalled_worker(self):
        before = {child.pid for child in multiprocessing.active_children()}
        started = time.monotonic()
        with mock.patch.object(capture, 'capture_worker', stalled_worker), \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(capture.supervise(timeout=0.5), 1)
        self.assertIn('wall-clock limit', errors.getvalue())
        self.assertLess(time.monotonic() - started, 12)
        self.assertEqual({child.pid for child in multiprocessing.active_children()}, before)

    @unittest.skipIf(os.name == 'nt', 'POSIX process-group cleanup')
    def test_cancellation_also_terminates_worker_descendants(self):
        context = multiprocessing.get_context('spawn')
        ready = context.Event()
        port_file = self.base / 'child-port'
        worker = context.Process(target=worker_with_child, args=(port_file, ready))
        worker.start()
        try:
            self.assertTrue(ready.wait(10))
            port = int(port_file.read_text())
            capture.stop_capture(worker)
            self.assertFalse(worker.is_alive())
            deadline = time.monotonic() + 2
            while True:
                with socket.socket() as connection:
                    connection.settimeout(1)
                    result = connection.connect_ex(('127.0.0.1', port))
                if result or time.monotonic() >= deadline:
                    break
                time.sleep(0.02)
            self.assertNotEqual(result, 0)
        finally:
            if worker.is_alive():
                capture.stop_capture(worker)
            worker.close()

    def test_video_rejects_urls_and_invalid_times_without_starting_ffmpeg(self):
        local = self.base / 'fixture.mp4'
        local.write_bytes(b'fixture')
        cases = [('https://example.invalid/a.mp4', '0'), ('//example.invalid/a.mp4', '0'),
                 (str(local), 'NaN'), (str(local), 'inf'), (str(local), '-1')]
        for path, at in cases:
            with self.subTest(path=path, at=at), mock.patch.object(measure.subprocess, 'run') as run:
                with self.assertRaises(SystemExit):
                    measure.video_frame(path, at)
                run.assert_not_called()

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg is not installed')
    def test_video_decodes_local_input_and_rejects_disguised_playlist(self):
        video = self.base / 'sample.mp4'
        subprocess.run([shutil.which('ffmpeg'), '-nostdin', '-v', 'error', '-f', 'lavfi',
                        '-i', 'color=c=red:s=16x16:r=1', '-frames:v', '1', '-threads', '1', str(video)],
                       check=True, capture_output=True, timeout=15)
        with measure.video_frame(str(video), '0') as image:
            image.load()
            self.assertEqual(image.size, (16, 16))
        playlist = self.base / 'playlist.mp4'
        playlist.write_text('#EXTM3U\n#EXTINF:1,\nhttps://example.invalid/segment.ts\n#EXT-X-ENDLIST\n')
        with self.assertRaises(SystemExit):
            measure.video_frame(str(playlist), '0')

    def test_encoding_failure_is_not_reported_as_success(self):
        with mock.patch.object(capture.shutil, 'which', return_value='/fixture/ffmpeg'), \
                mock.patch.object(capture.subprocess, 'run') as run:
            run.return_value = subprocess.CompletedProcess([], 1, '', 'synthetic failure')
            with self.assertRaisesRegex(RuntimeError, 'ffmpeg failed'):
                capture.encode(str(self.base), 30, self.base / 'video.mp4')
        with mock.patch.object(capture.shutil, 'which', return_value=None), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertFalse(capture.encode(str(self.base), 30, self.base / 'video.mp4'))
        self.assertIn('saving the PNG sequence', output.getvalue())

    def test_ab_key_is_private_and_deterministic_with_duplicate_basenames(self):
        files = []
        for folder, color in [('first', 'red'), ('second', 'blue')]:
            (self.base / folder).mkdir()
            path = self.base / folder / 'same.png'
            Image.new('RGB', (16, 16), color).save(path)
            files.append(str(path))
        sheet = self.base / 'sheet.png'
        key_path = self.base / 'keys/sheet.json'
        args = [*files, '--out', str(sheet)]
        with contextlib.redirect_stdout(io.StringIO()):
            ab_sheet.main(args)
            first_key, first_sheet = key_path.read_bytes(), sheet.read_bytes()
            ab_sheet.main(args)
        key = json.loads(first_key)
        self.assertNotIn(str(self.base), first_key.decode())
        self.assertNotIn('sha256', first_key.decode())
        self.assertNotIn('seed', key)
        self.assertEqual(sorted(item['input_index'] for item in key['order']), [1, 2])
        self.assertEqual(first_key, key_path.read_bytes())
        self.assertEqual(first_sheet, sheet.read_bytes())

    def test_texture_output_rejects_escaping_name_before_building(self):
        with mock.patch.object(paper_textures, 'build') as build, \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                paper_textures.main(['--out', str(self.base), '--name', '../outside'])
            build.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'requires unprivileged symlink creation')
    def test_texture_invalid_destination_preserves_existing_outputs(self):
        sentinel = self.base / 'sentinel.json'
        sentinel.write_text('unchanged')
        (self.base / 'paper.json').symlink_to(sentinel)
        albedo = self.base / 'paper_albedo.png'
        albedo.write_bytes(b'previous output')
        with self.assertRaises(ValueError):
            paper_textures.main(['--out', str(self.base), '--name', 'paper', '--size', '256', '--maps', 'albedo'])
        self.assertEqual(sentinel.read_text(), 'unchanged')
        self.assertEqual(albedo.read_bytes(), b'previous output')
        self.assertEqual(list(self.base.glob('.texture-*')), [])


@unittest.skipUnless(os.environ.get('PAPERCRAFT_BROWSER_TESTS') == '1',
                     'set PAPERCRAFT_BROWSER_TESTS=1 with Chrome or Playwright Chromium installed')
class BrowserSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='papercraft-browser-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'public'
        self.root.mkdir()
        self.out = self.base / 'captures'
        self.scripts = Path(capture.__file__).resolve().parent

    def run_capture(self, shots, timeout=30, **env):
        command = [sys.executable, '-c',
                   'import sys; sys.path.insert(0,sys.argv.pop(1)); import capture; '
                   f'sys.exit(capture.supervise(timeout={timeout}))',
                   str(self.scripts), str(self.root), *shots,
                   '--size', '64x64', '--out', str(self.out)]
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout + 20,
                              env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', **env})

    def test_browser_blocks_external_requests_and_preserves_video_outputs(self):
        requests = []

        class External(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), External)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        origin = f'http://127.0.0.1:{server.server_port}'
        (self.root / 'index.html').write_text(f"""<!doctype html><body>local fixture<script>
        window.__papercraft = {{
          ready: false, buildMs: 1, pieces: 0, frame: {{}},
          clips: () => [], cards: () => 0, restart() {{}}, step() {{}},
          heroPos: {{x:0,z:0}}, steer() {{}}, spots: {{bridge:[0,0]}}, look() {{}}
        }};
        Promise.all([
          fetch('{origin}/fetch').catch(() => console.warn('fetch blocked')),
          navigator.serviceWorker.register('./sw.js').then(
            async () => {{
              if ((await navigator.serviceWorker.getRegistrations()).length)
                throw new Error('unexpected service worker');
              console.warn('worker blocked');
            }}, () => console.warn('worker blocked'))
        ]).then(() => {{ __papercraft.ready = true; }});
        new WebSocket('ws://127.0.0.1:{server.server_port}/socket').onerror = () => {{}};
        console.warn('diagnostic\\u001b[31m\\nfixture');
        </script>""")
        (self.root / 'sw.js').write_text('self.addEventListener("fetch", () => {});')
        shots = ['bridge:spot=bridge,30', 'clip:video=0.1,10', 'walk:route=0.1,10;0,0']
        first = self.run_capture(shots)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn('fetch blocked', first.stdout)
        self.assertIn('worker blocked', first.stdout)
        self.assertIn('untrusted page console', first.stdout)
        self.assertNotIn('\x1b', first.stdout)
        self.assertEqual(requests, [])
        self.assertTrue((self.out / 'bridge.png').is_file())
        frame = self.out / 'clip/f_00000.png'
        original = frame.read_bytes()
        second = self.run_capture(['clip:video=0.1,10'])
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(frame.read_bytes(), original)
        self.assertEqual(len(list(self.out.glob('clip*/f_00000.png'))), 2)
        frames_only = self.run_capture(['frames-only:video=0.1,10'], PATH='')
        self.assertEqual(frames_only.returncode, 0, frames_only.stdout + frames_only.stderr)
        self.assertIn('ffmpeg is not on PATH', frames_only.stdout)
        self.assertTrue((self.out / 'frames-only/f_00000.png').is_file())
        self.assertFalse((self.out / 'frames-only.mp4').exists())
        self.assertEqual(list(self.out.glob('.capture-*')), [])

    def test_watchdog_stops_stalled_page_and_closes_browser(self):
        (self.root / 'index.html').write_text("""<!doctype html><script>
        window.__papercraft = {ready:true,buildMs:1,pieces:0,
          restart(){},step(){},cards:()=>new Promise(()=>{})};
        </script>""")
        result = self.run_capture(['overview'], timeout=5, DEBUG='pw:browser')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('untrusted page build metrics', result.stdout)
        self.assertIn('wall-clock limit', result.stderr)
        self.assertEqual(list(self.out.glob('.capture-*')), [])
        records = [json.loads(line) for line in next(self.out.glob('diagnostics-*.jsonl')).read_text().splitlines()]
        self.assertEqual(records[-1]['type'], 'summary')
        self.assertTrue(records[-1]['interrupted'])
        if os.name != 'nt':
            pids = re.findall(r'<launched> pid=(\d+)', result.stderr)
            self.assertTrue(pids, result.stderr)
            for pid in pids:
                with self.assertRaises(ProcessLookupError):
                    os.kill(int(pid), 0)

    def test_long_and_late_browser_diagnostics_remain_available_after_failure(self):
        (self.root / 'index.html').write_text("""<!doctype html><body>fixture<script>
        window.__papercraft = {ready:true,buildMs:1,pieces:0,
          frame:{note:'M'.repeat(3000)+'metrics-tail'},restart(){},step(){},cards:()=>0,
          clips:()=>Array.from({length:15},(_,i)=>({a:'pair-'+i,b:'other',kind:'fixture'}))};
        for(let i=0;i<50;i++) console.warn('warning-'+i);
        console.info('debug-only-local');
        function diagnosticFailure(){ throw new Error('D'.repeat(900)+'stack-tail'); }
        diagnosticFailure();
        </script>""")
        result = self.run_capture(['overview'])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('10 omitted from context', result.stdout)
        self.assertIn('[truncated:', result.stdout)
        self.assertNotIn('stack-tail', result.stdout)
        self.assertNotIn('debug-only-local', result.stdout)
        self.assertTrue((self.out / 'overview.png').is_file())
        records = [json.loads(line) for line in next(self.out.glob('diagnostics-*.jsonl')).read_text().splitlines()]
        self.assertTrue(any(r.get('text') == 'warning-49' for r in records))
        self.assertTrue(any(r.get('text') == 'debug-only-local' for r in records))
        error = next(r for r in records if r['type'] == 'pageerror')
        self.assertIn('stack-tail', error['text'])
        self.assertIn('diagnosticFailure', error['text'])
        self.assertEqual(error['shot'], 'overview')
        build = next(r for r in records if r['type'] == 'build')
        self.assertIn('metrics-tail', build['text'])
        clips = next(r for r in records if r['type'] == 'clips')
        self.assertEqual(len(json.loads(clips['text'])), 15)
        self.assertEqual(records[-1]['omitted'], 0)
        self.assertEqual(records[-1]['truncated'], 0)


if __name__ == '__main__':
    unittest.main()
