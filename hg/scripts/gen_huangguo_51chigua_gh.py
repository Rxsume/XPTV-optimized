#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub 版黄果+51吃瓜 JSON 生成器（huangguo_51chigua_gh）

跑在这台 VM 上，每小时由 cron 重跑一次（cron 另建，本脚本不设 cron）。
输出：~/workspace/your_files/babybus-patched/huangguo_51chigua_gh.json
（加密包裹 {"v":1,"enc":"<base64>"}，不是明文；T3 在手机上解开再用。
密钥固定，与 T3 共用，见下方 GH_ENC_MASTER_HEX；不要换钥匙。）

数据来源（均已实测）：
- 黄果：公开接口 POST {base}/api/public/request（明文，无需加密）取列表；
  详情页 /video/<id>/ 的 videoInitialData 取 trackingEpisodes（集数以此为准）；
  播放逐集走加密通道 dx150-edge-v1（P-256 ECDH + HKDF-SHA256 + AES-256-GCM，
  逻辑直接复用 babybus-patched/huangguo.py 的 Spider 类，下方只做实例化）。
- 51吃瓜：HTML 抓取，复用 babybus-patched/huangguo_51chigua.py 的 _cg_* 方法；
  详情一次抓取即拿全部集数直链 m3u8。
- 封面：两家封面都是 AES-CBC 加密字节，原 URL 无法直接显示；统一走
  wulii 解密代理 https://huangguo.wulii.de5.net/?token=<redacted>&url=<urlencoded 原地址>。

用法：python3 gen_huangguo_51chigua_gh.py
环境变量（可选）：GH_DATA_JSON（输出 JSON 路径）、GH_REPORT_JSON（报告路径）；
缺省时保持本机绝对路径。GitHub Actions 里由 workflow 传入仓库内相对路径。
依赖：requests / pyquery / pycryptodome（本机已装）。
"""
import base64
import hashlib
import concurrent.futures
import html
import json
import os
import re
import sys
import threading
import time
import traceback
from datetime import datetime, timezone, timedelta
from urllib.parse import quote, unquote, urlparse, parse_qs

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---- FongMi base.spider 最小桩（两个复用模块都需要）----
import types
_base_pkg = types.ModuleType('base')
_base_pkg.__path__ = []
_spider_mod = types.ModuleType('base.spider')


class _StubSpider:
    def getProxyUrl(self):
        return 'http://127.0.0.1:1234/proxy?do=py'

    def e64(self, s):
        return base64.urlsafe_b64encode(str(s).encode()).decode().rstrip('=')

    def d64(self, s):
        s = str(s or '')
        return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4)).decode()


_spider_mod.Spider = _StubSpider
_base_pkg.spider = _spider_mod
sys.modules['base'] = _base_pkg
sys.modules['base.spider'] = _spider_mod

import huangguo as _hgmod            # noqa: E402  （加密通道 dx150-edge-v1）
import huangguo_51chigua as _mgmod   # noqa: E402  （51吃瓜 _cg_*）
from pyquery import PyQuery as pq    # noqa: E402

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
HG_MIRRORS = [
    'https://6d2155.dmsbazvt.cc',
    'https://zju.dmsbazvt.cc',
    'https://jlu.dmsbazvt.cc',
    'https://nju.dmsbazvt.cc',
    'https://hust.dmsbazvt.cc',
    'https://huangguoai.com',
]
CG_HOSTS = [
    'https://boom.ennpaywf.cc',
    'https://battle.ennpaywf.cc',
    'https://budget.ennpaywf.cc',
    'https://51cg1.com',
]
WULII = 'https://huangguo.wulii.de5.net/?token=<redacted>&url='
OUT_JSON = os.environ.get('GH_DATA_JSON',
                          '/home/hatch/workspace/your_files/babybus-patched/huangguo_51chigua_gh.json')
REPORT_JSON = os.environ.get('GH_REPORT_JSON',
                             '/home/hatch/workspace/ghana-gh/last_run_report.json')

# ---- 数据加密（与 T3 共用一把固定密钥，不要换钥匙）----
# 输出到 GitHub 的 JSON 是加密包裹 {"v":1,"enc":"<base64>"}，不是明文。
# 主密钥 32 字节 hex；实际 XOR 用其 SHA-256（32 字节），与 T3 里的有效密钥
# 0e6d318b6d7349aa6a9b19d3e5d28e7322bb03327d5ef2a17db9c30d362ff19c 一致。
# T3 在手机上 base64 解码后逐字节 XOR 还原再解析；防随手翻看，不是防有钥匙的破解。
GH_ENC_MASTER_HEX = "6cffbbb54b9a19bafd262c03bdcf178fd076d2e79c4172a75087b886eede7915"
_GH_ENC_KEY = hashlib.sha256(bytes.fromhex(GH_ENC_MASTER_HEX)).digest()


def _encrypt_envelope(raw_utf8: bytes) -> dict:
    xored = bytes(b ^ _GH_ENC_KEY[i % len(_GH_ENC_KEY)] for i, b in enumerate(raw_utf8))
    return {"v": 1, "enc": base64.b64encode(xored).decode()}

# 并发与时限（黄果加密取链是耗时大头）
HG_DETAIL_WORKERS = 8
HG_EP_WORKERS = 12          # 每个 worker 一个独立 edge 会话，单会话内串行（≤3 并发要求内）
CG_WORKERS = 6
EP_SOFT_DEADLINE_SEC = 660  # 取链阶段软时限 11 分钟，超时后剩余集数跳过（视频已有集数照常落盘）

LOG_LOCK = threading.Lock()


def log(msg):
    with LOG_LOCK:
        print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


# ---------------------------------------------------------------- 黄果：公开接口
def hg_public(path, timeout=15):
    """公开接口，按镜像顺序试（某站报 status 0 内容下线时换站重试）。

    返回 data 字典；全部镜像失败则抛 ValueError。
    """
    last_err = None
    for base in HG_MIRRORS:
        try:
            r = requests.post(
                base + '/api/public/request',
                json={'method': 'GET', 'path': path, 'body': '', 'content_type': ''},
                headers={'Content-Type': 'application/json', 'User-Agent': UA,
                         'Referer': base + '/', 'Origin': base,
                         'X-HG-Public-JSON': '1'},
                timeout=timeout)
            j = r.json()
            if int(j.get('status') or 0) in (1, 200) and isinstance(j.get('data'), dict):
                return j['data'], base
            last_err = ValueError(f'{base} status={j.get("status")} msg={j.get("msg")}')
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise last_err or ValueError('hg_public: all mirrors failed')


def hg_items(data):
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


def hg_row_video(row):
    """列表行 -> (vid, title, cover_raw, desc, episode_count_hint)；拿不到 id 返回 None。"""
    if not isinstance(row, dict):
        return None
    vid = row.get('id') or row.get('video_id') or ''
    if not vid:
        return None
    vid = str(vid)
    cover = (row.get('cover') or row.get('posterUrl') or row.get('posterPath')
             or row.get('poster') or '')
    cover = html.unescape(str(cover)).replace('\\/', '/').strip()
    if cover.startswith('//'):
        cover = 'https:' + cover
    try:
        ep_hint = int(row.get('episode_count') or row.get('total_episodes') or 0)
    except Exception:  # noqa: BLE001
        ep_hint = 0
    return {
        'vid': vid,
        'title': str(row.get('title') or '').strip(),
        'cover': cover,
        'desc': str(row.get('description') or ''),
        'ep_hint': ep_hint,
    }


def hg_list_paged(route, want, size, label, extra=''):
    """按 size 分页拉满 want 条（不足则有多少拿多少）。返回视频行列表。"""
    out, seen = [], set()
    page = 1
    while len(out) < want and page <= 4:
        q = f'page={page}&size={size}' + (f'&{extra}' if extra else '')
        path = f'{route}?{q}'
        try:
            data, _ = hg_public(path)
        except Exception as e:  # noqa: BLE001
            log(f'  HG list {label} p{page} FAIL: {e}')
            break
        rows = hg_items(data)
        if not rows:
            break
        for row in rows:
            v = hg_row_video(row)
            if v and v['vid'] not in seen:
                seen.add(v['vid'])
                out.append(v)
                if len(out) >= want:
                    break
        # 注意：不要用 len(rows) < size 判断结束——/api/ranks/hot 请求 size=30 也只回 20 条/页，
        # 只有空页才算取完；页数上限 4 页兜底。
        page += 1
    log(f'  HG list {label}: {len(out)} 条（目标 {want}）')
    return out


def wulii_cover(raw):
    """原封面 URL -> wulii 解密代理 URL；raw 为空返回空串。"""
    raw = html.unescape(str(raw or '')).replace('\\/', '/').strip()
    if not raw:
        return ''
    if raw.startswith('//'):
        raw = 'https:' + raw
    elif not raw.startswith(('http://', 'https://')):
        raw = 'https://6d2155.dmsbazvt.cc/' + raw.lstrip('/')
    if 'wulii' in raw:
        return raw
    return WULII + quote(raw, safe='')


# ---------------------------------------------------------------- 黄果：详情 + 加密取链
def hg_pick_host():
    """挑一个公开接口可用的镜像（首页 + public 探针）。"""
    for base in HG_MIRRORS:
        try:
            r = requests.get(base, headers={'User-Agent': UA}, timeout=5)
            if r.status_code == 200 and ('videoInitialData' in r.text or '黄果短剧' in r.text):
                data, _ = hg_public('/api/videos?page=1&size=1')
                if data:
                    return base
        except Exception:  # noqa: BLE001
            continue
    return HG_MIRRORS[0]


def hg_detail(vid, host):
    """详情页 -> {title, desc, cover_raw, episodes:[int]}；失败返回 None。

    集数以 videoInitialData.trackingEpisodes 的 key 为准；a[data-ep-id] 为辅。
    """
    last_err = None
    for base in [host] + [h for h in HG_MIRRORS if h != host]:
        try:
            r = requests.get(f'{base}/video/{vid}/', headers={'User-Agent': UA}, timeout=12)
            if r.status_code != 200:
                last_err = ValueError(f'HTTP {r.status_code}')
                continue
            m = re.search(r'<script[^>]*id="videoInitialData"[^>]*>([\s\S]*?)</script>',
                          r.text, re.I)
            if not m:
                last_err = ValueError('no videoInitialData')
                continue
            data = json.loads(m.group(1).strip())
            if str(data.get('id')) != str(vid):
                last_err = ValueError('id mismatch')
                continue
            eps = set()
            te = data.get('trackingEpisodes') or {}
            for k in te.keys():
                try:
                    n = int(k)
                    if 1 <= n <= 10000:
                        eps.add(n)
                except Exception:  # noqa: BLE001
                    pass
            if not eps:
                for mm in re.finditer(r'data-ep-id=["\'](\d+)["\']', r.text):
                    try:
                        n = int(mm.group(1))
                        if 1 <= n <= 10000:
                            eps.add(n)
                    except Exception:  # noqa: BLE001
                        pass
            if not eps:
                try:
                    ep = int(data.get('ep') or 1)
                    eps.add(ep)
                except Exception:  # noqa: BLE001
                    pass
            poster = str(data.get('posterSrc') or data.get('coverSrc')
                         or data.get('cover') or '')
            return {
                'title': str(data.get('title') or '').strip(),
                'desc': str(data.get('description') or ''),
                'cover': html.unescape(poster).replace('\\/', '/').strip(),
                'episodes': sorted(eps),
            }
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise last_err or ValueError('detail failed')


def make_hg_edge_worker(host):
    """一个独立 edge 会话（不调 init，避免每次探站；只配必需属性）。"""
    sp = _hgmod.Spider()
    sp.proxies = {}
    sp.headers = {
        'User-Agent': UA,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Connection': 'keep-alive',
        'Cache-Control': 'no-cache',
        'Origin': host,
        'Referer': host + '/',
    }
    sp.host = host
    sp._edge_sessions = {}
    sp._edge_openings = {}
    sp._edge_slots = 3
    return sp


# ---------------------------------------------------------------- 51吃瓜
def make_cg_spider(host):
    sp = _mgmod.Spider()
    sp.proxies = {}
    sp.cg_host = host
    sp.cg_base = host
    sp.cg_site = host
    sp.cg_headers = {
        'User-Agent': UA,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Connection': 'keep-alive',
        'Cache-Control': 'no-cache',
        'Origin': host,
        'Referer': host + '/',
    }
    sp.hg_host = HG_MIRRORS[0]
    sp.hg_base = HG_MIRRORS[0]
    sp.hg_site = HG_MIRRORS[0]
    sp.headers = dict(sp.cg_headers)
    sp._edge_sessions = {}
    sp._edge_openings = {}
    sp._edge_slots = 3
    return sp


def cg_pick_host():
    for base in CG_HOSTS:
        try:
            r = requests.get(base, headers={'User-Agent': UA}, timeout=5)
            if r.status_code == 200 and len(pq(r.text)('article')) > 0:
                return base
        except Exception:  # noqa: BLE001
            continue
    return CG_HOSTS[0]


def raw_cover_from_proxy_url(proxy_url):
    """FongMi 代理 URL（...?url=<quoted>&type=img）-> 原封面 URL；解析失败原样返回。"""
    u = str(proxy_url or '').strip()
    if not u:
        return ''
    try:
        q = parse_qs(urlparse(u).query)
        if q.get('url'):
            return unquote(q['url'][0])
    except Exception:  # noqa: BLE001
        pass
    return u


def clean_cg_desc(text):
    """去掉 _cg_detail 里 [a=cr:{...}/]标题[/a] 的标记，只留标题文字。"""
    s = str(text or '')
    s = re.sub(r'\[a=cr:.*?/\]', '', s)
    s = s.replace('[/a]', ' ')
    return ' '.join(s.split())


def cg_folder_subs(sp, path):
    """合集页展开为子视频 [{vod_id, vod_name, vod_pic}]。

    两路：① 旧 getfod（h2/p 配对，mrdg 合集页适用）；
    ② <a class="btn btn-primary" href="/archives/...">（whhj/qghj 合集页适用，
    按钮文字即子视频标题）。两路都空才返回 []。
    """
    subs = []
    try:
        subs = sp.getfod(path) or []
    except Exception:  # noqa: BLE001
        subs = []
    if subs:
        return subs
    try:
        url = path if path.startswith('http') else f"{sp.cg_base}{path}"
        r = requests.get(url, headers=sp.cg_headers, timeout=12)
        if r.status_code != 200:
            return []
        d = pq(r.text)
        seen = set()
        for a in d('a.btn.btn-primary').items():
            href = (a.attr('href') or '').strip()
            if not href.startswith('/archives/'):
                continue
            href = href.split('#')[0].split('?')[0]
            if not href.endswith('/'):
                href += '/'
            if href in seen:
                continue
            seen.add(href)
            title = ' '.join((a.text() or '').split())
            subs.append({'vod_id': href, 'vod_name': title, 'vod_pic': '', 'vod_remarks': ''})
    except Exception:  # noqa: BLE001
        return []
    return subs


# ---------------------------------------------------------------- 主流程
def main():
    t_start = time.time()
    now = datetime.now(timezone(timedelta(hours=8)))
    stats = {
        'started_at': now.isoformat(),
        'hg': {'lists': {}, 'videos': 0, 'episodes': 0, 'episodes_hevc_fallback': 0,
               'episodes_failed': 0, 'videos_dropped_no_episodes': [], 'videos_detail_fallback': []},
        'cg': {'lists': {}, 'videos': 0, 'episodes': 0, 'episodes_failed': 0,
               'videos_dropped_no_episodes': [], 'videos_detail_failed': []},
        'missing': [],
        'notes': [],
    }

    # ---------- 黄果 ----------
    log('== 黄果：挑站 ==')
    hg_host = hg_pick_host()
    log(f'  HG host: {hg_host}')

    HG_TOPICS = [
        ('hot-aiduanju', '精品高分'),
        ('luanlun-aiduanju', '家庭伦理'),
        ('paranormal-aiduanju', '灵异诡事'),
        ('xiaoyuan—qingcun', '校园青春'),
        ('AImori-chongsheng', 'AI末世重生'),
        ('baihe-nvtong', '百合女同'),
        ('xiangcun-AIchengren', '乡村AI成人'),
        ('gufen-wuxiachuanyue', '古风穿越'),
        ('xianxia-qihuanyaoxiai', '仙侠奇幻'),
    ]
    HG_CATS = [
        ('ai-mogai', 'AI魔改'),
        ('ai-huanlian', 'AI换脸'),
        ('ai-manju', '漫剧'),
        ('ai-duanju', '短剧'),
    ]

    log('== 黄果：拉列表 ==')
    hg_lists = {}       # cat_id -> [vid]
    hg_meta = {}        # vid -> row info
    hg_order = []       # vid 按优先级（热门/最新/专题/题材）
    spec = []

    hot_rows = hg_list_paged('/api/ranks/hot', 30, 30, 'hg:hot')
    latest_rows = hg_list_paged('/api/videos', 30, 30, 'hg:latest', extra='sort=new')
    spec.append(('hg:hot', '黄果·热门', hot_rows))
    spec.append(('hg:latest', '黄果·最新', latest_rows))
    spec.append(('hg:rank', '黄果·排行榜', hot_rows))
    for slug, name in HG_TOPICS:
        rows = hg_list_paged(f'/api/topics/{quote(slug)}', 15, 15, f'hg:topic:{slug}')
        spec.append((f'hg:topic:{slug}', f'黄果·{name}', rows))
    for route, name in HG_CATS:
        rows = hg_list_paged(f'/api/videos/category/{route}', 15, 15, f'hg:cat:{route}')
        spec.append((f'hg:cat:{route}', f'黄果·{name}', rows))

    for cat_id, _name, rows in spec:
        ids = []
        for v in rows:
            vid = v['vid']
            ids.append(f'hg:{vid}')
            if vid not in hg_meta:
                hg_meta[vid] = v
                hg_order.append(vid)
        hg_lists[cat_id] = ids
        stats['hg']['lists'][cat_id] = len(ids)
        if len(ids) == 0:
            stats['missing'].append(f'{cat_id}：0 条（接口报内容已下线或无数据）')

    log(f'  HG 去重后视频：{len(hg_order)} 部')
    if len(hg_lists.get('hg:hot', [])) < 30:
        stats['notes'].append('黄果热门/排行榜接口只回 20 条且翻页内容重复（p1=p2），实测共 20 条，拿不到 30 条')

    # ---------- 黄果：详情（并行） ----------
    log('== 黄果：拉详情（取 trackingEpisodes 集数） ==')
    hg_detail_map = {}

    def _fetch_detail(vid):
        try:
            d = hg_detail(vid, hg_host)
            return vid, d, None
        except Exception as e:  # noqa: BLE001
            return vid, None, str(e)[:120]

    with concurrent.futures.ThreadPoolExecutor(max_workers=HG_DETAIL_WORKERS) as ex:
        futs = {ex.submit(_fetch_detail, vid): vid for vid in hg_order}
        done = 0
        for fut in concurrent.futures.as_completed(futs):
            vid, d, err = fut.result()
            done += 1
            if d:
                hg_detail_map[vid] = d
            else:
                row = hg_meta[vid]
                n = row['ep_hint'] if row['ep_hint'] > 0 else 0
                if n > 0:
                    # 详情页失败：用列表行的 episode_count 兜底 1..N（已在报告里注明）
                    hg_detail_map[vid] = {'title': row['title'], 'desc': row['desc'],
                                      'cover': row['cover'], 'episodes': list(range(1, n + 1)),
                                      '_fallback': True}
                    stats['hg']['videos_detail_fallback'].append(f'hg:{vid}（{err}，用列表 episode_count={n} 兜底）')
                else:
                    stats['hg']['videos_dropped_no_episodes'].append(f'hg:{vid}（详情失败：{err}，且无 episode_count）')
            if done % 40 == 0:
                log(f'  HG 详情 {done}/{len(hg_order)}')
    log(f'  HG 详情完成：{len(hg_detail_map)} 部有集数，丢弃 {len(stats["hg"]["videos_dropped_no_episodes"])} 部')

    # ---------- 黄果：逐集加密取链（多会话并行） ----------
    log('== 黄果：逐集加密取链（12 会话并行） ==')
    ep_jobs = []  # (vid, ep)
    for vid in hg_order:
        d = hg_detail_map.get(vid)
        if not d:
            continue
        for ep in d['episodes']:
            ep_jobs.append((vid, ep))
    log(f'  待取集数：{len(ep_jobs)} 集')
    ep_deadline = time.time() + EP_SOFT_DEADLINE_SEC
    hg_ep_url = {}          # (vid, ep) -> url
    hg_ep_hevc = set()      # hevc 兜底的 (vid, ep)
    hg_ep_failed = []       # (vid, ep, err)
    ep_counter = {'done': 0, 'ok': 0, 'fail': 0, 'skipped_deadline': 0}
    ep_lock = threading.Lock()
    # 每个 worker 线程一个 edge 实例
    _tls = threading.local()

    def _ep_worker():
        if not hasattr(_tls, 'sp'):
            _tls.sp = make_hg_edge_worker(hg_host)
        return _tls.sp

    def _fetch_ep(job):
        vid, ep = job
        if time.time() > ep_deadline:
            with ep_lock:
                ep_counter['skipped_deadline'] += 1
            return
        sp = _ep_worker()
        try:
            st = sp._edge_play_streams(vid, ep)
            url = st.get('main') or ''
            if not url and st.get('hevc'):
                url = st['hevc']
                with ep_lock:
                    hg_ep_hevc.add((vid, ep))
            if url:
                with ep_lock:
                    hg_ep_url[(vid, ep)] = url
                    ep_counter['ok'] += 1
            else:
                with ep_lock:
                    ep_counter['fail'] += 1
                    hg_ep_failed.append((vid, ep, 'empty streams'))
        except Exception as e:  # noqa: BLE001
            with ep_lock:
                ep_counter['fail'] += 1
                hg_ep_failed.append((vid, ep, str(e)[:100]))
        finally:
            with ep_lock:
                ep_counter['done'] += 1
                if ep_counter['done'] % 500 == 0:
                    log(f'  HG 取链 {ep_counter["done"]}/{len(ep_jobs)} ok={ep_counter["ok"]} fail={ep_counter["fail"]}')

    with concurrent.futures.ThreadPoolExecutor(max_workers=HG_EP_WORKERS) as ex:
        list(ex.map(_fetch_ep, ep_jobs))
    log(f'  HG 取链完成：ok={ep_counter["ok"]} fail={ep_counter["fail"]} '
        f'超时跳过={ep_counter["skipped_deadline"]} hevc兜底={len(hg_ep_hevc)}')
    stats['hg']['episodes'] = ep_counter['ok']
    stats['hg']['episodes_hevc_fallback'] = len(hg_ep_hevc)
    stats['hg']['episodes_failed'] = ep_counter['fail'] + ep_counter['skipped_deadline']
    if ep_counter['skipped_deadline']:
        stats['notes'].append(f"黄果取链触及 {EP_SOFT_DEADLINE_SEC}s 软时限，跳过 "
                              f"{ep_counter['skipped_deadline']} 集（题材类靠后，优先保热门/最新/专题）")

    # 组装黄果 videos（0 集的视频不进 videos，其在 lists 里的引用也去掉）
    hg_videos = {}
    for vid in hg_order:
        d = hg_detail_map.get(vid)
        if not d:
            continue
        eps = []
        for ep in d['episodes']:
            url = hg_ep_url.get((vid, ep))
            if url:
                eps.append({'name': f'第{ep}集', 'url': url})
        if not eps:
            stats['hg']['videos_dropped_no_episodes'].append(f'hg:{vid}（{len(d["episodes"])} 集全取不到）')
            continue
        cover_raw = d.get('cover') or hg_meta[vid]['cover']
        hg_videos[f'hg:{vid}'] = {
            'id': f'hg:{vid}',
            'source': 'hg',
            'title': d.get('title') or hg_meta[vid]['title'],
            'cover': wulii_cover(cover_raw) or wulii_cover(hg_meta[vid]['cover']),
            'desc': d.get('desc') or hg_meta[vid]['desc'],
            'episode_count': len(eps),
            'episodes': eps,
        }
    for cat_id in list(hg_lists):
        hg_lists[cat_id] = [i for i in hg_lists[cat_id] if i in hg_videos]
    stats['hg']['videos'] = len(hg_videos)
    log(f'  HG 入库：{len(hg_videos)} 部，{sum(len(v["episodes"]) for v in hg_videos.values())} 集')

    # ---------- 51吃瓜 ----------
    log('== 51吃瓜：挑站 + 首页分类 ==')
    cg_host = cg_pick_host()
    log(f'  CG host: {cg_host}')
    cg_sp_main = make_cg_spider(cg_host)
    classes = cg_sp_main._collect_classes(pq(requests.get(cg_host, headers={'User-Agent': UA}, timeout=12).text))
    # _collect_classes 已按路径去重（同路径保留最短名）；再按 slug 去重一次
    cg_cats = []
    seen_slug = set()
    for c in classes:
        path = str(c.get('type_id') or '')
        m = re.fullmatch(r'/category/([^/]+)/?', path)
        if not m:
            continue
        slug = m.group(1)
        if slug in seen_slug:
            continue
        seen_slug.add(slug)
        cg_cats.append((slug, str(c.get('type_name') or slug)))
    log(f'  CG 分类：{len(cg_cats)} 个：{", ".join(s for s, _ in cg_cats)}')
    if len(cg_cats) != 26:
        stats['notes'].append(f'51吃瓜首页实际取到 {len(cg_cats)} 个 /category/ 分类（任务说 26 个，以实测为准，全部都做）')

    log('== 51吃瓜：拉各分类列表 ==')
    cg_lists = {}
    cg_items = {}   # path -> {'title','cover_raw'}（列表页信息，供详情失败时兜底）
    cg_order = []   # video path 按分类顺序
    for slug, name in cg_cats:
        cat_id = f'cg:cat:{slug}'
        vids, seen = [], set()
        items_seen = 0  # 列表项计数（合集 folder 算 1 项，展开后子视频不计入 15 条）
        for page in (1, 2, 3):
            try:
                res = cg_sp_main._cg_category(f'/category/{slug}/', page, None, None)
            except Exception as e:  # noqa: BLE001
                log(f'  CG list {cat_id} p{page} FAIL: {e}')
                break
            items = res.get('list') or []
            if not items:
                break
            for it in items:
                if items_seen >= 15:
                    break
                raw_id = str(it.get('vod_id') or '')
                if not raw_id:
                    continue
                items_seen += 1
                if raw_id.endswith('@folder'):
                    # 合集页：展开为多个子视频
                    folder_path = raw_id[:-len('@folder')]
                    try:
                        subs = cg_folder_subs(cg_sp_main, folder_path)
                    except Exception as e:  # noqa: BLE001
                        log(f'  CG folder {folder_path} FAIL: {e}')
                        subs = []
                    for sub in subs:
                        p = str(sub.get('vod_id') or '').strip()
                        if p.startswith(('http://', 'https://')):
                            p = '/' + p.split('/', 3)[-1] if p.count('/') >= 3 else p
                            if not p.startswith('/archives/'):
                                continue
                        if p and p not in seen:
                            seen.add(p)
                            vids.append(p)
                            cg_items[p] = {'title': str(sub.get('vod_name') or ''),
                                           'cover_raw': raw_cover_from_proxy_url(sub.get('vod_pic') or '')}
                    continue
                p = raw_id
                if p not in seen:
                    seen.add(p)
                    vids.append(p)
                    cg_items[p] = {'title': str(it.get('vod_name') or ''),
                                   'cover_raw': raw_cover_from_proxy_url(it.get('vod_pic') or '')}
            if items_seen >= 15:
                break
            if len(items) < 5:
                break
        # 安全阀：单分类展开后最多 40 部视频
        vids = vids[:40]
        cg_lists[cat_id] = [f'cg:{p}' for p in vids]
        stats['cg']['lists'][cat_id] = len(vids)
        for p in vids:
            if p not in cg_order:
                cg_order.append(p)
        log(f'  CG list {cat_id}：{len(vids)} 条')
        if len(vids) == 0:
            stats['missing'].append(f'{cat_id}：0 条')
    log(f'  CG 去重后视频：{len(cg_order)} 部')

    # ---------- 51吃瓜：详情（并行，一次抓全部集数直链） ----------
    log('== 51吃瓜：拉详情（一次拿全部集数） ==')
    cg_tls = threading.local()

    def _cg_worker():
        if not hasattr(cg_tls, 'sp'):
            cg_tls.sp = make_cg_spider(cg_host)
        return cg_tls.sp

    def _fetch_cg(path):
        """返回 (path, video_dict|None, err|None, sub_paths|None)。

        无播链时再试 getfod 展开：有些分类（如 whhj 网黄合集、qghj 瓜友点播）
        的文章本身就是合集页（无 .dplayer，只有一排 btn-primary 子链接），
        需展开成子视频再逐个取详情。
        """
        sp = _cg_worker()
        try:
            res = sp._cg_detail([path])
            lst = res.get('list') or []
            if lst:
                v = lst[0]
                eps = []
                for idx, part in enumerate((v.get('vod_play_url') or '').split('#'), start=1):
                    if '$' not in part:
                        continue
                    _nm, url = part.split('$', 1)
                    url = url.strip()
                    if url:
                        eps.append({'name': f'第{idx}集', 'url': url})
                if eps:
                    cover_raw = raw_cover_from_proxy_url(v.get('vod_pic') or '') or cg_items.get(path, {}).get('cover_raw', '')
                    return path, {
                        'title': str(v.get('vod_name') or '').strip() or cg_items.get(path, {}).get('title', ''),
                        'cover': cover_raw,
                        'desc': clean_cg_desc(v.get('vod_content') or ''),
                        'episodes': eps,
                    }, None, None
            # 无播链：试合集展开
            try:
                subs = cg_folder_subs(sp, path)
            except Exception as e:  # noqa: BLE001
                return path, None, f"无播链且合集展开失败：{e}"[:120], None
            sub_paths = []
            for sub in subs or []:
                p = str(sub.get('vod_id') or '').strip()
                if p.startswith(('http://', 'https://')):
                    p = '/' + p.split('/', 3)[-1] if p.count('/') >= 3 else p
                    if not p.startswith('/archives/'):
                        continue
                if p:
                    sub_paths.append(p)
                    if p not in cg_items:
                        cg_items[p] = {'title': str(sub.get('vod_name') or ''),
                                       'cover_raw': raw_cover_from_proxy_url(sub.get('vod_pic') or '')}
            if sub_paths:
                return path, None, 'folder', sub_paths[:10]  # 单合集最多展开 10 子视频
            return path, None, str(res.get('msg') or '未找到有效視頻源')[:100], None
        except Exception as e:  # noqa: BLE001
            return path, None, str(e)[:120], None

    def _store_cg(path, d):
        cg_videos[f'cg:{path}'] = {
            'id': f'cg:{path}',
            'source': 'cg',
            'title': d['title'],
            'cover': wulii_cover(d['cover']),
            'desc': d['desc'],
            'episode_count': len(d['episodes']),
            'episodes': d['episodes'],
        }

    cg_videos = {}
    cg_expanded = {}  # parent path -> [成功入库的子视频 path]
    with concurrent.futures.ThreadPoolExecutor(max_workers=CG_WORKERS) as ex:
        futs = {ex.submit(_fetch_cg, p): p for p in cg_order}
        done = 0
        pending_subs = []  # 待取详情的子视频 path
        for fut in concurrent.futures.as_completed(futs):
            path, d, err, subs = fut.result()
            done += 1
            if d:
                _store_cg(path, d)
            elif subs:
                cg_expanded[path] = subs
                for p in subs:
                    if p not in cg_order and p not in pending_subs:
                        pending_subs.append(p)
            else:
                stats['cg']['videos_detail_failed'].append(f'cg:{path}（{err}）')
                stats['cg']['videos_dropped_no_episodes'].append(f'cg:{path}（详情失败：{err}）')
            if done % 50 == 0:
                log(f'  CG 详情 {done}/{len(cg_order)}')
        # 第二遍：取展开的子视频详情
        if pending_subs:
            log(f'  CG 合集展开：{len(cg_expanded)} 个合集，共 {len(pending_subs)} 子视频待取详情')
            futs2 = {ex.submit(_fetch_cg, p): p for p in pending_subs}
            done2 = 0
            for fut in concurrent.futures.as_completed(futs2):
                path, d, err, subs2 = fut.result()
                done2 += 1
                if d:
                    _store_cg(path, d)
                else:
                    stats['cg']['videos_detail_failed'].append(f'cg:{path}（合集子视频，{err}）')
                    stats['cg']['videos_dropped_no_episodes'].append(f'cg:{path}（合集子视频详情失败：{err}）')
                if done2 % 50 == 0:
                    log(f'  CG 子视频详情 {done2}/{len(pending_subs)}')
    for cat_id in list(cg_lists):
        rebuilt = []
        for vid in cg_lists[cat_id]:
            path = vid[3:]  # 去掉 'cg:' 前缀
            if path in cg_expanded:
                rebuilt.extend(f'cg:{p}' for p in cg_expanded[path]
                               if f'cg:{p}' in cg_videos)
            elif vid in cg_videos:
                rebuilt.append(vid)
        cg_lists[cat_id] = rebuilt[:40]  # 单分类最多 40 部视频
    stats['cg']['videos'] = len(cg_videos)
    stats['cg']['episodes'] = sum(len(v['episodes']) for v in cg_videos.values())
    log(f'  CG 入库：{len(cg_videos)} 部，{stats["cg"]["episodes"]} 集')

    # ---------- 组装 ----------
    categories = []
    cat_names = {}
    for cat_id, name, _rows in spec:
        cat_names[cat_id] = name
    for slug, name in cg_cats:
        cat_names[f'cg:cat:{slug}'] = f'51吃瓜·{name}'
    ordered_cat_ids = ['hg:hot', 'hg:latest', 'hg:rank'] \
        + [f'hg:topic:{s}' for s, _ in HG_TOPICS] \
        + [f'hg:cat:{r}' for r, _ in HG_CATS] \
        + [f'cg:cat:{s}' for s, _ in cg_cats]
    for cid in ordered_cat_ids:
        categories.append({'id': cid, 'name': cat_names.get(cid, cid),
                           'source': 'hg' if cid.startswith('hg:') else 'cg'})
    lists = {}
    for cid in ordered_cat_ids:
        lists[cid] = hg_lists.get(cid, cg_lists.get(cid, []))
    videos = {}
    videos.update(hg_videos)
    videos.update(cg_videos)
    out = {
        'generated_at': now.isoformat(),
        'generator': 'huangguo_51chigua_gh',
        'categories': categories,
        'lists': lists,
        'videos': videos,
    }
    # 输出写加密包裹（与明文 JSON 同结构，只是包了一层 XOR+base64）
    raw = json.dumps(out, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    env = _encrypt_envelope(raw)
    tmp_path = OUT_JSON + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(env, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp_path, OUT_JSON)
    elapsed = time.time() - t_start
    size = os.path.getsize(OUT_JSON)
    import hashlib
    md5 = hashlib.md5(open(OUT_JSON, 'rb').read()).hexdigest()
    stats.update({
        'finished_at': datetime.now(timezone(timedelta(hours=8))).isoformat(),
        'elapsed_sec': round(elapsed, 1),
        'out_path': OUT_JSON,
        'out_bytes': size,
        'out_md5': md5,
        'categories_total': len(categories),
        'categories_hg': len([c for c in categories if c['source'] == 'hg']),
        'categories_cg': len([c for c in categories if c['source'] == 'cg']),
        'videos_total': len(videos),
        'episodes_total': sum(len(v['episodes']) for v in videos.values()),
    })
    # hg failed-episode 明细太多，只留前 20 条进报告
    stats['hg']['episodes_failed_sample'] = [
        f'hg:{v} 第{ep}集（{e}）' for v, ep, e in hg_ep_failed[:20]]
    with open(REPORT_JSON, 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    log('== 完成 ==')
    log(f'  输出：{OUT_JSON} {size} 字节 md5={md5}')
    log(f'  耗时：{elapsed:.1f}s 分类 {len(categories)}（黄果 {stats["categories_hg"]} / 吃瓜 {stats["categories_cg"]}）'
        f' 视频 {len(videos)}（黄果 {len(hg_videos)} / 吃瓜 {len(cg_videos)}）'
        f' 总集数 {stats["episodes_total"]}（黄果 {stats["hg"]["episodes"]} / 吃瓜 {stats["cg"]["episodes"]}）')
    log(f'  报告：{REPORT_JSON}')
    return stats


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
