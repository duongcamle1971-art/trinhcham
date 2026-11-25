#!/usr/bin/env python3
# CppJudgeAI: tim hieu bai toan, AI sinh code + test, chay C++14 bang g++ va cham diem.
# Chi dung thu vien chuan cua Python. Can: g++ tren may va API key Anthropic.
import json, os, re, subprocess, sys, tempfile, threading, time, webbrowser
import urllib.request, urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

MODEL = 'claude-sonnet-5-5'
HOME = os.path.join(os.path.expanduser('~'), '.cpp_judge')
WORK = os.path.join(HOME, 'work')
CFG = os.path.join(HOME, 'config.json')
os.makedirs(WORK, exist_ok=True)
IS_WIN = os.name == 'nt'
NOWIN = 0x08000000 if IS_WIN else 0
EXE = '.exe' if IS_WIN else ''
STATE = {'tests': [], 'dir': None, 'tl': 1000}
LOCK = threading.Lock()


def get_key():
    k = os.environ.get('ANTHROPIC_API_KEY', '')
    if k:
        return k
    try:
        return json.load(open(CFG, encoding='utf8')).get('api_key', '')
    except Exception:
        return ''


def set_key(k):
    with open(CFG, 'w', encoding='utf8') as f:
        json.dump({'api_key': k.strip()}, f)


def call_claude(prompt, max_tokens=12000):
    key = get_key()
    if not key:
        raise RuntimeError('Chưa có API key. Hãy nhập key ở đầu trang.')
    body = json.dumps({'model': MODEL, 'max_tokens': max_tokens,
                       'messages': [{'role': 'user', 'content': prompt}]}).encode('utf8')
    req = urllib.request.Request('https://api.anthropic.com/v1/messages', data=body, headers={
        'content-type': 'application/json', 'x-api-key': key, 'anthropic-version': '2023-06-01'})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError('API lỗi %s: %s' % (e.code, e.read().decode('utf8', 'replace')[:300]))
    except urllib.error.URLError as e:
        raise RuntimeError('Không kết nối được Internet: %s' % e.reason)
    return ''.join(b.get('text', '') for b in d.get('content', []) if b.get('type') == 'text')


def norm(s):
    return ' '.join(s.split())


def section(text, tag):
    m = re.search(r'<%s>(.*?)</%s>' % (tag, tag), text, re.S)
    if not m:
        return ''
    s = m.group(1).strip()
    s = re.sub(r'^```[a-zA-Z+]*\n', '', s)
    s = re.sub(r'\n```$', '', s)
    return s.strip()


def compile_cpp(src, exe):
    try:
        r = subprocess.run(['g++', '-std=c++14', '-O2', '-o', exe, src], capture_output=True,
                           timeout=120, creationflags=NOWIN)
    except FileNotFoundError:
        raise RuntimeError('Không tìm thấy g++. Hãy cài MinGW-w64 (Windows), Xcode CLT (macOS) hoặc build-essential (Linux).')
    except subprocess.TimeoutExpired:
        return False, 'Bien dich qua lau'
    return r.returncode == 0, r.stderr.decode('utf8', 'replace')


def run_exe(exe, inp='', timeout=10.0, args=()):
    t0 = time.perf_counter()
    try:
        r = subprocess.run([exe] + list(args), input=inp.encode('utf8'), capture_output=True,
                           timeout=timeout, creationflags=NOWIN)
        ms = (time.perf_counter() - t0) * 1000
        return {'code': r.returncode, 'out': r.stdout.decode('utf8', 'replace'),
                'err': r.stderr.decode('utf8', 'replace'), 'ms': ms, 'tle': False}
    except subprocess.TimeoutExpired:
        return {'code': -1, 'out': '', 'err': '', 'ms': timeout * 1000, 'tle': True}


PROMPT = '''Ban la nguoi ra de va huan luyen vien lap trinh thi dau. Bai toan: "{title}". {extra}
Tra loi dung cac the sau, khong them gi ngoai cac the. Khong dung markdown trong code. Viet bang tieng Viet co dau cho phan chu.
<statement>de bai day du: mo ta, dinh dang input, output, rang buoc cu the, mot vi du</statement>
<idea>giai thich de nguoi hoc hieu bai: y tuong tung buoc, vi sao dung, do phuc tap</idea>
<solution>C++14 hoan chinh, loi giai toi uu, dung long long khi can</solution>
<brute>C++14 hoan chinh, loi giai vet can don gian nhat, chac chan dung, chi can chay duoc tren test nho</brute>
<generator>C++14 hoan chinh. Nhan argv[1]=k (0..{last}) va argv[2]=seed, in ra stdout mot input hop le tuyet doi, dung mt19937 khoi tao tu seed. Khoang 40% k dau: test rat nho (n<=8, co ca bien). 30% tiep: co vua, brute van chay duoc duoi 1 giay. 30% cuoi: sat gioi han toi da va test ac y (gia tri cuc dai gay tran int, toan phan tu bang nhau, da sap xep hoac nguoc, cau truc xau nhat cho cach ngay tho).</generator>
<sample>input cua vi du trong de bai</sample>
<timelimit>so mili giay, thuong 1000</timelimit>
Dap an phai duy nhat voi moi input (neu de co nhieu dap an thi chinh de de chi con mot).'''

TAGS = ['statement', 'idea', 'solution', 'brute', 'generator', 'sample', 'timelimit']


def build(title, extra, n):
    wd = tempfile.mkdtemp(dir=WORK)
    prompt = PROMPT.format(title=title, extra=('Yeu cau them: ' + extra) if extra else '', last=n - 1)
    problem = ''
    for attempt in range(3):
        text = call_claude(prompt + (('\n\nLan truoc bi loi, hay sua va tra lai du tat ca cac the:\n' + problem[:3000]) if problem else ''))
        sec = {t: section(text, t) for t in TAGS}
        miss = [t for t in TAGS if not sec[t]]
        if miss:
            problem = 'Thieu cac the: ' + ', '.join(miss)
            continue
        exes = {}
        problem = ''
        for name in ('solution', 'brute', 'generator'):
            src = os.path.join(wd, name + '.cpp')
            open(src, 'w', encoding='utf8').write(sec[name])
            exes[name] = os.path.join(wd, name + EXE)
            ok, err = compile_cpp(src, exes[name])
            if not ok:
                problem = 'Loi bien dich %s:\n%s' % (name, err[:1500])
                break
        if problem:
            continue
        tests, bad = [], []
        s = run_exe(exes['solution'], sec['sample'] + '\n', 10)
        if s['code'] == 0 and not s['tle']:
            tests.append({'name': 'Ví dụ', 'input': sec['sample'] + '\n', 'expected': s['out']})
        for k in range(n):
            g = run_exe(exes['generator'], '', 10, [str(k), str(1000 + k * 37)])
            if g['code'] != 0 or not g['out'].strip():
                bad.append('k=%d: generator loi %s' % (k, g['err'][:200]))
                continue
            inp = g['out']
            so = run_exe(exes['solution'], inp, 10)
            if so['code'] != 0 or so['tle']:
                bad.append('k=%d: solution loi hoac qua cham' % k)
                continue
            if k < n * 0.7:
                bo = run_exe(exes['brute'], inp, 5)
                if not bo['tle'] and norm(bo['out']) != norm(so['out']):
                    bad.append('k=%d: solution va brute khac nhau.\nInput: %s\nsolution: %s\nbrute: %s' % (
                        k, inp[:300], so['out'][:100], bo['out'][:100]))
                    continue
            tests.append({'name': 'Test %d' % (k + 1), 'input': inp, 'expected': so['out']})
        if len(tests) >= max(2, int(n * 0.6)):
            tl = 1000
            try:
                tl = max(200, min(20000, int(re.sub(r'\D', '', sec['timelimit']) or 1000)))
            except Exception:
                pass
            with LOCK:
                STATE.update(tests=tests, dir=wd, tl=tl)
            return {'statement': sec['statement'], 'idea': sec['idea'], 'solution': sec['solution'],
                    'count': len(tests), 'dropped': len(bad), 'tl': tl}
        problem = 'Bo test chua dat:\n' + '\n'.join(bad)[:2500]
    raise RuntimeError('AI chưa tạo được bộ test hợp lệ sau 3 lần. ' + problem[:300])


def judge(code, tl):
    with LOCK:
        tests, wd = list(STATE['tests']), STATE['dir']
    if not tests:
        raise RuntimeError('Hãy tìm hiểu bài toán và sinh test trước.')
    src = os.path.join(wd, 'user.cpp')
    exe = os.path.join(wd, 'user' + EXE)
    open(src, 'w', encoding='utf8').write(code)
    ok, err = compile_cpp(src, exe)
    if not ok:
        return {'ce': err[:4000], 'score': 0, 'results': []}
    res, ac = [], 0
    for t in tests:
        r = run_exe(exe, t['input'], tl / 1000.0 + 1.0)
        if r['tle'] or r['ms'] > tl:
            v = 'TLE'
        elif r['code'] != 0:
            v = 'RE'
        elif norm(r['out']) == norm(t['expected']):
            v = 'AC'
        else:
            v = 'WA'
        ac += v == 'AC'
        res.append({'name': t['name'], 'verdict': v, 'ms': round(r['ms']),
                    'input': t['input'][:300], 'expected': t['expected'][:300],
                    'got': r['out'][:300], 'err': r['err'][:300]})
    return {'ce': '', 'score': round(ac * 100.0 / len(tests), 2), 'ac': ac, 'total': len(tests), 'results': res}


def gpp_ok():
    try:
        return subprocess.run(['g++', '--version'], capture_output=True, creationflags=NOWIN).returncode == 0
    except Exception:
        return False


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, obj, code=200, ctype='application/json; charset=utf-8'):
        b = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode('utf8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == '/api/status':
            self.send({'has_key': bool(get_key()), 'gpp': gpp_ok(), 'tests': len(STATE['tests']), 'tl': STATE['tl']})
        else:
            self.send(PAGE.encode('utf8'), ctype='text/html; charset=utf-8')

    def do_POST(self):
        try:
            d = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
            if self.path == '/api/key':
                set_key(d.get('key', ''))
                self.send({'ok': True})
            elif self.path == '/api/build':
                self.send(build(d.get('title', '').strip(), d.get('extra', '').strip(), max(4, min(20, int(d.get('n', 10))))))
            elif self.path == '/api/judge':
                self.send(judge(d.get('code', ''), max(100, int(d.get('tl', 1000)))))
            else:
                self.send({'error': 'not found'}, 404)
        except Exception as e:
            self.send({'error': str(e)})


PAGE = r'''<!DOCTYPE html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>C++14 Judge AI</title><style>
:root{--bg:#f7f7f5;--card:#fff;--fg:#1c1c1a;--mut:#6b6b66;--bd:#dcdcd6;--ac:#2f6fed;--ok:#1a8f4c;--bad:#d03a3a;--warn:#b7791f;--code:#f0f0ec}
@media(prefers-color-scheme:dark){:root{--bg:#161615;--card:#212120;--fg:#ececea;--mut:#9a9a94;--bd:#3a3a37;--ac:#6b9bff;--ok:#4cc380;--bad:#ff7070;--warn:#e2b04a;--code:#2a2a28}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,Segoe UI,Arial,sans-serif}
main{max-width:920px;margin:0 auto;padding:16px}h1{font-size:20px;margin:4px 0 10px}.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:14px;margin-bottom:14px}
.card h2{font-size:15px;margin:0 0 10px}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
input,textarea{background:var(--bg);color:var(--fg);border:1px solid var(--bd);border-radius:8px;padding:8px 10px;font:inherit}
input[type=text],input[type=password]{flex:1;min-width:200px}input[type=number]{width:70px}
textarea{width:100%;min-height:280px;font:13px/1.45 Consolas,Menlo,monospace;white-space:pre;tab-size:4}
button{background:var(--ac);color:#fff;border:0;border-radius:8px;padding:8px 14px;font:inherit;cursor:pointer}button:disabled{opacity:.5}
button.sec{background:var(--code);color:var(--fg);border:1px solid var(--bd)}.mut{color:var(--mut);font-size:13px}
pre{background:var(--code);border-radius:8px;padding:8px 10px;margin:4px 0;overflow:auto;font:12.5px/1.45 Consolas,Menlo,monospace;white-space:pre-wrap;word-break:break-all;max-height:380px}
.t{white-space:pre-wrap}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--bd);font-size:14px}
.AC{color:var(--ok);font-weight:600}.WA,.RE{color:var(--bad);font-weight:600}.TLE{color:var(--warn);font-weight:600}.score{font-size:28px;font-weight:700}
details{margin-top:4px}summary{cursor:pointer}
</style></head><body><main>
<h1>C++14 Judge AI</h1>
<div class="card" id="setup"><div class="mut" id="st">Đang kiểm tra...</div>
<div class="row" id="keyRow" style="margin-top:8px"><input type="password" id="key" placeholder="Anthropic API key (sk-ant-...)"><button id="keyBtn" class="sec">Lưu key</button></div></div>
<div class="card"><h2>1. Nhập tựa đề bài toán</h2>
<div class="row"><input type="text" id="title" placeholder="VD: Dãy con tăng dài nhất, Dijkstra, Tổng đoạn con lớn nhất"></div>
<div class="row" style="margin-top:8px"><input type="text" id="extra" placeholder="Ràng buộc mong muốn (tùy chọn)"><label class="mut">Số test <input type="number" id="n" value="10" min="4" max="20"></label>
<button id="buildBtn">Tìm hiểu bài và sinh test</button></div><div class="mut" id="bst" style="margin-top:8px"></div>
<div id="prob" hidden><h2 style="margin-top:12px">Đề bài</h2><div class="t" id="stmt"></div><h2 style="margin-top:12px">Tìm hiểu bài toán</h2><div class="t" id="idea"></div>
<h2 style="margin-top:12px">Code C++14 mẫu do AI sinh</h2><pre id="sol"></pre><button class="sec" id="cp">Sao chép code mẫu</button></div></div>
<div class="card"><h2>2. Code của bạn</h2><textarea id="code" spellcheck="false"></textarea>
<div class="row" style="margin-top:8px"><button id="runBtn" disabled>Biên dịch, chạy và chấm điểm</button><label class="mut">Giới hạn (ms) <input type="number" id="tl" value="1000"></label><span class="mut" id="rst"></span></div></div>
<div class="card" id="res" hidden><h2>3. Kết quả</h2><div class="score" id="score"></div><pre id="ce" hidden></pre>
<div style="overflow-x:auto"><table><thead><tr><th>Test</th><th>Kết quả</th><th>Thời gian</th><th></th></tr></thead><tbody id="rows"></tbody></table></div></div>
</main><script>
const $=i=>document.getElementById(i);
$('code').value='#include <bits/stdc++.h>\nusing namespace std;\n\nint main() {\n    ios::sync_with_stdio(false);\n    cin.tie(nullptr);\n    // code của bạn\n    return 0;\n}\n';
async function api(p,b){const r=await fetch(p,b?{method:'POST',body:JSON.stringify(b)}:{});return r.json();}
function pre(t){const p=document.createElement('pre');p.textContent=t;return p;}
async function status(){const s=await api('/api/status');
 $('st').textContent=(s.gpp?'g++ sẵn sàng (C++14). ':'KHÔNG tìm thấy g++, hãy cài MinGW-w64. ')+(s.has_key?'API key đã lưu.':'Chưa có API key.');
 $('keyRow').hidden=s.has_key; if(s.tests){$('runBtn').disabled=false;$('tl').value=s.tl;}}
$('keyBtn').onclick=async()=>{await api('/api/key',{key:$('key').value});$('key').value='';status();};
$('buildBtn').onclick=async()=>{const title=$('title').value.trim();if(!title){$('bst').textContent='Hãy nhập tựa đề.';return;}
 $('buildBtn').disabled=true;$('runBtn').disabled=true;$('bst').textContent='AI đang viết đề, lời giải, bộ sinh test, rồi biên dịch và đối chiếu (có thể mất 1-2 phút)...';
 try{const d=await api('/api/build',{title,extra:$('extra').value,n:+$('n').value});
  if(d.error){$('bst').textContent='Lỗi: '+d.error;}else{
   $('stmt').textContent=d.statement;$('idea').textContent=d.idea;$('sol').textContent=d.solution;$('prob').hidden=false;$('tl').value=d.tl;
   $('bst').textContent='Đã có '+d.count+' test'+(d.dropped?' (loại '+d.dropped+' test nghi ngờ)':'')+', đáp án tính bằng lời giải C++14 và đối chiếu với lời giải vét cạn ở các test nhỏ.';$('runBtn').disabled=false;}
 }catch(e){$('bst').textContent='Lỗi: '+e;}$('buildBtn').disabled=false;};
$('cp').onclick=()=>navigator.clipboard.writeText($('sol').textContent);
$('runBtn').onclick=async()=>{$('runBtn').disabled=true;$('rst').textContent='Đang biên dịch và chạy...';$('res').hidden=false;$('rows').replaceChildren();$('ce').hidden=true;
 try{const d=await api('/api/judge',{code:$('code').value,tl:+$('tl').value});
  if(d.error){$('score').textContent='Lỗi: '+d.error;}else if(d.ce){$('score').textContent='Lỗi biên dịch: 0 / 100';$('ce').hidden=false;$('ce').textContent=d.ce;}
  else{$('score').textContent='Điểm: '+d.score+' / 100 ('+d.ac+'/'+d.total+' test đúng)';
   for(const r of d.results){const tr=document.createElement('tr');[r.name,r.verdict,r.ms+' ms'].forEach((x,j)=>{const td=document.createElement('td');td.textContent=x;if(j==1)td.className=r.verdict;tr.append(td);});
    const c=document.createElement('td');if(r.verdict!='AC'){const dt=document.createElement('details');const s=document.createElement('summary');s.textContent='Chi tiết';
     dt.append(s,pre('Input:\n'+r.input),pre('Kỳ vọng:\n'+r.expected),pre('Code in ra:\n'+(r.got||'(rỗng)')));if(r.err)dt.append(pre('stderr: '+r.err));c.append(dt);}tr.append(c);$('rows').append(tr);}}
 }catch(e){$('score').textContent='Lỗi: '+e;}$('rst').textContent='';$('runBtn').disabled=false;};
status();
</script></body></html>'''


def main():
    port = 8765
    srv = None
    while srv is None and port < 8800:
        try:
            srv = ThreadingHTTPServer(('127.0.0.1', port), H)
        except OSError:
            port += 1
    url = 'http://127.0.0.1:%d' % port
    print('CppJudgeAI đang chạy tại', url, '(đóng cửa sổ này để thoát)')
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
