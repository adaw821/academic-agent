"""BNBU OCR Server - Corrected Version"""
import sys, json, re, base64, io, os, time, traceback
from http.server import HTTPServer, BaseHTTPRequestHandler

HOST = '127.0.0.1'
PORT = 8766

BAIDU_API_KEY = 'Y8uaqxatMKlq7CS2ZEGNoJ82'
BAIDU_SECRET_KEY = 'hz0ZksynH6oVoWcwTLw17qM5vhOYm33j'
BAIDU_TOKEN_URL = 'https://aip.baidubce.com/oauth/2.0/token'
BAIDU_OCR_URL = 'https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic'

HAS_PIL = False
try:
    from PIL import Image; HAS_PIL = True
except ImportError: pass

HAS_TESSERACT = False; TESSERACT_PATH = None
if HAS_PIL:
    try:
        import pytesseract
        for p in [r'D:\Program Files (x86)\tesseract.exe', r'D:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
                   r'C:\Program Files\Tesseract-OCR\tesseract.exe', r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
                   r'C:\Program Files\Tesseract-OCR-w64\tesseract.exe',
                   os.path.expandvars(r'%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe'),
                   os.path.expandvars(r'%ProgramFiles%\Tesseract-OCR\tesseract.exe'),
                   os.path.expandvars(r'%ProgramFiles(x86)%\Tesseract-OCR\tesseract.exe'),
                   os.path.expandvars(r'%USERPROFILE%\AppData\Local\Tesseract-OCR\tesseract.exe')]:
            if os.path.exists(p): pytesseract.pytesseract.tesseract_cmd = p; TESSERACT_PATH = p; HAS_TESSERACT = True; break
        if not HAS_TESSERACT:
            import subprocess
            try:
                r = subprocess.run(['tesseract','--version'], capture_output=True, timeout=5)
                if r.returncode == 0: HAS_TESSERACT = True; TESSERACT_PATH = 'system PATH'
            except: pass
    except ImportError: pass

def get_status():
    return {'python': sys.version, 'pil': HAS_PIL, 'tesseract': HAS_TESSERACT,
            'tesseract_path': TESSERACT_PATH or 'not found', 'baidu_ocr': bool(BAIDU_API_KEY),
            'host': HOST, 'port': PORT}

# Baidu OCR
_baidu_token = None; _baidu_token_time = 0; _baidu_token_ttl = 2592000
import urllib.request, urllib.parse

def _get_baidu_token():
    global _baidu_token, _baidu_token_time
    now = time.time()
    if _baidu_token and (now - _baidu_token_time) < _baidu_token_ttl: return _baidu_token
    url = f'{BAIDU_TOKEN_URL}?grant_type=client_credentials&client_id={BAIDU_API_KEY}&client_secret={BAIDU_SECRET_KEY}'
    resp = urllib.request.urlopen(urllib.request.Request(url), timeout=10)
    data = json.loads(resp.read().decode())
    if 'access_token' not in data: raise RuntimeError(f'Baidu token error: {data}')
    _baidu_token = data['access_token']; _baidu_token_time = now
    return _baidu_token

def baidu_ocr(image_data):
    token = _get_baidu_token()
    img_urlenc = urllib.parse.quote(base64.b64encode(image_data).decode())
    req = urllib.request.Request(f'{BAIDU_OCR_URL}?access_token={token}',
        data=f'image={img_urlenc}&language_type=CHN_ENG'.encode(), method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    result = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
    if 'error_code' in result: raise RuntimeError(f'Baidu OCR {result.get("error_code")}: {result.get("error_msg")}')
    return '\n'.join(w.get('words','').strip() for w in result.get('words_result',[])).strip()

# Tesseract fallback
def tesseract_ocr(image_data):
    import subprocess
    img = Image.open(io.BytesIO(image_data))
    if max(img.size) > 2400:
        r = 2400 / max(img.size); img = img.resize((int(img.size[0]*r), int(img.size[1]*r)), Image.LANCZOS)
    import tempfile, uuid
    tmp = tempfile.gettempdir(); uid = uuid.uuid4().hex[:8]
    inp = os.path.join(tmp, 'bnbu_' + uid + '_in.png'); out_base = os.path.join(tmp, 'bnbu_' + uid + '_out')
    for mode, cfg in [('RGB','--psm 3'),('RGB','--psm 4'),('L','--psm 6'),('RGB','--psm 11'),('RGB','')]:
        try:
            img.convert(mode).save(inp)
            cmd = [TESSERACT_PATH, inp, out_base, '-l','eng'] + (cfg.split() if cfg else [])
            subprocess.run(cmd, capture_output=True, timeout=30)
            txt = out_base + '.txt'
            if os.path.exists(txt):
                t = open(txt,'r',encoding='utf-8',errors='replace').read().strip(); os.remove(txt)
                if len(t) > 20: return t
        except Exception as e:
            last = e
        finally:
            if os.path.exists(inp):
                try: os.remove(inp)
                except: pass
    raise last if 'last' in dir() else Exception('Tesseract failed')

def ocr_image(data):
    try:
        t = baidu_ocr(data)
        if len(t) > 10: print(f'[OCR] Baidu OK ({len(t)} chars)'); return t
    except Exception as e: print(f'[OCR] Baidu failed: {e}')
    if HAS_TESSERACT:
        try:
            t = tesseract_ocr(data)
            if len(t) > 10: print(f'[OCR] Tesseract OK ({len(t)} chars)'); return t
        except Exception as e: print(f'[OCR] Tesseract failed: {e}')
    return ""

# Sections parser - handles both merged lines and table cell formats
def parse_sections(ocr_text):
    """Parse Baidu OCR output for UIC Sections table.
    
    Handles two formats:
    1. Merged: All fields in one line (Baidu table OCR)
       Example: 1   MR   CMP2073   Computer Graphics   Dr.Weipeng   Tue14:00-15:40   T7-302   3.0
    2. Table: Each cell is a separate line (Baidu general OCR)
       Example (multiple lines):
       CMP2073
       Computer Graphics
       Dr.Weipeng
       Tue 14:00-15:40
       T7-302
    """
    results = []
    lines = [l.strip() for l in ocr_text.split('\n') if l.strip()]
    
    # Day mapping
    day_map = {
        'MON': 'mon', 'MONDAY': 'mon',
        'TUE': 'tue', 'TUESDAY': 'tue',
        'WED': 'wed', 'WEDNESDAY': 'wed',
        'THU': 'thu', 'THURSDAY': 'thu',
        'FRI': 'fri', 'FRIDAY': 'fri',
        'SAT': 'sat', 'SATURDAY': 'sat',
        'SUN': 'sun', 'SUNDAY': 'sun'
    }
    
    # Patterns
    time_re = re.compile(
        r'(Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:day)?\s*(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})',
        re.I
    )
    code_re = re.compile(r'\b(C0?MP\d{3,4}|GCAP\d+|GE\d+)\b', re.I)
    room_re = re.compile(r'\b(T\d{1,2}-\d{3}|T\d{3}|C\d{3}|E\d{3}|\d{2}-\d{3})\b', re.I)
    teacher_re = re.compile(r'\b(Dr|Prof|Mr|Ms)\.?\s*[A-Za-z]+', re.I)
    
    # Step 1: Find all lines that contain time patterns (these are anchor lines)
    time_lines = []
    for i, line in enumerate(lines):
        if time_re.search(line):
            time_lines.append(i)
    
    print(f'[Parser] Found {len(time_lines)} lines with time patterns')
    
    if not time_lines:
        # No time patterns found, try to parse as a simple list
        print('[Parser] No time patterns found, trying alternative parsing...')
        return parse_simple_list(lines, day_map)
    
    # Step 2: For each time line, extract course info
    courses = {}
    
    for ti in time_lines:
        line = lines[ti]
        print(f'[Parser] Processing time line {ti}: {line[:80]}...')
        
        # Extract time
        m = time_re.search(line)
        if not m: continue
        
        day_str = m.group(1).upper()
        day = day_map.get(day_str, day_str.lower())
        start = "%02d:%02d" % (int(m.group(2)), int(m.group(3)))
        end = "%02d:%02d" % (int(m.group(4)), int(m.group(5)))
        
        # Try to extract other info from this line
        code = ''
        room = ''
        teacher = ''
        
        code_m = code_re.search(line)
        if code_m: code = code_m.group(1).upper()
        
        room_m = room_re.search(line)
        if room_m: room = room_m.group(0).upper()
        
        teacher_m = teacher_re.search(line)
        if teacher_m: teacher = teacher_m.group(0).strip()
        
        # Try to extract course name (improved heuristic)
        # Strategy 1: If code and time are both present, name is between them
        name = ''
        if code and time_re.search(line):
            # Find the position of code and time
            code_pos = line.find(code)
            time_pos = time_re.search(line).start()
            if code_pos < time_pos:
                # Name is between code and time
                between = line[code_pos + len(code):time_pos].strip()
                # Remove category (MR, CORE, etc.) and teacher
                between = re.sub(r'^\s*(MR|ME|CORE|ELEC|FREE)\s+', '', between, flags=re.I)
                # 删除完整教师名：Dr./Prof./Mr./Ms. + 英文名 + 大写姓氏（如 "Dr.Welpeng ZHOU"）
                between = re.sub(r'\s*(?:Dr|Prof|Mr|Ms)\.?\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+[A-Z]{2,}\s*', ' ', between, flags=re.I)
                between = ' '.join(between.split()).strip()  # Normalize whitespace
                if len(between) > 3:
                    name = between
        
        # Strategy 2: If name not found, try to extract from the whole line
        if not name:
            # Remove time, code, room, teacher, numbers, category from line
            name_line = line
            name_line = time_re.sub('', name_line)
            name_line = code_re.sub('', name_line)
            name_line = room_re.sub('', name_line)
            name_line = teacher_re.sub('', name_line)
            # 额外清理教师姓氏（大写字母串，跟在头衔后面的）
            name_line = re.sub(r'\s+[A-Z]{2,}\b', '', name_line)
            name_line = re.sub(r'\b\d+\.?\d*\b', '', name_line)
            name_line = re.sub(r'\b(MR|ME|CORE|ELEC|FREE)\b', '', name_line, flags=re.I)
            name_line = ' '.join(name_line.split()).strip()
            if len(name_line) > 3:
                name = name_line
        
        # Strategy 3: If name still not found, look at nearby lines (for table format)
        if not name and ti > 0:
            # Look at previous lines (could be course name)
            for j in range(max(0, ti-4), ti):
                prev_line = lines[j]
                # Skip if it's a time line, code, room, or category
                if time_re.search(prev_line): continue
                if code_re.search(prev_line): 
                    if not code: code = code_re.search(prev_line).group(1).upper()
                    continue
                if room_re.search(prev_line): continue
                if re.match(r'^\s*(MR|ME|CORE|ELEC|FREE)\s*$', prev_line, re.I): continue
                # Assume it's a course name
                if len(prev_line) > 3 and not prev_line[0].isdigit():
                    if not name: name = prev_line
                    break
        
        # Extract room (improved: also look at nearby lines for table format)
        if not room:
            # Look at next line (for table format, room might be on the next line)
            if ti + 1 < len(lines):
                next_line = lines[ti + 1]
                room_m = room_re.search(next_line)
                if room_m: room = room_m.group(0).upper()
        
        # Extract teacher (improved: also look at nearby lines)
        if not teacher:
            # Look at nearby lines
            for j in range(max(0, ti-2), min(len(lines), ti+3)):
                line2 = lines[j]
                teacher_m = teacher_re.search(line2)
                if teacher_m:
                    teacher = teacher_m.group(0).strip()
                    break
        
        if not name:
            print(f'[Parser] WARNING: Could not extract name for time line {ti}')
            continue
        
        # Create course record
        key = (name, code)
        if key not in courses:
            courses[key] = {
                'name': name,
                'code': code,
                'teacher': teacher,
                'times': []
            }
        
        courses[key]['times'].append({
            'day': day,
            'start_time': start,
            'end_time': end,
            'location': room
        })
        
        print(f'[Parser] Found: {name} @ {day} {start}-{end} in {room} (teacher: {teacher})')
    
    # Step 3: Build final results (one record per course, with all time slots)
    for key, course in courses.items():
        # Use the first time slot for the main record
        # (frontend should handle displaying multiple time slots)
        first_time = course['times'][0]
        results.append({
            'name': course['name'],
            'code': course['code'],
            'teacher': course['teacher'],
            'day': first_time['day'],
            'start_time': first_time['start_time'],
            'end_time': first_time['end_time'],
            'location': first_time['location'],
            'all_times': course['times']  # Include all time slots
        })
    
    print(f'[Parser] Total courses found: {len(results)}')
    return results


def parse_simple_list(lines, day_map):
    """Fallback parser for simple list format."""
    results = []
    # Try to find lines that look like course names (capitalized words)
    for line in lines:
        if len(line) > 5 and line[0].isupper() and not line[0].isdigit():
            # Might be a course name
            results.append({
                'name': line,
                'day': 'mon',
                'start_time': '09:00',
                'end_time': '10:00',
                'location': '',
                'teacher': '',
                'code': ''
            })
    return results


def parse_courses(ocr_text):
    lo = ocr_text.lower()
    is_sec = any(m in lo for m in ['sections','course category','course code','units','remark'])
    has_dt = bool(re.search(r'(?:mon|tue|wed|thu|fri)\s*\d{1,2}:\d{2}', lo))
    if is_sec or has_dt:
        r = parse_sections(ocr_text)
        if r: return r
    r = parse_timetable(ocr_text)
    print(f'[Parser] Timetable: {len(r)} courses')
    return r


def parse_timetable(text):
    results = []; lines = text.split('\n')
    rr = re.compile(r'\b(T\d{1,2}-\d{3}|T\d{3}|C\d{3}|\d{2}-\d{3})\b', re.I)
    def ep(l):
        m = re.search(r'(\d{1,2}):(\d{2})', l)
        if not m: return None
        h = int(m.group(1))
        return 1 if h<10 else 2 if h<12 else 3 if h<14 else 4 if h<16 else 5 if h<18 else 6 if h<20 else 7
    def cn(r):
        s = rr.sub('', r.strip()); s = re.sub(r'\d:\d[-–—]\d:\d','',s); s = re.sub(r'\b\d{4}\b','',s)
        s = re.sub(r'\(100[12]\)','',s); s = re.sub(r'\b(Workshop|Perspective|Semester)\b','',s,re.I)
        # 删除教师名：Dr./Prof./Mr./Ms. + 英文名 + 大写姓氏
        s = re.sub(r'\s*(?:Dr|Prof|Mr|Ms)\.?\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+[A-Z]{2,}\s*', ' ', s, flags=re.I)
        s = ' '.join(s.split()).strip().strip('–— ()[]（）.,;:/')
        p = s.split()
        while len(p)>1 and len(p[0])<=2: p=p[1:]
        return ' '.join(p)
    ic = lambda p,c: (c[:4].lower() if len(c)>=4 else '') in ('in ','of ','for ','and ','on ','to ','the ')
    cp=None; buf=[]; sn=set()
    sw={'mon','tue','wed','thu','fri','sat','monday','tuesday','wednesday','thursday','friday','saturday'}
    for line in lines:
        l=line.strip()
        if not l or len(l)<2: continue
        lo=l.lower()
        p=ep(l)
        if p is not None: cp=p;buf=[];continue
        if lo in sw or lo=='time':buf=[];continue
        if re.match(r'^(My Timetable|Semester)$',l):continue
        if 'Semester:' in l:continue
        rm=rr.search(l)
        if rm:
            loc=rm.group(1).upper()
            cands=list(buf)
            ex=rr.sub(' ',l).strip()
            if ex and len(ex)>2:cands.append(ex)
            mg=[]
            for c in cands:
                if mg and ic(mg[-1],c):mg[-1]+=' '+c
                else:mg.append(c)
            nc=[(cn(m),len(cn(m))) for m in mg]
            bn=max(nc,key=lambda x:x[1])[0] if nc else ''
            if bn and len(bn)>3 and bn not in sn:
                sn.add(bn);results.append({'name':bn,'day':'mon','period':cp or 1,'location':loc})
            buf=[];continue
        buf.append(l)
        if len(buf)>2:buf.pop(0)
    return results


class OCRHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200); self.send_header('Access-Control-Allow-Origin','*')
        self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers','Content-Type'); self.end_headers()
    def do_GET(self):
        if self.path == '/health': self.send_json({'ok':True,'server':'ocr_server'})
        elif self.path == '/status': self.send_json(get_status())
        else: self.send_error(404)
    def do_POST(self):
        if self.path != '/ocr': self.send_json({'error':f'Unknown path: {self.path}'},404);return
        cl=int(self.headers.get('Content-Length',0)); body=self.rfile.read(cl)
        try: data=json.loads(body)
        except: self.send_json({'error':'Invalid JSON'},400);return
        b64=data.get('image','')
        if not b64: self.send_json({'error':'No image data'},400);return
        if ',' in b64: b64=b64.split(',',1)[1]
        try: img=base64.b64decode(b64)
        except: self.send_json({'error':'Invalid base64'},400);return
        if not HAS_TESSERACT and not BAIDU_API_KEY:
            self.send_json({'error':'No OCR engine.','status':get_status()},500);return
        try:
            rt=ocr_image(img)
            if not rt: self.send_json({'error':'OCR returned no text.','raw_text':'','courses':[],'count':0});return
            cs=parse_courses(rt)
            self.send_json({'raw_text':rt,'courses':cs,'count':len(cs)})
        except Exception as e:
            self.send_json({'error':str(e),'trace':traceback.format_exc()},500)
    def send_json(self,d,s=200):
        self.send_response(s); self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin','*'); self.end_headers()
        self.wfile.write(json.dumps(d,ensure_ascii=False).encode())
    def log_message(self,f,*a): print(f"[OCR] {a[0]}")

def main():
    st=get_status()
    print(f"BNBU OCR Server @ {HOST}:{PORT}")
    print(f"  Python:{st['python'].split()[0]}  Pillow:{'OK' if st['pil'] else '-'}  Tesseract:{st['tesseract_path']}  Baidu:{'OK' if st['baidu_ocr'] else '-'}")
    server=HTTPServer((HOST,PORT),OCRHandler)
    print("Ready..."); server.serve_forever()

if __name__ == '__main__': main()
