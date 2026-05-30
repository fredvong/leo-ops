# Copyright (c) 2026 Fred Vong. All rights reserved.
"""
T-247 Precision Check — Review App

Run from background_rejection/:
    python3 review_app.py

Opens http://localhost:8765 with 30 sampled rows from flagged_backgrounds.csv
(25 flagged, 5 VLM-error rows, shuffled). For each row shows the image and
background description. Pat labels each CLEAN / FLAGGED / UNSURE, optionally
adds notes, and saves results to data/precision_check.csv.
"""

import csv
import http.server
import json
import os
import random
import re
import socketserver
import threading
import webbrowser
from urllib.parse import unquote

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PORTFOLIO_ROOT = '/Volumes/fvong/Portfolio'
_HERE          = os.path.dirname(os.path.abspath(__file__))
CSV_PATH       = os.path.join(_HERE, 'data', 'flagged_backgrounds.csv')
OUTPUT_PATH    = os.path.join(_HERE, 'data', 'precision_check.csv')
PORT           = 8765
SEED           = 42
N_FLAGGED      = 25
N_ERROR        = 5

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _extract_background_description(text: str):
    m = re.search(
        r'\*\*Background Description:\*\*[ \t]*\r?\n([\s\S]*?)(?=\*\*|\Z)',
        text,
    )
    if m:
        val = m.group(1).strip()
        return val or None
    return None


def load_sample():
    with open(CSV_PATH, newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    errors  = [r for r in rows if r.get('error')]
    flagged = [r for r in rows if not r.get('error')]

    rng    = random.Random(SEED)
    sample = (
        rng.sample(flagged, min(N_FLAGGED, len(flagged))) +
        rng.sample(errors,  min(N_ERROR,   len(errors)))
    )
    rng.shuffle(sample)

    for row in sample:
        desc_file = os.path.join(
            PORTFOLIO_ROOT,
            os.path.dirname(row['background_path']),
            '.description',
            f"{row['background_filename']}.txt",
        )
        try:
            with open(desc_file, encoding='utf-8') as f:
                full = _extract_background_description(f.read())
            row['full_description'] = full or row.get('description_excerpt', '')
        except OSError:
            row['full_description'] = row.get('description_excerpt', '')

    return sample


# ---------------------------------------------------------------------------
# HTML (split around the embedded JSON data)
# ---------------------------------------------------------------------------

_HTML_TOP = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>T-247 Precision Check</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f4f5f7;color:#222}

#hdr{position:sticky;top:0;z-index:100;background:#1a1a2e;color:#fff;
     padding:10px 24px;display:flex;align-items:center;gap:14px}
#hdr h1{font-size:15px;font-weight:700;white-space:nowrap}
#pbar-wrap{flex:0 0 160px;height:6px;background:#333;border-radius:3px}
#pbar{height:6px;background:#4caf50;border-radius:3px;width:0%;transition:width .3s}
#ptxt{font-size:12px;color:#aaa;white-space:nowrap;margin-right:auto}
#save-btn{padding:7px 18px;background:#4caf50;color:#fff;border:none;
          border-radius:6px;cursor:pointer;font-size:13px;font-weight:700}
#save-btn:hover{background:#388e3c}
#save-btn:disabled{background:#555;cursor:default}

#cards{padding:20px 24px;display:flex;flex-direction:column;gap:14px;
       max-width:1060px;margin:0 auto}

.card{background:#fff;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,.1);
      display:flex;overflow:hidden;border-left:5px solid #ddd;transition:border-color .2s}
.card.lCLEAN  {border-left-color:#4caf50}
.card.lFLAGGED{border-left-color:#f44336}
.card.lUNSURE {border-left-color:#ff9800}

.cimg{width:260px;min-width:260px;background:#eee;
      display:flex;align-items:center;justify-content:center;overflow:hidden}
.cimg img{width:100%;height:190px;object-fit:cover;display:block}
.no-img{color:#999;font-size:12px;padding:12px;text-align:center}

.cbody{padding:14px 18px;flex:1;display:flex;flex-direction:column;gap:9px}
.ctop{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.cnum{font-size:11px;color:#999;font-weight:700}
.cfile{font-size:12px;color:#555;word-break:break-all;flex:1}

.badge{padding:2px 9px;border-radius:20px;font-size:10px;font-weight:800;
       text-transform:uppercase;letter-spacing:.5px}
.bFLAGGED{background:#fce4e4;color:#c62828}
.bCLEAN  {background:#e8f5e9;color:#2e7d32}
.bERR    {background:#fff3e0;color:#e65100}

.desc{font-size:13px;color:#444;line-height:1.55;background:#f8f8f8;
      border-radius:6px;padding:9px 11px;border-left:3px solid #ddd;
      max-height:110px;overflow-y:auto;white-space:pre-wrap}
.errnote{font-size:11px;color:#e65100;font-style:italic}

.lblrow{display:flex;gap:7px;align-items:center;flex-wrap:wrap}
.lblrow label{font-size:11px;color:#888;font-weight:700;margin-right:2px}
.lbtn{padding:5px 16px;border:2px solid #ddd;border-radius:20px;
      background:#fff;cursor:pointer;font-size:12px;font-weight:700;transition:all .12s}
.lbtn.CLEAN  {border-color:#4caf50;color:#2e7d32}
.lbtn.CLEAN.on  {background:#4caf50;color:#fff}
.lbtn.FLAGGED{border-color:#f44336;color:#c62828}
.lbtn.FLAGGED.on{background:#f44336;color:#fff}
.lbtn.UNSURE {border-color:#ff9800;color:#e65100}
.lbtn.UNSURE.on {background:#ff9800;color:#fff}

.nfield{width:100%;padding:5px 9px;font-size:12px;border:1px solid #ddd;
        border-radius:6px;resize:vertical;font-family:inherit;color:#333;min-height:32px}
.nfield:focus{outline:none;border-color:#90caf9}

#toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);
       background:#323232;color:#fff;padding:11px 22px;border-radius:8px;
       font-size:13px;display:none;z-index:200;box-shadow:0 2px 8px rgba(0,0,0,.3)}
</style>
</head>
<body>
<div id="hdr">
  <h1>T-247 Precision Check</h1>
  <div id="pbar-wrap"><div id="pbar"></div></div>
  <span id="ptxt">0 / 30 labeled</span>
  <button id="save-btn" onclick="save()">Save Results</button>
</div>
<div id="cards"></div>
<div id="toast"></div>
<script>
const DATA = \
"""

_HTML_BOTTOM = """\
;

function esc(s){
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
                  .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function upd(){
  const n = DATA.filter(r => r._label).length;
  document.getElementById('ptxt').textContent = `${n} / ${DATA.length} labeled`;
  document.getElementById('pbar').style.width = `${n / DATA.length * 100}%`;
}

function lbl(i, v){
  DATA[i]._label = v;
  DATA[i]._notes = document.getElementById('n' + i).value;
  document.getElementById('card' + i).className = 'card l' + v;
  ['CLEAN','FLAGGED','UNSURE'].forEach(l => {
    const b = document.getElementById('b' + i + l);
    b.className = 'lbtn ' + l + (l === v ? ' on' : '');
  });
  upd();
}

function build(){
  const c = document.getElementById('cards');
  DATA.forEach((r, i) => {
    const img    = `/image?path=${encodeURIComponent(r.background_path)}`;
    const hasErr = r.error && r.error.trim();
    const desc   = r.full_description || r.description_excerpt || '(no description)';
    const d      = document.createElement('div');
    d.className  = 'card';
    d.id         = 'card' + i;
    d.innerHTML  = `
      <div class="cimg">
        <img src="${img}" alt=""
             onerror="this.parentElement.innerHTML='<div class=\\'no-img\\'>Image not found</div>'">
      </div>
      <div class="cbody">
        <div class="ctop">
          <span class="cnum">#${i + 1}</span>
          <span class="cfile">${esc(r.background_filename)}</span>
          <span class="badge b${r.verdict}">${r.verdict}</span>
          ${hasErr ? '<span class="badge bERR">VLM error</span>' : ''}
        </div>
        <div class="desc">${esc(desc)}</div>
        ${hasErr ? `<div class="errnote">Error: ${esc(r.error)}</div>` : ''}
        <div class="lblrow">
          <label>Your label:</label>
          <button id="b${i}CLEAN"   class="lbtn CLEAN"   onclick="lbl(${i},'CLEAN')">CLEAN</button>
          <button id="b${i}FLAGGED" class="lbtn FLAGGED" onclick="lbl(${i},'FLAGGED')">FLAGGED</button>
          <button id="b${i}UNSURE"  class="lbtn UNSURE"  onclick="lbl(${i},'UNSURE')">UNSURE</button>
        </div>
        <textarea id="n${i}" class="nfield" placeholder="Notes…" rows="1"
                  oninput="DATA[${i}]._notes = this.value"></textarea>
      </div>`;
    c.appendChild(d);
  });
  upd();
}

function toast(msg, ms = 3000){
  const t = document.getElementById('toast');
  t.textContent = msg; t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', ms);
}

function save(){
  const btn = document.getElementById('save-btn');
  btn.disabled = true;
  const payload = DATA.map(r => ({
    background_path:    r.background_path,
    background_filename: r.background_filename,
    verdict:            r.verdict,
    my_label:           r._label  || '',
    notes:              r._notes  || '',
    error:              r.error   || '',
  }));
  fetch('/save', {
    method:  'POST',
    headers: {'Content-Type': 'application/json'},
    body:    JSON.stringify(payload),
  })
  .then(r => r.json())
  .then(d => { toast('Saved → ' + d.path); btn.disabled = false; })
  .catch(e => { toast('Save failed: ' + e); btn.disabled = false; });
}

build();
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class _Handler(http.server.BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == '/':
            safe_json = json.dumps(SAMPLE).replace('</script>', r'<\/script>')
            body = (_HTML_TOP + safe_json + _HTML_BOTTOM).encode('utf-8')
            self._ok('text/html; charset=utf-8', body)

        elif self.path.startswith('/image?path='):
            rel  = unquote(self.path[len('/image?path='):])
            full = os.path.join(PORTFOLIO_ROOT, rel)
            ext  = os.path.splitext(rel)[1].lower()
            mime = {'.png': 'image/png', '.jpg': 'image/jpeg',
                    '.jpeg': 'image/jpeg', '.webp': 'image/webp'}.get(ext, 'image/jpeg')
            if os.path.isfile(full):
                with open(full, 'rb') as f:
                    data = f.read()
                self._ok(mime, data)
            else:
                self.send_response(404); self.end_headers()

        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        if self.path == '/save':
            length  = int(self.headers['Content-Length'])
            payload = json.loads(self.rfile.read(length))
            with open(OUTPUT_PATH, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow(['background_path', 'background_filename',
                             'vlm_verdict', 'my_label', 'notes', 'error'])
                for item in payload:
                    w.writerow([
                        item['background_path'], item['background_filename'],
                        item['verdict'],         item.get('my_label', ''),
                        item.get('notes', ''),   item.get('error', ''),
                    ])
            body = json.dumps({'ok': True, 'path': OUTPUT_PATH}).encode()
            self._ok('application/json', body)
        else:
            self.send_response(404); self.end_headers()

    def _ok(self, ctype, body):
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # suppress per-request logs


socketserver.TCPServer.allow_reuse_address = True

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

SAMPLE = load_sample()


def main():
    with socketserver.TCPServer(('', PORT), _Handler) as srv:
        n_err = sum(1 for r in SAMPLE if r.get('error'))
        print(f'http://localhost:{PORT}')
        print(f'  {len(SAMPLE)} rows  ({len(SAMPLE) - n_err} flagged + {n_err} VLM-error)')
        print(f'  Results → {OUTPUT_PATH}')
        print('Ctrl+C to stop.')
        threading.Timer(0.8, lambda: webbrowser.open(f'http://localhost:{PORT}')).start()
        srv.serve_forever()


if __name__ == '__main__':
    main()
