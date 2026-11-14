# -*- coding: utf-8 -*-
# 黄果短剧（huangguoai）FongMi/TVBox Python 爬虫
# 由 Yingxia T3（huangguo.webox.t3.v163.js）反向转换而来，结构照 51吃瓜 chigua 爬虫。
# 站点：主站 huangguoai.com（国内直连被墙），镜像 6d2155/zju/jlu/nju/hust.dmsbazvt.cc（自动选可用站）。
# 列表/分类/专题走明文公开接口 POST /api/public/request；搜索与点播走加密通道 dx150-edge-v1
# （P-256 ECDH + HKDF-SHA256 + AES-256-GCM，纯 Python 实现 P-256，AES-GCM 用 pycryptodome）。
# 封面是 AES-CBC 加密字节（与 51吃瓜同一套 key/iv），走本机代理 localProxy 解密回图。
# 详情解析 /video/<id>/ 的 videoInitialData + a[data-ep-id] 选集。
# 视频直链 m3u8（hls.bgzzal.cn / hzjvlo.cn），带 auth_key，约 2 小时过期。

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

PAGE_SIZE = 30
EDGE_PROTOCOL = "dx150-edge-v1"

# ---------------- P-256 ECDH（纯 Python） ----------------
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

        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Connection': 'keep-alive',
            'Cache-Control': 'no-cache',
        }
        # 取得可用站點（镜像优先，主站垫底）
        self.host = self.get_working_host()
        self.headers.update({'Origin': self.host, 'Referer': f"{self.host}/"})
        # 加密会话（按 scope 分别缓存：playback / dynamic）
        self._edge_sessions = {}
        self._edge_openings = {}
        self._edge_slots = 3

    def getName(self):
        return "🌈 黄果短剧"

    def isVideoFormat(self, url):
        path = urlparse(str(url or '')).path.lower()
        return any(path.endswith(ext) for ext in ['.m3u8', '.mp4', '.ts', '.flv', '.mkv'])

    def manualVideoCheck(self):
        return False

    def destroy(self):
        self.proxies = {}
        self._edge_sessions = {}

    # ---------------- 基础请求 ----------------

    def _get(self, url, timeout=12, referer=None):
        headers = dict(self.headers)
        if referer:
            headers['Referer'] = referer
        headers['Origin'] = self.host
        response = requests.get(url, headers=headers, proxies=self.proxies, timeout=timeout)
        return response

    def _post_json(self, url, obj, timeout=15, referer=None):
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': self.headers['User-Agent'],
            'Referer': referer or f"{self.host}/",
            'Origin': self.host,
        }
        response = requests.post(url, json=obj, headers=headers, proxies=self.proxies, timeout=timeout)
        return response

    def get_working_host(self):
        """镜像站點測試：依序探測 dmsbazvt.cc 镜像，主站垫底。首页含 videoInitialData / 黄果短剧才算可用。"""
        dynamic_urls = [
            'https://6d2155.dmsbazvt.cc',
            'https://zju.dmsbazvt.cc',
            'https://jlu.dmsbazvt.cc',
            'https://nju.dmsbazvt.cc',
            'https://hust.dmsbazvt.cc',
            'https://huangguoai.com',
        ]
        for url in dynamic_urls:
            try:
                response = requests.get(url, headers=self.headers, proxies=self.proxies, timeout=5)
                if response.status_code == 200 and response.text:
                    text = response.text
                    if 'videoInitialData' in text or '黄果短剧' in text or 'huangguoai' in text:
                        # 再确认接口不是空壳：public 请求须回真 JSON
                        try:
                            r2 = self._post_json(url + '/api/public/request',
                                                 {'method': 'GET', 'path': '/api/videos?page=1&size=1', 'body': '', 'content_type': ''},
                                                 timeout=8)
                            j2 = r2.json() if r2.status_code == 200 else {}
                            if int(j2.get('status') or 0) in (1, 200):
                                return url
                        except Exception:
                            continue
            except Exception:
                continue
        return 'https://6d2155.dmsbazvt.cc'

    # ---------------- 公开接口（明文，无需加密） ----------------

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
            'Referer': self.host + '/',
            'Origin': self.host,
            'X-HG-Public-JSON': '1',
        }
        response = requests.post(self.host + '/api/public/request', json=body, headers=headers,
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
        q = "page=%d&size=%d%s" % (page, PAGE_SIZE, ("&" + extra) if extra else "")
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
        pagecount = (page + 1) if pages > page else (page + 1 if len(items) >= PAGE_SIZE else page)
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
        poster = self._proxy_img(poster) if poster else ''
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

    # ---------------- 加密通道 dx150-edge-v1 ----------------

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
        hosts = [self.host] + [h for h in [
            'https://6d2155.dmsbazvt.cc', 'https://zju.dmsbazvt.cc', 'https://jlu.dmsbazvt.cc',
            'https://nju.dmsbazvt.cc', 'https://hust.dmsbazvt.cc', 'https://huangguoai.com',
        ] if h != self.host]
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

    # ---------------- 首页 / 分类 ----------------

    def homeContent(self, filter):
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

    def homeVideoContent(self):
        try:
            data = self._hg_list('/api/videos', 1)
            return {'list': data['items']}
        except Exception:
            return {'list': []}

    def categoryContent(self, tid, pg, filter, extend):
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
                    return {'list': [], 'page': page_num, 'pagecount': 1, 'limit': PAGE_SIZE, 'total': 0}
                route = '/api/topics/' + quote(slug)
            elif tid.startswith('topic:'):
                slug = tid[6:]
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', slug):
                    return {'list': [], 'page': page_num, 'pagecount': 1, 'limit': PAGE_SIZE, 'total': 0}
                route = '/api/topics/' + quote(slug)
            elif tid.startswith('category:'):
                rt = tid[9:]
                if not re.fullmatch(r'[A-Za-z0-9_-]+', rt):
                    return {'list': [], 'page': page_num, 'pagecount': 1, 'limit': PAGE_SIZE, 'total': 0}
                route = '/api/videos/category/' + rt
            else:
                return {'list': [], 'page': page_num, 'pagecount': 1, 'limit': PAGE_SIZE, 'total': 0}
            data = self._hg_list(route, page_num, extra)
            return {
                'list': data['items'],
                'page': page_num,
                'pagecount': data['pagecount'],
                'limit': PAGE_SIZE,
                'total': data['total'],
            }
        except Exception:
            return {'list': [], 'page': max(1, int(pg) if str(pg).isdigit() else 1), 'pagecount': 1, 'limit': PAGE_SIZE, 'total': 0}

    # ---------------- 详情 ----------------

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
        base = self.host
        if not u.startswith(('http://', 'https://')):
            u = urljoin(base + '/', u)
        if not u.startswith(base):
            return None
        path = urlparse(u).path
        m = re.fullmatch(r'/video/([1-9]\d{0,18})/(?:ep-([1-9]\d{0,3})/)?', path)
        if not m:
            return None
        return {'video': m.group(1), 'ep': int(m.group(2) or 1), 'path': path}

    def detailContent(self, ids):
        try:
            if not ids or not ids[0]:
                return {'list': [], 'msg': '無效的影片 ID'}
            req_id = str(ids[0])
            m = re.search(r'(\d+)', req_id)
            if not m:
                return {'list': [], 'msg': '無效的影片 ID'}
            video_id = m.group(1)
            page_url = f"{self.host}/video/{video_id}/"
            response = self._get(page_url, timeout=12)
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

    # ---------------- 搜索 ----------------

    def searchContent(self, key, quick, pg="1"):
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
            pagecount = (page_num + 1) if pages > page_num else (page_num + 1 if len(items) >= PAGE_SIZE else page_num)
            return {
                'list': items,
                'page': page_num,
                'pagecount': max(1, pagecount),
                'limit': PAGE_SIZE,
                'total': total,
            }
        except Exception:
            return {'list': [], 'page': max(1, int(pg) if str(pg).isdigit() else 1), 'pagecount': 1, 'limit': PAGE_SIZE, 'total': 0}

    # ---------------- 播放 ----------------

    def playerContent(self, flag, id, vipFlags):
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
                'header': {'User-Agent': self.headers['User-Agent'], 'Referer': f"{self.host}/video/{video_id}/"},
            }
        except Exception as e:
            return {'parse': 0, 'msg': f'播放失敗: {type(e).__name__}'}

    # ---------------- 代理 / 封面 ----------------

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
    def _safe_label(value):
        """清理標籤文字，避免破壞 Fongmi 播放分隔符 $ 與 #"""
        return str(value or '').replace('$', '＄').replace('#', '＃').strip()

    def _proxy_img(self, raw):
        """加密封面走本機代理（AES 解密）。網址先 quote，避免 & 截斷代理參數。"""
        raw = html_unescape(str(raw or '')).replace('\\/', '/').strip()
        if not raw:
            return ''
        if raw.startswith('//'):
            raw = 'https:' + raw
        elif not raw.startswith(('http://', 'https://')):
            raw = urljoin(f"{self.host}/", raw)
        try:
            return f"{self.getProxyUrl()}&url={quote(raw, safe='')}&type=img"
        except Exception:
            return raw

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
