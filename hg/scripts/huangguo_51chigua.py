# -*- coding: utf-8 -*-
import base64
import hashlib
import hmac
import json
import re
import secrets
import sys
import time
from urllib.parse import urlparse, quote, unquote, urljoin

import requests
from html import unescape as html_unescape
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from pyquery import PyQuery as pq

sys.path.append('..')
from base.spider import Spider

HG_PAGE_SIZE = 30
CG_PAGE_SIZE = 90
EDGE_PROTOCOL = "dx150-edge-v1"
CF_PROXY = "https://cms-proxy-hg.gumson.workers.dev"
HG_HOSTS = {
    '6d2155.dmsbazvt.cc', 'zju.dmsbazvt.cc', 'jlu.dmsbazvt.cc',
    'nju.dmsbazvt.cc', 'hust.dmsbazvt.cc', 'huangguoai.com',
}
CG_HOSTS = [
    'https://boom.ennpaywf.cc',
    'https://battle.ennpaywf.cc',
    'https://budget.ennpaywf.cc',
    'https://51cg1.com',
]
HG_MIRRORS = [
    'https://6d2155.dmsbazvt.cc',
    'https://zju.dmsbazvt.cc',
    'https://jlu.dmsbazvt.cc',
    'https://nju.dmsbazvt.cc',
    'https://hust.dmsbazvt.cc',
    'https://huangguoai.com',
]







P256_P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff

P256_A = P256_P - 3

P256_GX = 0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296

P256_GY = 0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5

P256_N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551

def _ec_add(P, Q):
    if P is None:
        return Q
    if Q is None:
        return P
    x1, y1 = P
    x2, y2 = Q
    if x1 == x2 and (y1 + y2) % P256_P == 0:
        return None
    if P == Q:
        lam = (3 * x1 * x1 + P256_A) * pow(2 * y1, -1, P256_P) % P256_P
    else:
        lam = (y2 - y1) * pow(x2 - x1, -1, P256_P) % P256_P
    x3 = (lam * lam - x1 - x2) % P256_P
    y3 = (lam * (x1 - x3) - y1) % P256_P
    return (x3, y3)

def _ec_mul(k, P):
    R = None
    while k:
        if k & 1:
            R = _ec_add(R, P)
        P = _ec_add(P, P)
        k >>= 1
    return R

def _ecdh_generate():
    d = secrets.randbelow(P256_N - 1) + 1
    Q = _ec_mul(d, (P256_GX, P256_GY))
    pub = b'\x04' + Q[0].to_bytes(32, 'big') + Q[1].to_bytes(32, 'big')
    return d, pub

def _ecdh_derive(d, pub_bytes):
    x = int.from_bytes(pub_bytes[1:33], 'big')
    y = int.from_bytes(pub_bytes[33:65], 'big')
    S = _ec_mul(d, (x, y))
    if S is None:
        raise ValueError('ECDH shared point at infinity')
    return S[0].to_bytes(32, 'big')

def _hkdf_sha256(salt, ikm, info, length=32):
    if not salt:
        salt = b'\x00' * 32
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    out = b''
    t = b''
    counter = 1
    while len(out) < length:
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha256).digest()
        out += t
        counter += 1
    return out[:length]

def _b64url_encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')

def _b64url_decode(s):
    s = str(s or '').strip()
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))

def _aes_gcm_encrypt(key, iv, plaintext, aad):
    cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
    cipher.update(aad)
    ct, tag = cipher.encrypt_and_digest(plaintext)
    return ct + tag

def _aes_gcm_decrypt(key, iv, ct_with_tag, aad):
    ct, tag = ct_with_tag[:-16], ct_with_tag[-16:]
    cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
    cipher.update(aad)
    return cipher.decrypt_and_verify(ct, tag)

class Spider(Spider):

    def init(self, extend=""):
        if isinstance(extend, dict):
            self.proxies = extend
        elif isinstance(extend, str) and extend.strip():
            try:
                self.proxies = json.loads(extend)
            except Exception:
                self.proxies = {}
        else:
            self.proxies = {}
        self.hg_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Connection': 'keep-alive',
            'Cache-Control': 'no-cache',
        }
        self.cg_headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Connection': 'keep-alive',
            'Cache-Control': 'no-cache',
        }
        self.headers = dict(self.hg_headers)
        self.hg_host = self._hg_pick_base()
        self.hg_use_cf = self._hg_cf_ok()
        self.hg_base = self._hg_cf_base() if self.hg_use_cf else self.hg_host
        # Referer/Origin 必须 match 服务器实际看到的站：走 CF 时 Worker 转到 huangguoai.com，
        # 直连时才是镜像本身。Referer 与 Host 不一致会被公开接口拒（status 0）。
        self.hg_site = 'https://huangguoai.com' if self.hg_use_cf else self.hg_host
        self.hg_headers.update({'Origin': self.hg_site, 'Referer': self.hg_site + '/'})
        self.headers = dict(self.hg_headers)
        self.host = self.hg_host
        self.cg_host = self._cg_pick_host()
        self.cg_use_cf = self._cg_cf_ok()
        self.cg_base = self._cg_cf_base() if self.cg_use_cf else self.cg_host
        self.cg_site = 'https://boom.ennpaywf.cc' if self.cg_use_cf else self.cg_host
        self.cg_headers.update({'Origin': self.cg_site, 'Referer': self.cg_site + '/'})
        self._edge_sessions = {}
        self._edge_openings = {}
        self._edge_slots = 3

    def getName(self):
        return "🌈 黄果+51吃瓜"

    def isVideoFormat(self, url):
        path = urlparse(str(url or '')).path.lower()
        return any(path.endswith(ext) for ext in ['.m3u8', '.mp4', '.ts', '.flv', '.mkv'])

    def manualVideoCheck(self):
        return False

    def destroy(self):
        self.proxies = {}
        self._edge_sessions = {}

    # ---------------- CF 线路 ----------------

    def _hg_cf_base(self):
        return CF_PROXY + "/hg"

    def _cg_cf_base(self):
        return CF_PROXY + "/cg"

    def _hg_cf_ok(self):
        try:
            r = requests.post(self._hg_cf_base() + '/api/public/request',
                              json={'method': 'GET', 'path': '/api/videos?page=1&size=1', 'body': '', 'content_type': ''},
                              headers={'Content-Type': 'application/json', 'User-Agent': self.hg_headers['User-Agent'],
                                       'Referer': 'https://huangguoai.com/', 'Origin': 'https://huangguoai.com',
                                       'X-HG-Public-JSON': '1'},
                              proxies=self.proxies, timeout=8)
            j = r.json() if r.status_code == 200 else {}
            return int(j.get('status') or 0) in (1, 200)
        except Exception:
            return False

    def _cg_cf_ok(self):
        try:
            headers = dict(self.cg_headers)
            headers.update({'Referer': 'https://boom.ennpaywf.cc/', 'Origin': 'https://boom.ennpaywf.cc'})
            r = requests.get(self._cg_cf_base() + '/', headers=headers, proxies=self.proxies, timeout=5)
            if r.status_code != 200 or not r.text:
                return False
            d = self.getpq(r.text)
            return len(d('article')) > 0 or len(d('#index a, #archive a')) > 0
        except Exception:
            return False

    def _hg_pick_base(self):
        for url in HG_MIRRORS:
            try:
                response = requests.get(url, headers=self.hg_headers, proxies=self.proxies, timeout=5)
                if response.status_code == 200 and response.text:
                    text = response.text
                    if 'videoInitialData' in text or '黄果短剧' in text or 'huangguoai' in text:
                        try:
                            r2 = requests.post(url + '/api/public/request',
                                               json={'method': 'GET', 'path': '/api/videos?page=1&size=1', 'body': '', 'content_type': ''},
                                               headers={'Content-Type': 'application/json', 'User-Agent': self.hg_headers['User-Agent'],
                                                        'Referer': url + '/', 'Origin': url, 'X-HG-Public-JSON': '1'},
                                               proxies=self.proxies, timeout=8)
                            j2 = r2.json() if r2.status_code == 200 else {}
                            if int(j2.get('status') or 0) in (1, 200):
                                return url
                        except Exception:
                            continue
            except Exception:
                continue
        return 'https://6d2155.dmsbazvt.cc'

    def _cg_pick_host(self):
        for url in CG_HOSTS:
            try:
                response = requests.get(url, headers=self.cg_headers, proxies=self.proxies, timeout=3)
                if response.status_code == 200:
                    data = self.getpq(response.text)
                    if len(data('article')) > 0 or len(data('#index a, #archive a')) > 0:
                        return url
            except Exception:
                continue
        return "https://51cg1.com"

    @staticmethod
    def _prefix_vod(vods, prefix):
        for v in (vods or []):
            if isinstance(v, dict) and v.get('vod_id') and not str(v['vod_id']).startswith(prefix):
                v['vod_id'] = prefix + str(v['vod_id'])
        return vods

    @staticmethod
    def _prefix_class(classes, prefix, name_prefix):
        for c in (classes or []):
            if isinstance(c, dict):
                if c.get('type_id') and not str(c['type_id']).startswith(prefix):
                    c['type_id'] = prefix + str(c['type_id'])
                if c.get('type_name') and not str(c['type_name']).startswith(name_prefix):
                    c['type_name'] = name_prefix + str(c['type_name'])
        return classes

    # ---------------- FongMi 入口（双源路由） ----------------

    def homeContent(self, filter):
        try:
            hg = self._hg_home(filter)
            cg = self._cg_home(filter)
            classes = self._prefix_class(hg.get('class') or [], 'hg:', '黄果·')
            classes += self._prefix_class(cg.get('class') or [], 'cg:', '51吃瓜·')
            lst = self._prefix_vod(cg.get('list') or [], 'cg:')
            if not lst:
                lst = self._prefix_vod(hg.get('list') or [], 'hg:')
            return {'class': classes, 'list': lst}
        except Exception:
            return {'class': [], 'list': []}

    def homeVideoContent(self):
        try:
            hg = self._hg_home_video()
            cg = self._cg_home_video()
            lst = self._prefix_vod(cg.get('list') or [], 'cg:')
            if not lst:
                lst = self._prefix_vod(hg.get('list') or [], 'hg:')
            return {'list': lst}
        except Exception:
            return {'list': []}

    def categoryContent(self, tid, pg, filter, extend):
        try:
            t = str(tid or '')
            page_num = max(1, int(pg)) if str(pg).isdigit() else 1
            if t.startswith('cg:'):
                r = self._cg_category(t[3:], pg, filter, extend)
                r['list'] = self._prefix_vod(r.get('list') or [], 'cg:')
                return r
            if t.startswith('hg:'):
                r = self._hg_category(t[3:], pg, filter, extend)
                r['list'] = self._prefix_vod(r.get('list') or [], 'hg:')
                return r
            r = self._hg_category('home', pg, filter, extend)
            r['list'] = self._prefix_vod(r.get('list') or [], 'hg:')
            return r
        except Exception:
            return {'list': [], 'page': max(1, int(pg) if str(pg).isdigit() else 1), 'pagecount': 1, 'limit': HG_PAGE_SIZE, 'total': 0}

    def detailContent(self, ids):
        try:
            if not ids or not ids[0]:
                return {'list': [], 'msg': '無效的影片 ID'}
            raw = str(ids[0])
            if raw.startswith('cg:'):
                r = self._cg_detail([raw[3:]])
                r['list'] = self._prefix_vod(r.get('list') or [], 'cg:')
                return r
            if raw.startswith('hg:'):
                r = self._hg_detail([raw[3:]])
                r['list'] = self._prefix_vod(r.get('list') or [], 'hg:')
                return r
            if '/archives/' in raw or '@folder' in raw:
                r = self._cg_detail([raw])
                r['list'] = self._prefix_vod(r.get('list') or [], 'cg:')
                return r
            r = self._hg_detail([raw])
            r['list'] = self._prefix_vod(r.get('list') or [], 'hg:')
            return r
        except Exception as e:
            return {'list': [], 'msg': f'詳情頁加載失敗: {type(e).__name__}'}

    def searchContent(self, key, quick, pg="1"):
        try:
            page_num = max(1, int(pg)) if str(pg).isdigit() else 1
            hg = self._hg_search(key, quick, pg)
            cg = self._cg_search(key, quick, pg)
            lst = self._prefix_vod(hg.get('list') or [], 'hg:')
            lst += self._prefix_vod(cg.get('list') or [], 'cg:')
            return {
                'list': lst,
                'page': page_num,
                'pagecount': max(hg.get('pagecount') or 1, cg.get('pagecount') or 1),
                'limit': HG_PAGE_SIZE,
                'total': (hg.get('total') or 0) + (cg.get('total') or 0),
            }
        except Exception:
            return {'list': [], 'page': max(1, int(pg) if str(pg).isdigit() else 1), 'pagecount': 1, 'limit': HG_PAGE_SIZE, 'total': 0}

    def playerContent(self, flag, id, vipFlags):
        try:
            pid = str(id or '').strip()
            if not pid:
                return {'parse': 0, 'msg': '無效的播放網址'}
            if re.match(r'^hgh?\d+e\d+$', pid, re.I):
                return self._hg_play(flag, pid, vipFlags)
            if pid.startswith(('http://', 'https://')):
                if re.search(r'bgzzal\.cn|zhixunkeji\.xyz|ennpaywf\.cc|51cg1\.com|gyezbp\.cn', pid, re.I):
                    return self._cg_play(flag, pid, vipFlags)
                if re.search(r'hzjvlo\.cn|dmsbazvt\.cc|huangguoai', pid, re.I):
                    return self._hg_play(flag, pid, vipFlags)
                return self._cg_play(flag, pid, vipFlags)
            return self._hg_play(flag, pid, vipFlags)
        except Exception as e:
            return {'parse': 0, 'msg': f'播放失敗: {type(e).__name__}'}


    def _get(self, url, timeout=12, referer=None):
        headers = dict(self.headers)
        if referer:
            headers['Referer'] = referer
        headers['Origin'] = self.hg_site
        response = requests.get(url, headers=headers, proxies=self.proxies, timeout=timeout)
        return response

    def _post_json(self, url, obj, timeout=15, referer=None):
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': self.headers['User-Agent'],
            'Referer': referer or f"{self.hg_site}/",
            'Origin': self.hg_site,
        }
        response = requests.post(url, json=obj, headers=headers, proxies=self.proxies, timeout=timeout)
        return response

    def _hg_business(self, data):
        if not isinstance(data, dict):
            raise ValueError('business: not object')
        st = int(data.get('status') if data.get('status') is not None else data.get('code') or 0)
        if st not in (1, 200):
            msg = str(data.get('msg') or data.get('message') or '')
            raise ValueError('business status %s: %s' % (st, msg[:120]))
        if not isinstance(data.get('data'), dict):
            raise ValueError('business: no data')
        return data['data']

    def _hg_public(self, path):
        body = {'method': 'GET', 'path': path, 'body': '', 'content_type': ''}
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': self.headers['User-Agent'],
            'Referer': self.hg_site + '/',
            'Origin': self.hg_site,
            'X-HG-Public-JSON': '1',
        }
        response = requests.post(self.hg_base + '/api/public/request', json=body, headers=headers,
                                 proxies=self.proxies, timeout=15)
        data = response.json()
        return self._hg_business(data)

    @staticmethod
    def _hg_items(data):
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if isinstance(data.get('items'), list):
                return data['items']
            if isinstance(data.get('videos'), list):
                return data['videos']
            if isinstance(data.get('videos'), dict) and isinstance(data['videos'].get('items'), list):
                return data['videos']['items']
        return []

    def _hg_list(self, route, page, extra=""):
        q = "page=%d&size=%d%s" % (page, HG_PAGE_SIZE, ("&" + extra) if extra else "")
        data = self._hg_public(route + "?" + q)
        rows = self._hg_items(data)
        items, seen = [], set()
        for row in rows:
            vod = self._hg_card(row)
            if vod and vod['vod_id'] not in seen:
                seen.add(vod['vod_id'])
                items.append(vod)
        p = data.get('pagination') or {}
        pages = int(p.get('pages') or 0)
        total = int(p.get('total') or len(items))
        pagecount = (page + 1) if pages > page else (page + 1 if len(items) >= HG_PAGE_SIZE else page)
        return {'items': items, 'pagecount': max(1, pagecount), 'total': total}

    def _hg_card(self, row):
        if not isinstance(row, dict):
            return None
        vid = row.get('id') or row.get('video_id') or ''
        if not vid:
            return None
        link = row.get('detailUrl') or row.get('link') or f"/video/{vid}/"
        link = str(link).split('?')[0]
        poster = row.get('cover') or row.get('posterUrl') or row.get('posterPath') or row.get('poster') or ''
        poster = self._proxy_img(poster, self.hg_host) if poster else ''
        tags = row.get('tags') or []
        remarks = (str(row.get('score')) + '分') if row.get('score') else ('/'.join(tags) if tags else '')
        return {
            'vod_id': link,
            'vod_name': self._safe_label(row.get('title') or ''),
            'vod_pic': poster,
            'vod_remarks': self._safe_label(remarks),
            'vod_content': str(row.get('description') or ''),
            'type_name': '短剧',
        }

    def _edge_post_json(self, url, obj):
        response = self._post_json(url, obj, timeout=15)
        return response.json()

    def _edge_open_session(self, scope, base):
        local = int(time.time())
        priv, pub = _ecdh_generate()
        hello = self._edge_post_json(base + '/api/' + scope + '/request',
                                     {'v': 1, 'type': 'hello', 'client_pub': _b64url_encode(pub)})
        if (not hello or hello.get('v') != 1 or hello.get('type') != 'hello'
                or not isinstance(hello.get('server_pub'), str) or not isinstance(hello.get('salt'), str)
                or not isinstance(hello.get('sid'), str) or not isinstance(hello.get('kid'), str)):
            raise ValueError('edge hello invalid')
        shared = _ecdh_derive(priv, _b64url_decode(hello['server_pub']))
        salt = _b64url_decode(hello['salt'])
        info = '\n'.join([EDGE_PROTOCOL, scope, str(hello.get('site') or ''), hello['kid'], hello['sid']]).encode('utf-8')
        req_key = _hkdf_sha256(salt, shared, info + b'\nreq', 32)
        res_key = _hkdf_sha256(salt, shared, info + b'\nres', 32)
        return {
            'base': base, 'info': info, 'kid': hello['kid'], 'sid': hello['sid'],
            'clock_offset': int(hello.get('server_time', local)) - local,
            'expires_at': local + int(hello.get('expires_at', 0)) - int(hello.get('server_time', local)),
            'sequence': 0, 'req_key': req_key, 'res_key': res_key,
        }

    def _edge_get_session(self, scope):
        now = int(time.time())
        sess = self._edge_sessions.get(scope)
        if sess and sess['expires_at'] > now + 5 and sess['sequence'] < 16384:
            return sess
        # 按基址顺序试握手（镜像优先、主站垫底）
        hosts = [self.hg_base, self.hg_host] + [h for h in [
            'https://6d2155.dmsbazvt.cc', 'https://zju.dmsbazvt.cc', 'https://jlu.dmsbazvt.cc',
            'https://nju.dmsbazvt.cc', 'https://hust.dmsbazvt.cc', 'https://huangguoai.com',
        ] if h not in (self.hg_base, self.hg_host)]
        last_err = None
        for base in hosts:
            try:
                sess = self._edge_open_session(scope, base)
                self._edge_sessions[scope] = sess
                return sess
            except Exception as e:
                last_err = e
        raise last_err or ValueError('edge hello failed')

    def _edge_call_inner(self, request, scope):
        sess = self._edge_get_session(scope)
        sess['sequence'] += 1
        sequence = sess['sequence']
        timestamp = int(time.time()) + sess['clock_offset']
        iv = bytearray(12)
        iv[8:12] = sequence.to_bytes(4, 'big')
        iv = bytes(iv)
        plain = json.dumps({'request': request}, ensure_ascii=False).encode('utf-8')
        aad_req = '\n'.join([sess['info'].decode('utf-8'), 'req', str(sequence), str(timestamp)]).encode('utf-8')
        ct = _aes_gcm_encrypt(sess['req_key'], iv, plain, aad_req)
        result = self._edge_post_json(sess['base'] + '/api/' + scope + '/request', {
            'v': 1, 'type': 'call', 'kid': sess['kid'], 'sid': sess['sid'],
            'seq': sequence, 'ts': timestamp, 'ciphertext': _b64url_encode(ct),
        })
        if (not result or result.get('v') != 1 or result.get('type') != 'result'
                or result.get('sid') != sess['sid'] or result.get('seq') != sequence):
            raise ValueError('edge result mismatch')
        aad_res = '\n'.join([sess['info'].decode('utf-8'), 'res', str(sequence), str(timestamp)]).encode('utf-8')
        pt = _aes_gcm_decrypt(sess['res_key'], iv, _b64url_decode(result['ciphertext']), aad_res)
        inner = json.loads(pt.decode('utf-8'))
        if not inner or int(inner.get('status') or 0) != 200:
            raise ValueError('edge inner status %s' % (inner.get('status') if inner else None))
        return json.loads(base64.b64decode(inner.get('body') or '').decode('utf-8'))

    def _edge_call(self, request, scope):
        last_err = None
        for _ in range(3):
            try:
                return self._edge_call_inner(request, scope)
            except Exception as e:
                last_err = e
                self._edge_sessions.pop(scope, None)
        raise last_err or ValueError('edge call failed')

    def _edge_play_streams(self, video_id, episode):
        res = self._edge_call({'method': 'POST', 'path': '/api/videos/%s/episodes/%s/playback' % (video_id, episode)}, 'playback')
        d = (res or {}).get('data') or {}
        if str(d.get('id')) != str(video_id) or int(d.get('ep') or 0) != int(episode):
            return {'main': '', 'hevc': ''}
        if d.get('manifest_method') and d['manifest_method'] != 'GET':
            return {'main': '', 'hevc': ''}
        streams = d.get('streams') or {}
        return {'main': streams.get('main') or '', 'hevc': streams.get('hevc') or ''}

    def _edge_dynamic_search(self, keyword, page):
        q = "/api/search?q=%s&type=video&page=%d&size=30" % (quote(keyword), page)
        res = self._edge_call({'method': 'GET', 'path': q, 'body': '', 'content_type': ''}, 'dynamic')
        return res

    @staticmethod
    def _extract_video_initial_data(html):
        # 数据在 <script id="videoInitialData" type="application/json">JSON</script> 里
        m = re.search(r'<script[^>]*id="videoInitialData"[^>]*>([\s\S]*?)</script>', html, re.I)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except Exception:
                pass
        # 兜底：JS 变量赋值形式
        m = re.search(r'videoInitialData\s*=\s*(\{[\s\S]*?\})\s*;', html)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass
        return {}

    def _episode_path(self, href, video_id):
        u = str(href or '').strip()
        if not u:
            return None
        if not u.startswith(('http://', 'https://')):
            u = urljoin(self.hg_base + '/', u)
        netloc = urlparse(u).netloc
        if netloc not in HG_HOSTS:
            return None
        path = urlparse(u).path
        m = re.fullmatch(r'/video/([1-9]\d{0,18})/(?:ep-([1-9]\d{0,3})/)?', path)
        if not m:
            return None
        return {'video': m.group(1), 'ep': int(m.group(2) or 1), 'path': path}


    def _hg_home(self, filter):
        try:
            classes = [
                {'type_name': '首页推荐', 'type_id': 'home'},
                {'type_name': '最近上新', 'type_id': 'latest'},
                {'type_name': '排行榜', 'type_id': 'rank'},
                {'type_name': 'AI成人短剧', 'type_id': 'drama'},
                {'type_name': 'AI成人漫剧', 'type_id': 'comic'},
                {'type_name': '精选专题', 'type_id': 'topic'},
            ]
            # 精选专题（/api/topics）
            try:
                topics = self._hg_items(self._hg_public('/api/topics'))
                seen = set()
                for row in topics:
                    slug = str(row.get('slug') or row.get('route') or '')
                    name = str(row.get('title') or row.get('name') or '')
                    if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', slug) or not name or slug in seen:
                        continue
                    seen.add(slug)
                    classes.append({'type_name': name, 'type_id': 'topic:' + slug})
                    if len(classes) > 18:
                        break
            except Exception:
                pass
            # 题材分类（/api/site/categories/video），只保留真实有内容的（探一次，410/下线跳过）
            try:
                cats = self._hg_items(self._hg_public('/api/site/categories/video'))
                seen_c = set()
                candidates = []
                for row in cats:
                    route = str(row.get('route') or '').strip('/ ')
                    if not route or route in ('ai-duanju', 'ai-manju') or route in seen_c:
                        continue
                    if not re.fullmatch(r'[A-Za-z0-9_-]+', route) or not row.get('name'):
                        continue
                    seen_c.add(route)
                    candidates.append({'route': route, 'name': str(row['name'])})
                for cand in candidates[:6]:
                    try:
                        probe = self._hg_public('/api/videos/category/%s?page=1&size=1' % cand['route'])
                        if self._hg_items(probe):
                            classes.append({'type_name': cand['name'], 'type_id': 'category:' + cand['route']})
                    except Exception:
                        continue
            except Exception:
                pass
            data = self._hg_list('/api/videos', 1)
            return {'class': classes, 'list': data['items']}
        except Exception:
            return {'class': [], 'list': []}

    def _hg_home_video(self):
        try:
            data = self._hg_list('/api/videos', 1)
            return {'list': data['items']}
        except Exception:
            return {'list': []}

    def _hg_category(self, tid, pg, filter, extend):
        try:
            page_num = max(1, int(pg)) if str(pg).isdigit() else 1
            tid = str(tid or '')
            route, extra = '', ''
            if tid in ('home', '/', 'recommend'):
                route = '/api/videos'
            elif tid == 'latest':
                route, extra = '/api/videos', 'sort=new'
            elif tid == 'rank':
                route = '/api/ranks/hot'
            elif tid == 'drama':
                route = '/api/videos/category/ai-duanju'
            elif tid == 'comic':
                route = '/api/videos/category/ai-manju'
            elif tid == 'topic':
                topics = self._hg_items(self._hg_public('/api/topics'))
                slug = ''
                for row in topics:
                    s = str(row.get('slug') or row.get('route') or '')
                    if re.fullmatch(r'[A-Za-z0-9_-]{1,200}', s):
                        slug = s
                        break
                if not slug:
                    return {'list': [], 'page': page_num, 'pagecount': 1, 'limit': HG_PAGE_SIZE, 'total': 0}
                route = '/api/topics/' + quote(slug)
            elif tid.startswith('topic:'):
                slug = tid[6:]
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', slug):
                    return {'list': [], 'page': page_num, 'pagecount': 1, 'limit': HG_PAGE_SIZE, 'total': 0}
                route = '/api/topics/' + quote(slug)
            elif tid.startswith('category:'):
                rt = tid[9:]
                if not re.fullmatch(r'[A-Za-z0-9_-]+', rt):
                    return {'list': [], 'page': page_num, 'pagecount': 1, 'limit': HG_PAGE_SIZE, 'total': 0}
                route = '/api/videos/category/' + rt
            else:
                return {'list': [], 'page': page_num, 'pagecount': 1, 'limit': HG_PAGE_SIZE, 'total': 0}
            data = self._hg_list(route, page_num, extra)
            return {
                'list': data['items'],
                'page': page_num,
                'pagecount': data['pagecount'],
                'limit': HG_PAGE_SIZE,
                'total': data['total'],
            }
        except Exception:
            return {'list': [], 'page': max(1, int(pg) if str(pg).isdigit() else 1), 'pagecount': 1, 'limit': HG_PAGE_SIZE, 'total': 0}

    def _hg_detail(self, ids):
        try:
            if not ids or not ids[0]:
                return {'list': [], 'msg': '無效的影片 ID'}
            req_id = str(ids[0])
            m = re.search(r'(\d+)', req_id)
            if not m:
                return {'list': [], 'msg': '無效的影片 ID'}
            video_id = m.group(1)
            page_url = f"{self.hg_host}/video/{video_id}/"
            response = self._get(f"{self.hg_base}/video/{video_id}/", timeout=12)
            if response.status_code != 200:
                return {'list': [], 'msg': '頁面加載失敗'}
            html = response.text
            data = self._extract_video_initial_data(html)
            if not data or str(data.get('id')) != str(video_id):
                return {'list': [], 'msg': '詳情數據缺失'}
            episodes = {}
            for pattern, swap in [
                (r'<a\b[^>]*data-ep-id=["\'](\d+)["\'][^>]*href=["\']([^"\']+)["\'][^>]*>', False),
                (r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*data-ep-id=["\'](\d+)["\'][^>]*>', True),
            ]:
                for mm in re.finditer(pattern, html, re.I):
                    ep_num = int(mm.group(2) if swap else mm.group(1))
                    href = mm.group(1) if swap else mm.group(2)
                    if ep_num < 1 or ep_num > 10000:
                        continue
                    parsed = self._episode_path(href, video_id)
                    if parsed and parsed['video'] == str(video_id) and parsed['ep'] == ep_num:
                        episodes[ep_num] = parsed['path']
            if not episodes:
                te = data.get('trackingEpisodes') or {}
                for k in te.keys():
                    try:
                        n = int(k)
                    except Exception:
                        continue
                    if 1 <= n <= 10000:
                        episodes[n] = f"/video/{video_id}/ep-{n}/" if n > 1 else f"/video/{video_id}/"
            if not episodes:
                ep = int(data.get('ep') or 1)
                episodes[ep] = f"/video/{video_id}/ep-{ep}/" if ep > 1 else f"/video/{video_id}/"
            ordered = sorted(episodes.keys())
            poster_raw = data.get('posterSrc') or data.get('coverSrc') or ''
            poster = self._proxy_img(poster_raw) if poster_raw else ''
            if not poster:
                poster = self._proxy_img(data.get('cover') or '')
            title = str(data.get('title') or '').strip()
            desc = str(data.get('description') or '')
            main_line = '#'.join(f"第{n}集$hg{video_id}e{n}" for n in ordered)
            hevc_line = '#'.join(f"第{n}集$hgh{video_id}e{n}" for n in ordered)
            vod = {
                'vod_id': page_url,
                'vod_name': self._safe_label(title),
                'vod_pic': poster,
                'vod_content': desc,
                'vod_play_from': '黄果$$$HEVC',
                'vod_play_url': main_line + '$$$' + hevc_line,
                'type_name': '短剧',
            }
            return {'list': [vod]}
        except Exception as e:
            return {'list': [], 'msg': f'詳情頁加載失敗: {type(e).__name__}'}

    def _hg_search(self, key, quick, pg="1"):
        try:
            page_num = max(1, int(pg)) if str(pg).isdigit() else 1
            res = self._edge_dynamic_search(str(key or ''), page_num)
            rows = self._hg_items(res.get('data') or res)
            items, seen = [], set()
            for row in rows:
                vod = self._hg_card(row)
                if vod and vod['vod_id'] not in seen:
                    seen.add(vod['vod_id'])
                    items.append(vod)
            p = ((res.get('data') or {}).get('pagination') or {})
            pages = int(p.get('pages') or 0)
            total = int(p.get('total') or len(items))
            pagecount = (page_num + 1) if pages > page_num else (page_num + 1 if len(items) >= HG_PAGE_SIZE else page_num)
            return {
                'list': items,
                'page': page_num,
                'pagecount': max(1, pagecount),
                'limit': HG_PAGE_SIZE,
                'total': total,
            }
        except Exception:
            return {'list': [], 'page': max(1, int(pg) if str(pg).isdigit() else 1), 'pagecount': 1, 'limit': HG_PAGE_SIZE, 'total': 0}

    def _hg_play(self, flag, id, vipFlags):
        try:
            pid = str(id or '').strip()
            if not pid:
                return {'parse': 0, 'msg': '無效的播放網址'}
            if pid.startswith(('http://', 'https://')):
                parse = 0 if self.isVideoFormat(pid) else 1
                return {'parse': parse, 'url': pid, 'header': self.headers}
            m = re.match(r'^hgh(\d+)e(\d+)$', pid, re.I) or re.match(r'^hg(\d+)e(\d+)$', pid, re.I)
            if not m:
                return {'parse': 0, 'msg': '無效的播放標識'}
            video_id, ep = m.group(1), int(m.group(2))
            is_hevc = pid.lower().startswith('hgh')
            streams = self._edge_play_streams(video_id, ep)
            url = (streams.get('hevc') or streams.get('main')) if is_hevc else streams.get('main')
            if not url:
                return {'parse': 0, 'msg': '未找到有效視頻源'}
            return {
                'parse': 0,
                'url': url,
                'header': {'User-Agent': self.headers['User-Agent'], 'Referer': f"{self.hg_site}/video/{video_id}/"},
            }
        except Exception as e:
            return {'parse': 0, 'msg': f'播放失敗: {type(e).__name__}'}

    @staticmethod
    def _normalize_path(href):
        """把導航 href 正規化成站內路徑：完整網址只取 path，去掉 query/fragment。"""
        href = str(href or '').strip()
        if not href or href.startswith(('#', 'javascript:', 'mailto:', 'tel:', 'data:')):
            return ''
        try:
            path = unquote(urlparse(href).path or '')
        except Exception:
            return ''
        if not path:
            return ''
        if not path.startswith('/'):
            path = '/' + path
        return re.sub(r'/+', '/', path)

    def _collect_classes(self, data):
        """收集分類（依 51cg1.com 真實首頁源碼修訂）。

        真實分類的路徑全都是 /category/<slug>/；導航裡混著大量站務
        頁（/telegram.html 官方TG群、/qun.html 官方QQ群、/twitter.html
        官方推特、/share.html、/archives/... 等）與外站推廣連結。
        所以主規則只認 /category/ 路徑，從根本上排除官方TG等條目；
        同一路徑出現長短兩種名稱時（如「今日吃瓜每日更新」與
        「今日吃瓜」）保留最短的乾淨名稱。
        只有完全找不到 /category/ 連結時（其他鏡像站結構不同）才
        回退到通用導航掃描，且回退時外站連結不剝域名硬轉站內路徑、
        .html 站務頁與社群連結一律排除。
        """
        found = {}
        order = []

        def clean_name(name):
            name = ' '.join(str(name or '').split())
            name = re.sub(r'\s*[\(（]\d+[\)）]\s*$', '', name).strip()
            name = re.sub(r'(每日更新|热播推荐|熱播推薦|热门推荐)$', '', name).strip()
            return name

        def is_social(name, path):
            low_name = str(name or '').lower()
            low_path = str(path or '').lower()
            return (
                'telegram' in low_path or 'telegram' in low_name
                or 'tg群' in low_name or '官方tg' in low_name
                or '电报' in str(name or '') or '電報' in str(name or '')
                or '推特' in str(name or '') or 'twitter' in low_name
                or 'qq群' in low_name or 'github' in low_name
            )

        def add_category(name, href):
            name = clean_name(name)
            path = self._normalize_path(href)
            if not name or len(name) > 20 or name.isdigit():
                return
            if not re.fullmatch(r'/category/[^/]+/?', path):
                return
            if not path.endswith('/'):
                path += '/'
            if is_social(name, path):
                return
            if path in found:
                if len(name) < len(found[path]):
                    found[path] = name
                return
            found[path] = name
            order.append(path)

        try:
            for k in data('a').items():
                href = k.attr('href') or ''
                if '/category/' in href:
                    add_category(k.text(), href)
        except Exception:
            pass

        if order:
            return [{'type_name': found[path], 'type_id': path} for path in order]

        # ---- 回退：沒有 /category/ 路徑時的通用導航掃描 ----
        bad_names = {
            '首页', '主頁', '主页', 'Home', 'HOME', '登录', '登陸', '注册',
            '搜索', '搜尋', '上一页', '下一页', '上一頁', '下一頁',
            'github', 'GitHub', 'twitter', 'Twitter', '菜单', '菜單', '更多',
        }
        bad_parts = (
            'QQ群', 'TG群', '電報群', '电报群', '官方群', '推特', '下載APP',
            '下载APP', 'APP下载', '用戶協議', '用户协议', '隱私', '隐私',
            'DMCA', '合規', '合规', 'RSS', '訂閱', '订阅',
        )
        bad_path_parts = (
            '/search/', '/login', '/register', '/feed', '/rss', '/usr/',
            '/admin', '/action/', '/author/', '/page/', '/user/',
            '/telegram', '/twitter', '/qun', '/github',
        )
        bad_exts = (
            '.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.ico',
            '.css', '.js', '.apk', '.zip', '.xml', '.mp4', '.m3u8', '.html',
        )
        host_netloc = urlparse(str(getattr(self, 'cg_host', '') or '')).netloc

        classes = []
        seen = set()

        def add(name, href):
            name = clean_name(name)
            if not name or name in bad_names or len(name) > 20:
                return
            if name.isdigit() or any(part in name for part in bad_parts):
                return
            if is_social(name, href):
                return
            # 外站連結直接排除，不再剝掉域名變成假的站內分類
            parsed = urlparse(str(href or ''))
            if parsed.netloc and host_netloc and parsed.netloc != host_netloc:
                return
            path = self._normalize_path(href)
            if not path or path == '/' or path in seen:
                return
            low = path.lower()
            if low.endswith(bad_exts) or any(part in low for part in bad_path_parts):
                return
            seen.add(path)
            classes.append({'type_name': name, 'type_id': path})

        containers = (
            'header a', 'nav a', '.category-list a', '.nav-menu a',
            '.menu a', '.navbar a', '.site-nav a', '.main-menu a',
            '#nav a', '#menu a', '.header a',
        )
        for selector in containers:
            try:
                for k in data(selector).items():
                    add(k.text(), k.attr('href'))
            except Exception:
                continue

        return classes

    def _pagecount(self, data, page_num, has_videos=False):
        numbers = []
        try:
            for selector in ('.pagination', '.page-nav', '.pager', '.nav-links', '.pages', 'nav'):
                for link in data(selector).find('a').items():
                    text = (link.text() or '').strip()
                    if text.isdigit():
                        numbers.append(int(text))
                    href_num = re.search(r'/(?:page/)?(\d+)/?$', (link.attr('href') or '').split('?')[0])
                    if href_num and text:
                        numbers.append(int(href_num.group(1)))
                if numbers:
                    break
        except Exception:
            numbers = []
        pagecount = max(numbers) if numbers else 1
        if pagecount <= page_num and has_videos:
            try:
                if data('a:contains("下一页"), a:contains("下頁"), a:contains("Next")'):
                    pagecount = page_num + 1
            except Exception:
                pass
        return max(1, pagecount)

    def _direct_img(self, raw):
        raw = html_unescape(str(raw or '')).replace('\\/', '/').strip()
        if not raw or raw.startswith('data:'):
            return ''
        if raw.startswith('//'):
            return 'https:' + raw
        if raw.startswith(('http://', 'https://')):
            return raw
        return urljoin(f"{self.cg_host}/", raw)

    @staticmethod
    def _looks_like_chrome_img(url):
        """站點外觀圖（logo/頭像/圖標/廣告橫幅）不是封面，跳過。"""
        low = str(url or '').lower()
        return any(x in low for x in (
            'logo', 'avatar', 'icon', 'sprite', 'loading', 'placeholder',
            'blank.gif', 'default.jpg', '/usr/themes/', 'favicon',
        ))

    def _extract_cover(self, unit):
        """從一個列表單元（article）裡提取封面（依真實源碼修訂）。

        51cg1.com（Mirages 主題）的真實封面寫法是：
            <div class="blog-background"
                 x-bg-loader-url="https://pic.gyezbp.cn/.../*.jpeg">
        而且該 CDN 回傳的是 AES 加密字節（Content-Type 為
        binary/octet-stream，魔數不是 JPEG），瀏覽器靠主題 JS 解密，
        App 直接拿原始網址必然加載不出來。所以取到的封面一律走
        本機代理 type=img，由 localProxy 下載後 AES 解密再回傳
        （localProxy 已改成先認魔數：明文圖原樣回傳，不會誤傷）。

        優先級：x-bg-loader-url / z-image-loader-url（主題加載器）
        > loadBannerDirect 加密圖 > data-xkrkllgl > 懶加載屬性
        > 普通 img（跳過 zw-1.png 等佔位圖）> background-image。
        """
        raw = ''

        try:
            for attr in ('x-bg-loader-url', 'z-bg-loader-url', 'z-image-loader-url'):
                for el in unit(f'[{attr}]').items():
                    value = el.attr(attr)
                    if value and not self._looks_like_chrome_img(value):
                        raw = value
                        break
                if raw:
                    break
        except Exception:
            pass

        try:
            html = html_unescape(unit.html() or '')
        except Exception:
            html = ''

        if not raw and html:
            match = re.search(r"loadBanner\w*\(\s*['\"]([^'\"]+)['\"]", html)
            if match:
                raw = match.group(1)

        if not raw:
            try:
                for img in unit('img').items():
                    custom = img.attr('data-xkrkllgl')
                    if custom and not self._looks_like_chrome_img(custom):
                        raw = custom
                        break
                if not raw:
                    for attr in ('data-src', 'data-original', 'data-lazy-src', 'data-url', 'src'):
                        for img in unit('img').items():
                            value = img.attr(attr)
                            if value and not self._looks_like_chrome_img(value):
                                raw = value
                                break
                        if raw:
                            break
            except Exception:
                pass

        if not raw and html:
            match = re.search(r"background(?:-image)?\s*:\s*url\(['\"]?([^'\")]+)", html)
            if match and not self._looks_like_chrome_img(match.group(1)):
                raw = match.group(1)
            else:
                match = re.search(r"(https?:)?//[^\s'\"<>]+?\.(?:jpg|jpeg|png|webp|gif)", html)
                if match and not self._looks_like_chrome_img(match.group(0)):
                    raw = match.group(0)

        if not raw or str(raw).startswith('data:'):
            return ''
        return self._proxy_img(raw, self.cg_host)

    def _detail_pic(self, data):
        for selector in ('article', '.post-content', '.entry-content', '.post', '.content'):
            try:
                units = data(selector)
                if len(units):
                    pic = self._extract_cover(units.eq(0))
                    if pic:
                        return pic
            except Exception:
                continue
        return ''

    def getlist(self, data, tid=''):
        """data 為整頁 PyQuery 文件（修復版）。

        原寫法逐個遍歷 <article> 裡的 <a>，同一篇文章的封面連結
        （無標題）和標題連結（含 h2 但無封面）被拆成兩筆，只有
        標題那筆留下來且封面為空，還會產生重複。這裡以 <article>
        為一筆：連結、標題、日期、封面都在同一單元內提取並去重。
        頁面沒有 <article> 時回退到按 href 合併 <a> 的舊結構。
        """
        videos = []
        is_folder = '/mrdg' in str(tid or '')
        seen = set()

        def push(href, name, remarks, pic):
            href = str(href or '').strip()
            name = ' '.join(str(name or '').replace('\n', ' ').split())
            if not href or not name:
                return
            # <script> 的文本不是標題（例如封面腳本 loadBannerDirect(...)）
            if 'loadBanner' in name or 'function(' in name or '://' in name:
                return
            # 只有角標文字（如「熱門」圖卡）不是真標題，整筆丟棄
            if name in ('热门', '熱門', '热播', '推薦', '推荐', '置顶', '广告', 'HOT', 'NEW'):
                return
            key = href.split('#')[0]
            if key in seen:
                return
            seen.add(key)
            item = {
                'vod_id': f"{href}{'@folder' if is_folder else ''}",
                'vod_name': self._safe_label(name),
                'vod_pic': pic or '',
                'vod_remarks': self._safe_label(' '.join(str(remarks or '').split())),
                'style': {"type": "rect", "ratio": 1.33}
            }
            if is_folder:
                item['vod_tag'] = 'folder'
            videos.append(item)

        try:
            units = data('article')
        except Exception:
            units = []

        if len(units):
            for unit in units.items():
                # 跳過列表廣告位（外站推廣卡片，不是文章）
                try:
                    if len(unit('.post-card-ads, .tjtagmanager, a[data-ad_slot_key]')):
                        continue
                except Exception:
                    pass
                title_link = unit('h2 a').eq(0)
                href = title_link.attr('href')
                name = unit('h2').eq(0).text() or unit('h1, h3').eq(0).text()
                if not href:
                    # 找第一個帶文字或指向文章的連結
                    for a in unit('a').items():
                        if (a.text() or '').strip() or a('img'):
                            href = a.attr('href')
                            if href:
                                break
                if not name:
                    for a in unit('a').items():
                        if (a.text() or '').strip() and not len(a('script')):
                            name = a.text()
                            break
                remarks = (
                    unit('span[itemprop="datePublished"]').text()
                    or unit('time').eq(0).text()
                    or unit('time').eq(0).attr('datetime')
                    or unit('.post-meta, .entry-meta, .meta, .date').eq(0).text()
                )
                push(href, name, remarks, self._extract_cover(unit))
            return videos

        # 無 article 結構時：按 href 合併同一篇文章的封面連結與標題連結
        merged = {}
        order = []
        try:
            links = data('#index a, #archive a, main a, .post a, .list a')
            if not len(links):
                links = data('a')
            for k in links.items():
                href = (k.attr('href') or '').strip()
                if not href:
                    continue
                if href not in merged:
                    merged[href] = {'name': '', 'pic': '', 'remarks': ''}
                    order.append(href)
                entry = merged[href]
                text = (k('h2').text() or '').strip()
                if not text and not len(k('script')):
                    text = (k.text() or '').strip()
                if text and 'loadBanner' in text:
                    text = ''
                if text and not entry['name']:
                    entry['name'] = text
                if not entry['pic']:
                    entry['pic'] = self._extract_cover(k)
                if not entry['pic']:
                    try:
                        entry['pic'] = self._extract_cover(k.parent())
                    except Exception:
                        pass
        except Exception:
            pass
        for href in order:
            entry = merged[href]
            push(href, entry['name'], entry['remarks'], entry['pic'])
        return videos

    def getfod(self, folder_id):
        folder_id = str(folder_id or '')
        url = folder_id if folder_id.startswith('http') else f"{self.cg_base}{folder_id}"
        response = requests.get(url, headers=self.cg_headers, proxies=self.proxies, timeout=12)
        if response.status_code != 200:
            return []

        data = self.getpq(response.text)
        vdata = data('.post-content[itemprop="articleBody"]')
        if not len(vdata):
            vdata = data('article, .post-content, .entry-content').eq(0)
        for i in ['.txt-apps', '.line', 'blockquote', '.tags', '.content-tabs']:
            vdata.remove(i)

        p = vdata('p')
        videos = []
        for i, x in enumerate(vdata('h2').items()):
            c = i * 2
            name = p.eq(c).text()
            vod_id = p.eq(c)('a').attr('href') or ''
            if not vod_id or not (name or '').strip():
                continue
            pic = self._extract_cover(p.eq(c + 1)) if len(p) > c + 1 else ''
            videos.append({
                'vod_id': vod_id,
                'vod_name': self._safe_label(name),
                'vod_pic': pic,
                'vod_remarks': self._safe_label(x.text())
            })
        return videos

    def getimg(self, text):
        """兼容舊呼叫：從腳本文本裡找加密封面網址。"""
        text = html_unescape(str(text or ''))
        match = re.search(r"loadBanner\w*\(\s*['\"]([^'\"]+)['\"]", text)
        if not match:
            match = re.search(r"(https?:\\?/\\?/[^\s'\"]+?\.(?:jpg|jpeg|png|webp))", text)
        if match:
            return self._proxy_img(match.group(1), self.cg_host)
        return ''

    def _cg_home(self, filter):
        try:
            response = requests.get(self.cg_base, headers=self.cg_headers, proxies=self.proxies, timeout=10)
            if response.status_code != 200:
                return {'class': [], 'list': []}

            data = self.getpq(response.text)
            return {
                'class': self._collect_classes(data),
                'list': self.getlist(data)
            }
        except Exception:
            return {'class': [], 'list': []}

    def _cg_home_video(self):
        try:
            response = requests.get(self.cg_base, headers=self.cg_headers, proxies=self.proxies, timeout=10)
            if response.status_code != 200:
                return {'list': []}
            data = self.getpq(response.text)
            return {'list': self.getlist(data)}
        except Exception:
            return {'list': []}

    def _cg_category(self, tid, pg, filter, extend):
        try:
            page_num = max(1, int(pg)) if str(pg).isdigit() else 1
            tid = str(tid or '')

            if '@folder' in tid:
                folder_id = self._normalize_path(tid.replace('@folder', '')) or tid.replace('@folder', '')
                videos = self.getfod(folder_id)
                return {
                    'list': videos,
                    'page': page_num,
                    'pagecount': 1,
                    'limit': len(videos) or CG_PAGE_SIZE,
                    'total': len(videos)
                }

            # type_id 可能是舊版存下的完整網址，先正規化成站內路徑再拼接，
            # 否則會出現 host + https://... 的錯誤網址，分類頁必然為空。
            path = self._normalize_path(tid) or '/'
            base_url = f"{self.cg_base}{path}".rstrip('/')

            # 分頁格式不同主題不一致（/分類/2/ 與 /分類/page/2/），
            # 第一種取不到列表時自動嘗試第二種。
            if page_num > 1:
                candidate_urls = [f"{base_url}/{page_num}/", f"{base_url}/page/{page_num}/"]
            else:
                candidate_urls = [f"{base_url}/"]

            data = None
            videos = []
            for url in candidate_urls:
                response = requests.get(url, headers=self.cg_headers, proxies=self.proxies, timeout=12)
                if response.status_code != 200:
                    continue
                data = self.getpq(response.text)
                videos = self.getlist(data, path)
                if videos:
                    break

            if data is None:
                return {'list': [], 'page': page_num, 'pagecount': 1, 'limit': CG_PAGE_SIZE, 'total': 0}

            pagecount = self._pagecount(data, page_num, has_videos=bool(videos))
            return {
                'list': videos,
                'page': page_num,
                'pagecount': max(1, pagecount),
                'limit': CG_PAGE_SIZE,
                'total': max(len(videos), page_num * CG_PAGE_SIZE)
            }
        except Exception:
            return {'list': [], 'page': max(1, int(pg) if str(pg).isdigit() else 1), 'pagecount': 1, 'limit': CG_PAGE_SIZE, 'total': 0}

    def _cg_detail(self, ids):
        try:
            if not ids or not ids[0]:
                return {'list': [], 'msg': '無效的影片 ID'}

            req_id = ids[0]
            url = req_id if req_id.startswith('http') else f"{self.cg_base}{req_id}"
            response = requests.get(url, headers=self.cg_headers, proxies=self.proxies, timeout=12)

            if response.status_code != 200:
                return {'list': [], 'msg': '頁面加載失敗'}

            data = self.getpq(response.text)
            vod = {
                'vod_id': req_id,
                'vod_name': self._safe_label(data('.post-title, h1, .entry-title').eq(0).text() or '51吸瓜视频'),
                'vod_pic': self._detail_pic(data),
                'vod_play_from': '51吸瓜'
            }

            try:
                clist = []
                if data('.tags .keywords a'):
                    for k in data('.tags .keywords a').items():
                        title = k.text().strip()
                        href = k.attr('href')
                        if title and href:
                            payload = json.dumps({'id': href, 'name': title, 'type_flag': '1'}, ensure_ascii=False)
                            clist.append(f"[a=cr:{payload}/]{title}[/a]")
                vod['vod_content'] = ' '.join(clist) if clist else (data('.post-title').text() or '')
            except Exception:
                vod['vod_content'] = data('.post-title').text() or ''

            plist = []
            used_names = set()
            if data('.dplayer'):
                for c, k in enumerate(data('.dplayer').items(), start=1):
                    config_attr = k.attr('data-config')
                    if not config_attr:
                        continue
                    try:
                        config = json.loads(config_attr)
                        video_url = config.get('video', {}).get('url', '')
                        if not video_url:
                            continue

                        ep_name = ''
                        try:
                            parent = k.parents().eq(0)
                            for _ in range(3):
                                if not parent: break
                                heading = parent.find('h2, h3, h4').eq(0).text() or ''
                                heading = heading.strip()
                                if heading:
                                    ep_name = heading
                                    break
                                parent = parent.parents().eq(0)
                        except Exception:
                            ep_name = ''

                        base_name = ep_name if ep_name else f"视频{c}"
                        name = base_name
                        count = 2
                        while name in used_names:
                            name = f"{base_name} {count}"
                            count += 1
                        used_names.add(name)

                        safe_ep_name = self._safe_label(name)
                        plist.append(f"{safe_ep_name}${video_url}")
                    except Exception:
                        continue

            if plist:
                vod['vod_play_url'] = '#'.join(plist)
            else:
                return {'list': [], 'msg': '未找到有效視頻源'}

            return {'list': [vod]}
        except Exception as e:
            return {'list': [], 'msg': f'詳情頁加載失敗: {type(e).__name__}'}

    def _cg_search(self, key, quick, pg="1"):
        try:
            page_num = max(1, int(pg)) if str(pg).isdigit() else 1
            if page_num > 1:
                candidate_urls = [
                    f"{self.cg_base}/search/{quote(key)}/{page_num}/",
                    f"{self.cg_base}/search/{quote(key)}/page/{page_num}/",
                ]
            else:
                candidate_urls = [f"{self.cg_base}/search/{quote(key)}/"]

            data = None
            videos = []
            for url in candidate_urls:
                response = requests.get(url, headers=self.cg_headers, proxies=self.proxies, timeout=12)
                if response.status_code != 200:
                    continue
                data = self.getpq(response.text)
                videos = self.getlist(data)
                if videos:
                    break

            if data is None:
                return {'list': [], 'page': page_num, 'pagecount': 1, 'limit': CG_PAGE_SIZE, 'total': 0}

            pagecount = self._pagecount(data, page_num, has_videos=bool(videos))

            return {
                'list': videos,
                'page': page_num,
                'pagecount': max(1, pagecount),
                'limit': CG_PAGE_SIZE,
                'total': max(len(videos), page_num * CG_PAGE_SIZE)
            }
        except Exception:
            return {'list': [], 'page': max(1, int(pg) if str(pg).isdigit() else 1), 'pagecount': 1, 'limit': CG_PAGE_SIZE, 'total': 0}

    def _cg_play(self, flag, id, vipFlags):
        url = str(id or '').strip()
        if not url:
            return {'parse': 0, 'msg': '無效的播放網址'}

        parse = 0 if self.isVideoFormat(url) else 1

        if parse == 0 and '.m3u8' in url and self.proxies:
            url = self.proxy(url)

        return {
            'parse': parse,
            'url': url,
            'header': self.cg_headers
        }

    @staticmethod
    def _safe_label(value):
        """清理標籤文字，避免破壞 Fongmi 播放分隔符 $ 與 #"""
        return str(value or '').replace('$', '＄').replace('#', '＃').strip()

    def aesimg(self, word):
        # 已是明文圖片（魔數正確）就原樣回傳，避免對普通圖做無謂解密
        if word[:3] == b'\xff\xd8\xff' or word[:8] == b'\x89PNG\r\n\x1a\n' \
                or word[:6] in (b'GIF87a', b'GIF89a') \
                or (word[:4] == b'RIFF' and word[8:12] == b'WEBP'):
            return word
        try:
            key = b'f5d965df75336270'
            iv = b'97b60394abc2fbe1'
            cipher = AES.new(key, AES.MODE_CBC, iv)
            return unpad(cipher.decrypt(word), AES.block_size)
        except Exception:
            return word

    def getpq(self, data):
        try:
            return pq(data)
        except Exception:
            return pq(data.encode('utf-8'))

    def localProxy(self, param):
        req_type = param.get('type')
        if req_type == 'img':
            img_url = unquote(str(param.get('url') or ''))
            if not img_url:
                return [404, 'text/plain', b'']
            res = requests.get(img_url, headers=self.headers, proxies=self.proxies, timeout=10)
            content = self.aesimg(res.content)
            return [200, self._img_content_type(content, res.headers.get('Content-Type')), content]
        elif req_type == 'm3u8':
            return self.m3Proxy(param['url'])
        else:
            return self.tsProxy(param['url'])

    def proxy(self, data, type='m3u8'):
        if data and self.proxies:
            return f"{self.getProxyUrl()}&url={self.e64(data)}&type={type}"
        return data

    def m3Proxy(self, url):
        url = self.d64(url)
        ydata = requests.get(url, headers=self.headers, proxies=self.proxies, allow_redirects=False)
        data = ydata.content.decode('utf-8', errors='ignore')
        if ydata.headers.get('Location'):
            url = ydata.headers['Location']
            data = requests.get(url, headers=self.headers, proxies=self.proxies).content.decode('utf-8', errors='ignore')
        lines = data.strip().split('\n')
        last_r = url[:url.rfind('/')]
        parsed_url = urlparse(url)
        durl = f"{parsed_url.scheme}://{parsed_url.netloc}"
        iskey = True
        for index, string in enumerate(lines):
            if iskey and 'URI' in string:
                pattern = r'URI="([^"]*)"'
                match = re.search(pattern, string)
                if match:
                    lines[index] = re.sub(pattern, f'URI="{self.proxy(match.group(1), "mkey")}"', string)
                    iskey = False
                    continue
            if '#EXT' not in string:
                if 'http' not in string:
                    domain = last_r if string.count('/') < 2 else durl
                    string = domain + ('' if string.startswith('/') else '/') + string
                lines[index] = self.proxy(string, string.split('.')[-1].split('?')[0])
        data = '\n'.join(lines)
        return [200, "application/vnd.apple.mpegurl", data]

    def tsProxy(self, url):
        url = self.d64(url)
        data = requests.get(url, headers=self.headers, proxies=self.proxies, stream=True)
        return [200, data.headers.get('Content-Type', 'video/MP2T'), data.content]

    @staticmethod
    def _img_content_type(content, fallback=None):
        """依解密後字節的魔數判斷真實圖片類型（上游只回 octet-stream）。"""
        if content[:3] == b'\xff\xd8\xff':
            return 'image/jpeg'
        if content[:8] == b'\x89PNG\r\n\x1a\n':
            return 'image/png'
        if content[:6] in (b'GIF87a', b'GIF89a'):
            return 'image/gif'
        if content[:4] == b'RIFF' and content[8:12] == b'WEBP':
            return 'image/webp'
        if fallback and str(fallback).startswith('image/'):
            return fallback
        return 'image/jpeg'

    def _proxy_img(self, raw, base=None):
        raw = html_unescape(str(raw or '')).replace('\\/', '/').strip()
        if not raw:
            return ''
        if raw.startswith('//'):
            raw = 'https:' + raw
        elif not raw.startswith(('http://', 'https://')):
            raw = urljoin(f"{base or self.hg_host}/", raw)
        try:
            return f"{self.getProxyUrl()}&url={quote(raw, safe='')}&type=img"
        except Exception:
            return raw

