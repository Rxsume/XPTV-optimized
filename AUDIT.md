# 检查报告（2026-11-12）：XPTV js/py 全量核查

核查范围：上游 fangkuia/XPTV 的 `js/` 爬虫脚本（48 个）、备用配置 `fun.json` 引用的 py 爬虫（27 个）、
以及 51 吃瓜 ECONNREFUSED 的原因。结论：js 全部语法通过；py 有 7 处坏链，已修复；51 吃瓜是用户侧网络问题，不是代码问题。

## 1. js 爬虫（48 个，全部 syntax OK）

`node --check` 全部通过。48 个文件已备份到本仓库 `js/`。

上游可达性（从本机测，2026-11-12 17:40 左右，本机走 egress 代理，仅供参考——你服务器的网络不同，结果可能不一样）：
69 个不重复上游域名中：33 个返回 200；19 个连接失败（000）；13 个返回 403；3 个 404；1 个 405。

连接失败（000，很可能是死了或被挡）的：
123.666291.xyz、1.star2.cn、czzy.*.tk、ddys.mov、ddys.pro、img.externulls.com、missav.ai、
mitaotunbbx.xyz、smdyo.top、surrit.store、v.ddys.pro、v.xl01.eu.cc、vip.omii.top、wp.0v.fit、
www.gyg.la、www.gying.org、xiafan.banye.tech、xpanpan.site、yunpan8.net

返回 403（多半是 Cloudflare/反爬挡了机房 IP，换个网络可能通）：
api.agedm.io、fq.skybook.qzz.io、hanime1.me、jp.spankbang.com、lk1.supremejav.com、static.olelive.com、
supjav.com、surrit.com、video.beeg.com、www.czzymovie.com、www.tianyiso.com、www.uaa.com、www.wogg.net

返回 404/405：api.olelive.com、reading.snssdk.com、store.externulls.com、thumbs.externulls.com

**以上只作参考，没有删任何一个 js 爬虫**——你那边网络和我这边不一样，删了可能把你那边能用的也删掉。
哪个站你那边打不开，以那个站的实际报错为准。

## 2. py 爬虫（fun.json 引用 27 个）

20 个正常（下载到且 `python -m py_compile` 通过），已备份到本仓库 `py/`。

### 修好的 3 个（路径写错了）

`fun.json` 里写的是 `./py/18.py`、`./py/district.py`、`./py/jable.py`，
但 clun.top 上这 3 个文件的实际位置是 `./js/ss/`，`./py/` 下只有 clun.top 首页的错误页（返回 200 的 HTML）。
已改成正确路径，3 个文件都重新下载并编译通过：
- `py_18`：`./py/18.py` → `./js/ss/18.py`（12,639 字节）
- `py_七区`：`./py/district.py` → `./js/ss/district.py`（27,611 字节）
- `py_jable`：`./py/jable.py` → `./js/ss/jable.py`（5,716 字节）

### 删掉的 5 个（上游仓库已不存在）

`fish2018/lib` 这个 GitHub 仓库已经整个没了（API 返回 404），所以下面 5 个站的脚本永远是 404，已从 `fun.json` 里删掉：
- `色播`（sebo.js）
- `lav`（py_lav.py）
- `xhm`（py_Xhm.py）
- `pornhub`（py_Phb.py）
- `推特`（py_推特.py）

`fun.json` 由 87 个站减到 82 个站，修完后所有 py 引用都指向已验证的文件。
注意：`fun.json` 最初下载时，clun.top 对默认 Python 爬虫 UA 返回 403，必须带浏览器 UA 才能拿到——
如果你的播放器也拿不到 py 文件，检查它的请求头。

## 3. 51 吃瓜 ECONNREFUSED（用户手机截图）

- 爬虫 key 是 `chigua`（51吃瓜），上游 `https://51cg1.com`，在我这边测是 HTTP 200，站没挂。
- 报错是服务器去抓上游时 TCP 层直接被拒（ECONNREFUSED），还没走到列表解析，**不是优化版代码的问题，原版一样**。
- 最可能的原因：你服务器那边的网络/分流。51cg1.com 这类站大概率要走 VPN，走了 DIRECT（被墙）或挂掉的节点就会这样。
- 自查：在跑 XPTV 服务器的机器上 `curl -I https://51cg1.com/`，再检查分流里 51cg1.com 走哪个组。

## 本仓库内容

- `index.optimized.js`：优化版 dist/index.js（见 OPTIMIZE.md）
- `fun.json`：修好的备用 TVBox 配置（82 站）
- `py/`：26 个已验证的 py 爬虫备份
- `js/`：48 个已验证的 js 爬虫备份（上游原样，未改）
- `AUDIT.md`：本报告
