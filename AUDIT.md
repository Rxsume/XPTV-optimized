# 检查报告（2026-11-12）：XPTV js/py/CMS 全量核查

核查范围：上游 fangkuia/XPTV 的 `js/` 爬虫脚本（48 个）、备用配置 `fun.json` 引用的 py 爬虫（27 个）、
`fun.json` 里 39 个 CMS 采集接口，以及 51 吃瓜 ECONNREFUSED 的原因。
结论：js 全部语法通过；py 有 7 处坏链已处理；CMS 接口 9 个死链（10 个站）已删；51 吃瓜是用户侧网络问题，不是代码问题。
`fun.json` 最终 72 站。

## 1. js 爬虫（48 个，全部 syntax OK）

`node --check` 全部通过。48 个文件已备份到本仓库 `js/`，另加 clun.top 的 `xp天堂.js`（15,504 字节，验证通过），共 49 个。

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

20 个正常（下载到且 `python -m py_compile` 通过），已备份到本仓库 `py/`（共 23 个，含下面修好的 3 个）。

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

注意：`fun.json` 最初下载时，clun.top 对默认 Python 爬虫 UA 返回 403，必须带浏览器 UA 才能拿到——
如果你的播放器也拿不到 py 文件，检查它的请求头。

## 3. CMS 采集接口（fun.json 里 39 个，36 个不重复地址）

每个接口都实测 2 次（带浏览器 UA）。27 个返回 200 且是有效 CMS JSON/XML，正常。
下面 9 个地址（10 个站）每次都失败，找不到可用的替代地址，已从 `fun.json` 里删掉：

| 站 | 接口 | 现象 |
|---|---|---|
| AIvin | http://lbapiby.com/api.php/provide/vod/at/xml | 502 ×2 |
| 色猫（第 2 个） | https://api.maozyapi.com/inc/apijson_vod.php | 523 ×2（源站不可达） |
| 松视开通3、性爱 | https://api.sexnguon.com/api.php/provide/vod/ | 连接直接被断开 ×2 |
| *抖阴视频 | https://www.888dav.com/api.php/provide/vod/ | 连接直接被断开 ×2 |
| zp066 | https://api.apilyzy.com/api.php/provide/vod/at/xml/ | 连接直接被断开 ×2 |
| 易看 | https://api.yikanapi.com/api.php/provide/vod/ | 连接直接被断开 ×2 |
| 奶茶 | https://caiji.naichaapi.com/inc/api.php | 连接直接被断开 ×2 |
| 探探 | https://apittzy.com/api.php/provide/vod/ | 连接直接被断开 ×2 |
| 523专场 | https://523zyw.me/inc/apijson_vod.php | 连接直接被断开 ×2 |

`fun.json` 的 csp 内置站（csp_GM、csp_JavBus 等 16 个）没有 URL 可测，播放器内部解析，全部保留。

## 4. 51 吃瓜 ECONNREFUSED（用户手机截图）

- 爬虫 key 是 `chigua`（51吃瓜），上游 `https://51cg1.com`，在我这边测是 HTTP 200，站没挂。
- 报错是服务器去抓上游时 TCP 层直接被拒（ECONNREFUSED），还没走到列表解析，**不是优化版代码的问题，原版一样**。
- 最可能的原因：你服务器那边的网络/分流。51cg1.com 这类站大概率要走 VPN，走了 DIRECT（被墙）或挂掉的节点就会这样。
- 自查：在跑 XPTV 服务器的机器上 `curl -I https://51cg1.com/`，再检查分流里 51cg1.com 走哪个组。

## 5. 上游 index.js 是否变了（用户 17:40 发来 index.js.md5）

用户发来的 `index.js.md5` 是 `e36ded5f5b448379c265b97d5d65264e`，和上游当前 `dist/index.js.md5` 一致，
上游 `dist/index.js` 仍是 2,379,104 字节，**没有更新**，优化版（`index.optimized.js`，md5 `59644fcf18d50e5792aac2145f6b12d2`）仍是最新，不用重做。

## 本仓库内容

- `index.optimized.js`：优化版 dist/index.js（见 OPTIMIZE.md）
- `fun.json`：清理后的备用 TVBox 配置（72 站）
- `fun.original.json`：用户发来的原版 fun.json（87 站，未改动）
- `py/`：23 个已验证的 py 爬虫备份
- `js/`：49 个已验证的 js 爬虫备份（48 个上游 + xp天堂.js，上游原样，未改）
- `AUDIT.md`：本报告
