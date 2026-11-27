"""Deterministic review captures and videos of a papercraft build (the harness's window.__papercraft hook).

Serves a dedicated, public build folder, opens the page in Chrome (headless, GPU), waits for the build, runs each shot on the fixed
clock and saves a PNG (or a frame sequence and an MP4). Prints the build time, the frame, the pieces, the standing
cards in frame, the slowest prints, the hero's card-clipping check (cards cutting into each other, standing or
walking) and bounded samples of page errors and console messages, and exits non-zero on a page error, a clipping hero or a walk that
does not arrive.

    python scripts/capture.py <public build folder> [shots ...] [--page index.html] [--out frames] [--size 1600x900] [--ss 1]
                              [--safe all|meta|tiktok|shorts]

--size: WxH, a ratio (9:16 = 1080x1920: the short side 1080) or a format (references/formats.md):
    1080p 1920x1080 | 720p | 1440p | 4k 3840x2160 | reel / story / tiktok / shorts / vertical 1080x1920 |
    reel-hd 1440x2560 | feed / portrait 1080x1350 (4:5) | grid 1080x1440 (3:4) | square 1080x1080 |
    ultrawide 2560x1080 | flat 1998x1080 (1.85) | scope 2048x858 (2.39)
The page gets ?size=WxH, so it renders exactly that frame: the 3D harness frames the shape (the short side keeps the
lens, a tall frame comes closer and lifts the subject) and prints finer for big frames. The default 1600x900 is the
review size measure.py's gates assume. --safe outlines a vertical platform's UI-safe area and the feed crops on the
frames (a check, never a deliverable).

Shots (default: overview):
    overview              restart(); step(3.2): the opening frame, risen
    midrise               restart(); step(0.8): mid-rise
    test                  the ?test page (close, your test cards)
    key                   ?diag=key: the key light alone (shadow shapes)
    mood=NAME             ?mood=NAME (golden, dusk, night or one of yours), risen
    look=X,Z[,DIST[,Y]]   a close look at (X, Z) (DIST default 30)
    spot=NAME[,DIST]      a close look at H.spots[NAME]
    walk=X,Z;X,Z;...      a scripted walk from the start through the waypoints (blockers and steps apply), the camera
                          following; prints { reached, of, stuck, blockedBy, y, on }
    at=X,Z                the hero placed at (X, Z), the walking camera framing him (walk-area and fill edges)
    hold=KEYS,SEC         from the start, hold keys (e.g. ArrowUp+ArrowLeft) for SEC seconds at 60 fps, live input
    video=SEC[,FPS[,KEYS[,FROM]]]  from restart (everything flat), SEC seconds at FPS (default 30) on the fixed
                          clock, one PNG per frame in <out>/<name>/, then <out>/<name>.mp4 if ffmpeg is on PATH (H.264
                          High, yuv420p, BT.709, +faststart: what every platform takes). KEYS (ArrowLeft+ArrowUp, or -)
                          are held while it records; FROM skips that many seconds first (3.2: start risen). Sound is
                          rendered offline and muxed after (references/sound.md).
    route=SEC,FPS[,FROM];X,Z;X,Z;...  a video of the hero walking through the waypoints (steered like a stick, the
                          camera following; FROM default 3.2: the world risen); he stops at the last one
Name a shot's file with NAME: before it (e.g. bridge:spot=bridge). Needs Playwright 1.48+ and installed Chrome or Playwright Chromium.
Prepare browser dependencies separately from the capture. A parent process enforces a 30-minute wall-clock limit,
including stalled page execution and encoding, followed by bounded process cleanup.
Summaries point to a private diagnostics-*.jsonl file in --out. Detailed records are untrusted data, limited to
8 MiB per run and 65536 characters per text field; the final record reports omitted and truncated records.
"""
import argparse
import http.server
import json
import mimetypes
import multiprocessing
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit

from output_paths import filename_stem, linked_path, output_directory, output_file

FORMATS = {
    '720p': (1280, 720), '1080p': (1920, 1080), '1440p': (2560, 1440), '4k': (3840, 2160), 'uhd': (3840, 2160),
    'vertical': (1080, 1920), 'reel': (1080, 1920), 'reels': (1080, 1920), 'story': (1080, 1920), 'tiktok': (1080, 1920),
    'shorts': (1080, 1920), 'reel-hd': (1440, 2560), 'feed': (1080, 1350), 'portrait': (1080, 1350), 'grid': (1080, 1440),
    'square': (1080, 1080), 'ultrawide': (2560, 1080), 'flat': (1998, 1080), 'scope': (2048, 858),
}


def parse_size(s):
    s = s.strip().lower()
    if s in FORMATS:
        return FORMATS[s]
    m = re.fullmatch(r'(\d+)x(\d+)', s)
    if m:
        return int(m[1]), int(m[2])
    m = re.fullmatch(r'([\d.]+):([\d.]+)', s)
    if m:   # a ratio: the short side 1080, both sides even
        a, b = number(m[1], 0.01, 1000), number(m[2], 0.01, 1000)
        w, h = (1080 * a / b, 1080) if a >= b else (1080, 1080 * b / a)
        return int(round(w / 2)) * 2, int(round(h / 2)) * 2
    raise SystemExit(f'capture.py: --size {s!r}: use WxH, a ratio like 9:16, or one of {", ".join(FORMATS)}')


def build_file(root, excluded, url_path):
    parts = unquote(url_path).strip('/').split('/')
    path = root
    for part in parts:
        if not part:
            continue
        if part.startswith('.') or any(c in part for c in ('\\', ':', '\x00')):
            return None
        path = path / part
        if linked_path(path) or (path.is_dir() and path.samefile(excluded)):
            return None
    if not (path.is_dir() or path.is_file()):
        return None
    return path


def serve(root, excluded):
    class Static(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_asset(True)

        def do_HEAD(self):
            self.send_asset(False)

        def send_asset(self, body):
            request_path = self.path.partition('?')[0]
            path = build_file(root, excluded, request_path)
            if path is None or self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
                self.send_error(404)
                return
            if path.is_dir():
                path = build_file(root, excluded, request_path.rstrip('/') + '/index.html')
                if path is None or not path.is_file():
                    self.send_error(404)
                    return
                if not request_path.endswith('/'):
                    self.send_response(301)
                    location, sep, query = self.path.partition('?')
                    self.send_header('Location', '/' + location.strip('/') + '/' + sep + query)
                    self.end_headers()
                    return
            with path.open('rb') as source:
                self.send_response(200)
                self.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
                self.send_header('Content-Length', str(os.fstat(source.fileno()).st_size))
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'self' data: blob:; "
                                 "script-src 'self' 'unsafe-inline'; "
                                 "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
                                 "object-src 'none'; base-uri 'self'; form-action 'none'")
                self.end_headers()
                if body:
                    shutil.copyfileobj(source, self.wfile)

    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Static)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def number(value, low=-1e6, high=1e6):
    n = float(value)
    if not low <= n <= high:
        raise ValueError(f'number must be between {low:g} and {high:g}')
    return n


def coordinates(arg, lengths):
    values = [number(t) for t in arg.split(',')]
    if len(values) not in lengths:
        raise ValueError('incorrect number of coordinates')
    return values


def preview_page(value, root, excluded):
    url = urlsplit(value)
    if url.scheme or url.netloc or url.fragment or value.startswith('/'):
        raise ValueError('--page must be relative to the build')
    target = build_file(root, excluded, url.path)
    if target is None:
        raise ValueError('--page must name a file or index directory in the build')
    if target.is_dir() and not build_file(root, excluded, url.path.rstrip('/') + '/index.html'):
        raise ValueError('selected directory has no public index.html')
    query = parse_qsl(url.query, keep_blank_values=True, max_num_fields=64)
    for key, val in query:
        if key == 'ss':
            number(val, 1, 3)
        if key == 'res':
            number(val, 0.1, 3)
    return quote(unquote(url.path), safe='/'), query


def display(value, limit=400):
    text = str(value)
    suffix = f' [truncated: {len(text) - limit} characters omitted]' if len(text) > limit else ''
    return ascii(text[:limit]) + suffix


class DiagnosticLog:
    MAX_BYTES = 8 * 1024**2
    MAX_TEXT = 65536

    def __init__(self, directory):
        self.file = tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory,
                                                prefix='diagnostics-', suffix='.jsonl', delete=False)
        self.path = Path(self.file.name)
        self.bytes = self.written = self.omitted = self.truncated = 0
        self.full = False

    def _write(self, record):
        line = json.dumps(record, ensure_ascii=True) + '\n'
        self.file.write(line)
        self.file.flush()
        self.bytes += len(line.encode('utf-8'))

    def __enter__(self):
        try:
            self._write({'type': 'header', 'untrusted': True, 'max_bytes': self.MAX_BYTES,
                         'max_text_chars': self.MAX_TEXT})
        except OSError:
            self.file.close()
            raise
        print('untrusted diagnostic details (private):', display(str(self.path), 4096), flush=True)
        return self

    def record(self, kind, value, shot=None, *, truncated=False, location=None):
        if self.full:
            self.omitted += 1
            return
        text = str(value)
        limited = truncated or len(text) > self.MAX_TEXT
        record = {'type': kind, 'shot': shot, 'text': text[:self.MAX_TEXT], 'truncated': limited}
        if location:
            url = str(location.get('url', ''))
            record['location'] = {'url': url[:4096], 'line': location.get('lineNumber'),
                                  'column': location.get('columnNumber'), 'truncated': len(url) > 4096}
            limited = limited or len(url) > 4096
            record['truncated'] = limited
        # Reserve space for the final accounting record, even when page output fills the budget.
        if self.bytes + len((json.dumps(record, ensure_ascii=True) + '\n').encode('utf-8')) > self.MAX_BYTES - 1024:
            self.full = True
            self.omitted += 1
            return
        self._write(record)
        self.written += 1
        self.truncated += int(limited)

    def __exit__(self, kind, error, tb):
        try:
            if error is not None:
                self.record('capture.error', ''.join(traceback.format_exception(kind, error, tb)))
            self._write({'type': 'summary', 'written': self.written, 'omitted': self.omitted,
                         'truncated': self.truncated, 'interrupted': kind is not None})
            print(f'untrusted diagnostic details: {self.written} records saved, {self.omitted} omitted, '
                  f'{self.truncated} truncated; file {display(str(self.path), 4096)}')
        finally:
            self.file.close()


class PageMessages:
    def __init__(self):
        self.count = 0
        self.samples = []

    def add(self, message):
        self.count += 1
        if len(self.samples) < 40:
            self.samples.append(display(message))

    def report(self, kind):
        if self.count:
            print(f'untrusted page {kind} ({self.count} total, {len(self.samples)} shown, '
                  f'{self.count - len(self.samples)} omitted from context; see diagnostic file)')
            for sample in self.samples:
                print(' ', sample)


def plan(shot):
    """(file name, url query, js run on the risen page or None, kind)"""
    name = None
    if ':' in shot.split('=')[0]:
        name, shot = shot.split(':', 1)
        filename_stem(name)
    key, _, arg = shot.partition('=')
    base = 'restart(); step(3.2);'
    if key == 'overview': return name or 'overview', '', base, 'frame'
    if key == 'midrise': return name or 'midrise', '', 'restart(); step(0.8);', 'frame'
    if key == 'test': return name or 'test', 'test', 'restart(); step(2.5);', 'frame'
    if key == 'key': return name or 'key', 'diag=key', base, 'frame'
    if key == 'mood': return name or f'mood-{arg}', urlencode({'mood': arg}), base, 'frame'
    if key == 'look':
        v = coordinates(arg, (2, 3, 4))
        return name or 'look-' + '_'.join(f'{t:g}' for t in v[:2]), '', base + f' look({", ".join(str(t) for t in v)}); step(1 / 60);', 'frame'
    if key == 'spot':
        sp, _, d = arg.partition(',')
        return name or f'spot-{sp}', '', (sp, number(d or 30, 0.001)), 'spot'
    if key == 'walk':
        pts = [coordinates(p, (2,)) for p in arg.split(';') if p]
        return name or 'walk', '', pts, 'walk'
    if key == 'at':
        x, z = coordinates(arg, (2,))
        return name or f'at-{x:g}_{z:g}', '', base + f' const y = place({x}, {z}); step(0.5); return y;', 'at'
    if key == 'hold':
        keys, _, sec = arg.partition(',')
        return name or 'hold', '', (keys.split('+'), number(sec or 1, 0, 120)), 'hold'
    if key == 'video':
        p = arg.split(',')
        sec = number(p[0] or 4, 0.001, 120) if p and p[0] else 4.0
        fps = int(p[1]) if len(p) > 1 and p[1] else 30
        keys = p[2].split('+') if len(p) > 2 and p[2] and p[2] != '-' else []
        start = number(p[3], 0, 120) if len(p) > 3 and p[3] else 0.0
        if len(p) > 4: raise ValueError('too many video arguments')
        return name or 'video', '', (sec, fps, keys, start, None), 'video'
    if key == 'route':
        head, *pts = arg.split(';')
        p = head.split(',')
        sec, fps = number(p[0] or 4, 0.001, 120), int(p[1]) if len(p) > 1 and p[1] else 30
        start = number(p[2], 0, 120) if len(p) > 2 and p[2] else 3.2
        way = [coordinates(q, (2,)) for q in pts if q]
        if len(p) > 3 or not way: raise ValueError('route needs a timing header and waypoints')
        return name or 'route', '', (sec, fps, [], start, way), 'video'
    raise SystemExit(f'capture.py: unknown shot "{shot}" (see the docstring)')


def encode(frames_dir, fps, mp4):
    """Frame PNGs -> an MP4 every platform accepts: H.264 High, yuv420p, BT.709 limited range, closed GOP of half a
    second, +faststart (references/formats.md, export settings)."""
    exe = shutil.which('ffmpeg')
    if not exe:
        print('  ffmpeg is not on PATH; saving the PNG sequence')
        return False
    cmd = [exe, '-y', '-loglevel', 'error', '-framerate', str(fps), '-i', os.path.join(frames_dir, 'f_%05d.png'),
           '-vf', 'scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv',
           '-c:v', 'libx264', '-profile:v', 'high',
           '-preset', 'slow', '-crf', '18', '-g', str(max(1, fps // 2)), '-bf', '2',
           '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-color_range', 'tv',
           '-movflags', '+faststart', mp4]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError('ffmpeg could not complete: ' + display(exc)) from exc
    if r.returncode:
        raise RuntimeError('ffmpeg failed: ' + display(r.stderr[-600:]))
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('project'); ap.add_argument('shots', nargs='*')
    ap.add_argument('--page', default='index.html'); ap.add_argument('--out', default='frames')
    ap.add_argument('--size', default='1600x900'); ap.add_argument('--ss', default='1')
    ap.add_argument('--safe', choices=['all', 'meta', 'tiktok', 'shorts'], help='outline a vertical platform safe area: all | meta | tiktok | shorts')
    a = ap.parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit('capture.py requires Playwright 1.48+ and installed Chrome or Playwright Chromium')
    try:
        root = Path(a.project).resolve(strict=True)
        if not root.is_dir() or root == root.parent or root == Path.home().resolve() or (root / '.git').exists():
            raise ValueError('project must be a dedicated public build directory')
        out_path = root / a.out
        if out_path.resolve() == root or out_path.resolve() in root.parents:
            raise ValueError('--out must be separate from the build root')
        out = output_directory(out_path)
        page, page_query = preview_page(a.page, root, out)
        W, H = parse_size(a.size)
        ss = number(a.ss, 1, 3)
        if a.ss == '1':
            ss = next((number(v, 1, 3) for k, v in page_query if k == 'ss'), 1)
        page_query = [(k, v) for k, v in page_query if k not in ('capture', 'size', 'ss')
                      and (not a.safe or k != 'safe')]
        if min(W, H) < 1 or max(W, H) > 8192 or W * H * ss ** 2 > 36_000_000:
            raise ValueError('capture exceeds the 8192-side or 36 megapixel supersampled frame limit')
        plans = [plan(shot) for shot in a.shots or ['overview']]
        frames = 0
        for name, _, payload, kind in plans:
            filename_stem(name)
            if kind == 'video':
                sec, fps, *_ = payload
                if not 1 <= fps <= 120 or W % 2 or H % 2:
                    raise ValueError('video needs 1–120 fps and even dimensions')
                frames += max(1, round(sec * fps))
            else:
                frames += 1
        if frames > 3600:
            raise ValueError('capture exceeds the 3600-frame budget')
    except (OSError, ValueError) as exc:
        ap.error(str(exc))
    failed = False
    deadline = time.monotonic() + 1800
    with ExitStack() as stack:
        diagnostics = stack.enter_context(DiagnosticLog(out))
        server = serve(root, out)
        stack.callback(server.server_close)
        stack.callback(server.shutdown)
        origin = f'http://127.0.0.1:{server.server_port}'
        staging = Path(stack.enter_context(tempfile.TemporaryDirectory(dir=out, prefix='.capture-')))
        p = stack.enter_context(sync_playwright())
        args = ['--enable-gpu', '--ignore-gpu-blocklist'] + (['--use-angle=d3d11'] if os.name == 'nt' else [])
        try: b = p.chromium.launch(channel='chrome', headless=True, args=args, chromium_sandbox=True)
        except Exception: b = p.chromium.launch(headless=True, args=args, chromium_sandbox=True)
        logs, errors = PageMessages(), PageMessages()

        def console_message(message):
            diagnostics.record('console.' + message.type, message.text, name, location=message.location)
            if message.type in ('error', 'warning'):
                logs.add(f'{message.type}: {message.text}')

        def page_error(error):
            diagnostics.record('pageerror', getattr(error, 'stack', None) or str(error), name)
            errors.add(str(error))

        context, cur = None, None
        disk_bytes = 0
        for name, query, js, kind in plans:
            if time.monotonic() > deadline:
                raise SystemExit('capture exceeded 30 minutes')
            q = '&'.join(t for t in ('capture', f'size={W}x{H}', f'ss={ss:g}',
                                     f'safe={a.safe}' if a.safe else '', urlencode(page_query), query) if t)
            if q != cur:   # a fresh page per URL: a reload in the same tab builds slower (the old page's canvases)
                if context:
                    context.close()
                context = b.new_context(viewport={'width': W, 'height': H}, service_workers='block', accept_downloads=False)
                if not hasattr(context, 'route_web_socket'):
                    raise SystemExit('capture.py requires Playwright 1.48+ for WebSocket routing')
                context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else route.abort())
                context.route_web_socket('**/*', lambda ws: ws.close())
                pg = context.new_page()
                pg.on('console', console_message)
                pg.on('pageerror', page_error)
                pg.goto(f'{origin}/{page}?{q}')
                try:
                    pg.wait_for_function('window.__papercraft && window.__papercraft.ready', timeout=300000)
                except Exception:
                    print('the page never became ready (no window.__papercraft.ready):')
                    errors.report('errors')
                    logs.report('console messages')
                    raise SystemExit(1)
                cur = q
                stats = pg.evaluate("""limit => {
                    const p = __papercraft;
                    const text = JSON.stringify({buildMs: p.buildMs, pieces: p.pieces, frame: p.frame,
                        prints: p.printStats?.(5)});
                    return {text: text.slice(0, limit), truncated: text.length > limit};
                }""", diagnostics.MAX_TEXT)
                diagnostics.record('build', stats['text'], name, truncated=stats['truncated'])
                print(f'[{q}] untrusted page build metrics {display(stats["text"], 2400)}')
                clips = pg.evaluate("""limit => {
                    const clips = __papercraft.clips?.();
                    if (clips == null) return null;
                    const text = JSON.stringify(clips);
                    return {count: clips.length, text: text.slice(0, limit), truncated: text.length > limit};
                }""", diagnostics.MAX_TEXT)
                if clips is not None:
                    count = clips['count']
                    diagnostics.record('clips', clips['text'], name, truncated=clips['truncated'])
                    print(f'  hero: {int(count)} card pair(s) cut into each other' if count else '  hero: no card cuts into another, standing or walking')
                    if count:
                        print('  untrusted page clip details', display(clips['text'], 1200))
                        failed = True
            if kind == 'video':
                sec, fps, keys, start, way = js
                fdir = Path(tempfile.mkdtemp(dir=staging))
                pg.evaluate(f'() => {{ __papercraft.restart(); if ({start} > 0) __papercraft.step({start}); }}')
                for k in keys: pg.evaluate('(k) => dispatchEvent(new KeyboardEvent("keydown", { code: k, key: k }))', k)
                n, wi = max(1, round(sec * fps)), 0
                for i in range(n):
                    if way:   # steer toward the next waypoint (a stick held that way); stop at the last
                        while wi < len(way):
                            h = pg.evaluate('(() => { const h = __papercraft.heroPos; return [h.x, h.z]; })()')
                            dx, dz = way[wi][0] - h[0], way[wi][1] - h[1]
                            if dx * dx + dz * dz > 0.36: break
                            wi += 1
                        pg.evaluate(f'() => __papercraft.steer({dx}, {dz})' if wi < len(way) else '() => __papercraft.steer(null)')
                    pg.evaluate(f'() => __papercraft.step({1 / fps}, {fps})')
                    frame = fdir / f'f_{i:05d}.png'
                    pg.screenshot(path=frame)
                    disk_bytes += frame.stat().st_size
                    if disk_bytes > 8 * 1024**3 or time.monotonic() > deadline:
                        raise SystemExit('capture exceeded the 8 GiB or 30 minute budget')
                for k in keys: pg.evaluate('(k) => dispatchEvent(new KeyboardEvent("keyup", { code: k, key: k }))', k)
                if way:
                    pg.evaluate('() => __papercraft.steer(null)')
                    h = pg.evaluate('(() => { const h = __papercraft.heroPos; return [+h.x.toFixed(1), +h.z.toFixed(1)]; })()')
                    diagnostics.record('route', {'reached': wi, 'of': len(way), 'hero': h}, name)
                    print(f'{name}: waypoints reached {wi} of {len(way)}, untrusted page hero at {display(h)}')
                    if wi < len(way): failed = True
                destination = out / name
                if destination.exists() or destination.is_symlink() or (out / (name + '.mp4')).exists():
                    destination = out / (name + '-' + fdir.name)
                mp4 = output_file(out, destination.name + '.mp4')
                encoded = staging / 'video.mp4'
                fdir.rename(destination)
                try:
                    ok = encode(destination, fps, encoded)
                except RuntimeError as exc:
                    raise SystemExit(f'{exc}; PNGs retained in {destination}') from exc
                if ok:
                    encoded.replace(mp4)
                print(f'{name}: {n} frames at {fps} fps ({W}x{H}) in {destination}' + (f' -> {mp4}' if ok else ''))
                continue
            if kind == 'walk':
                r = pg.evaluate('(pts) => { const P = __papercraft; P.restart(); P.step(3.2); const r = P.walkPath(pts); P.step(1 / 60); return r; }', js)
                diagnostics.record('walk', r, name)
                r.pop('heights', None); print(f'{name}: untrusted page walk {display(r)}')
                if r.get('reached', 0) < r.get('of', 0): failed = True
            elif kind == 'hold':
                keys, sec = js
                pg.evaluate('() => { __papercraft.restart(); __papercraft.step(3.2); }')
                for k in keys: pg.evaluate('(k) => dispatchEvent(new KeyboardEvent("keydown", { code: k, key: k }))', k)
                pg.evaluate(f'() => __papercraft.step({sec})')
                for k in keys: pg.evaluate('(k) => dispatchEvent(new KeyboardEvent("keyup", { code: k, key: k }))', k)
                pos = pg.evaluate('(() => { const h = __papercraft.heroPos; return [+h.x.toFixed(2), +h.y.toFixed(2), +h.z.toFixed(2)]; })()')
                diagnostics.record('hero', pos, name)
                print(f'{name}: untrusted page hero at {display(pos)}')
            elif kind == 'spot':
                pg.evaluate("""([name, distance]) => {
                    const p = __papercraft;
                    p.restart(); p.step(3.2);
                    const [x, z] = p.spots[name];
                    p.look(x, z, distance); p.step(1 / 60);
                }""", js)
            else:
                r = pg.evaluate(f'(() => {{ const P = __papercraft; with (P) {{ {js} }} }})()')
                if kind == 'at':
                    diagnostics.record('hero-height', r, name)
                    print(f'{name}: untrusted page hero height {display(r)}' + ('  (outside the walk field: blocked there)' if r is None else ''))
            cards = pg.evaluate('__papercraft.cards ? __papercraft.cards() : null')
            diagnostics.record('cards', cards, name)
            destination = output_file(out, name + '.png')
            still = staging / 'still.png'
            pg.screenshot(path=still)
            disk_bytes += still.stat().st_size
            if disk_bytes > 8 * 1024**3:
                raise SystemExit('capture exceeded the 8 GiB budget')
            still.replace(destination)
            print(f'  -> {os.path.join(a.out, name + ".png")}' + (f'  (untrusted page standing cards: {display(cards)})' if cards is not None else ''))
        b.close()
    errors.report('errors')
    logs.report('console messages')
    if errors.count or failed: sys.exit(1)


def capture_worker():
    if os.name != 'nt':
        os.setsid()
        signal.signal(signal.SIGTERM, cancel_capture)
    try:
        main()
    except Exception as exc:
        sys.exit(f'capture failed ({type(exc).__name__}, potentially untrusted diagnostic): {display(exc, 1000)}')


def cancel_capture(signum, frame):
    raise SystemExit('capture cancelled')


def stop_capture(worker):
    if os.name == 'nt':
        try:
            result = subprocess.run(['taskkill', '/PID', str(worker.pid), '/T', '/F'],
                                    capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            worker.kill()
            worker.join(5)
            raise RuntimeError('could not terminate the capture process tree: ' + display(exc)) from exc
        if result.returncode and worker.is_alive():
            worker.kill()
            worker.join(5)
            raise RuntimeError('could not terminate the capture process tree: ' + display(result.stderr))
    else:
        worker.terminate()
        worker.join(5)
        # Only the capture worker creates this session; clean up any encoder still in its group.
        try:
            os.killpg(worker.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if worker.is_alive():
            worker.kill()
    worker.join(5)
    if worker.is_alive():
        raise RuntimeError('capture worker did not terminate')


def supervise(timeout=1800):
    worker = multiprocessing.get_context('spawn').Process(target=capture_worker)
    worker.start()
    try:
        worker.join(timeout)
        if worker.is_alive():
            print(f'capture exceeded its {timeout:g}-second wall-clock limit; stopping the capture', file=sys.stderr)
            stop_capture(worker)
            return 1
        return worker.exitcode if worker.exitcode >= 0 else 1
    except KeyboardInterrupt:
        print('capture interrupted; stopping the capture', file=sys.stderr)
        stop_capture(worker)
        return 130
    finally:
        if worker.is_alive():
            stop_capture(worker)
        worker.close()


if __name__ == '__main__':
    sys.exit(supervise())
