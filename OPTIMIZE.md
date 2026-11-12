# XPTV dist/index.js 优化说明（2026-11-12）

源文件：`fangkuia/XPTV` dist/index.js（2,379,104 字节，md5 `e36ded5f5b448379c265b97d5d65264e`）
优化版：`index.optimized.js`（2,380,597 字节，只多了 1,493 字节）
补丁脚本：`/tmp/patch_xptv.py`（6 处替换，每处都是文件里唯一出现的字符串，可复现）

只改 6 处，不改任何接口和返回结构。已验证：`node --check` 通过；本地起服务，爬虫路由正常
（`/spider/huangguo/3/home` 200、`category` 200）；新增的 HTML 缓存单独做了单元测试，全部通过（见下）。

## 先说结论：原来已经不慢的地方

别被“2.4MB 打包文件”吓到，作者其实已经做了不少优化，这次没重复造轮子：

- 上游请求客户端 `Pa`（axios 实例，`re`）已有 keep-alive（`maxSockets: 16`）、gzip（`Accept-Encoding: gzip, deflate, br`）、
  cookie jar（只在第一次读盘，之后全内存）、10 秒超时、socket 断开重试（80ms 后重试 1 次）。
- 播放链路已有缓存：m3u8 包装缓存 `eq`（45 秒）、missav master 播放列表缓存 `Mc`（200 条，正结果 10 分钟）、
  TS 分片缓存（`Ke`，带命中率统计）。
- m3u8 加速（`m3u8Accel`）已有预取窗口、分片并发、按字节分段拉取等一整套配置。

所以这次只补作者没做的三块短板。

## 改了什么（6 处）

### 1. 上游连接池放大：`maxSockets 16 → 64`，`maxFreeSockets 8 → 32`
位置：`tF={keepAlive:!0,keepAliveMsecs:3e4,maxSockets:16,maxFreeSockets:8}`。
影响：同一上游域名最多同时 16 条连接。播一个视频要预取多个分片，多人同时看会排队等连接；64 条基本够个人用，仍远低于 Node 默认的不设限。
风险：低。上游并发变多，对上游压力稍大，个人服务器无感。

### 2. m3u8 加速默认值调高（只改默认值）
位置：`Yn={...window:8, rangeParts:2, maxTsCache:30, maxInflightPerSession:3, ...}` →
`window:12, rangeParts:4, maxTsCache:48, maxInflightPerSession:6`。
影响：预取窗口 8→12 个分片（提前缓冲更多，拖进度/弱网更不容易卡）；大分片并行分段 2→4（大 TS 下得更快）；
每会话缓存分片 30→48 个（回看/重播少重复拉）；每会话并发拉取 3→6 个分片（起播缓冲更快）。都在作者自己定的合法范围
内（`gxe`：window ≤64、rangeParts ≤8、maxTsCache ≤200、maxInflight ≤16），字节预算（96MB/会话、384MB 总）没动，不会爆内存。

**⚠️ 重要：这条只对新部署生效。** 运行中的设置是从数据库 `/systemSettings/m3u8Accel` 读出来覆盖默认值的，
你现在的服务器如果已经保存过设置，换文件后仍用旧值。想用新值二选一：
在设置页（`/systemSettings/m3u8Accel`）点“恢复默认”，或调 `POST /m3u8/reset`（会把 `Yn` 写回数据库）。

### 3. `ph()` 辅助请求：开 gzip、失败更快
位置：`ph()`（只在 3 处用的辅助请求，missav master m3u8 等）。
- `Accept-Encoding: identity → gzip, deflate, br`（之前强制不压缩，文本白白多传几倍）。
- 默认重试 `4 → 3` 次、默认超时 `20s → 15s`（上游挂了更快失败，不用等 80 秒才报错；已显式传参的调用点不受影响）。

### 4. 新增上游 HTML 短时缓存（这次最大的“加载”提速）
位置：紧跟 `re=Pa` 之后，包住 `re.get`（所有爬虫的统一上游请求入口）。
规则（都很保守）：
- 只缓存 GET、且是 HTML 文本（`content-type` 含 `text/html` 或内容以 `<` 开头）、状态 200、小于 2MB 的响应；
- TTL 90 秒、最多 300 条、先进先出淘汰；返回时浅拷贝一份，调用方改返回头不会污染缓存；
- **不缓存**：`responseType` 为 arraybuffer/stream 的（TS 分片、m3u8 二进制）、POST、JSON 接口。
效果：90 秒内重复打开同一个列表/详情/搜索页，直接走内存，不用再等上游（上游普遍 0.5~2 秒一次）。
代价：列表 90 秒内的新内容看不到（短剧站更新没那么快）；缓存 key 只有 URL，没带 cookie——个人单用户服务器没问题，
多用户共用且各站登录态不同时别用这版。

单元测试（`/tmp/test_cache.mjs`，mock 上游 50ms）：
首取 51ms / 1 次上游；重复取 0ms / 0 次上游；不同页正常回源；分片（arraybuffer）与 JSON 接口各调两次都不进缓存；
91 秒后过期自动重取；改返回头不污染缓存。全部通过。

## 实测记录（本机）

- `node --check` 通过；起服务后爬虫注册 19 个站，`/spider/huangguo/3/home` 200（4ms）、`category` 200。
- **黄果分类在本机返回空列表（1.5 秒），但这是本机测试环境的网络问题，不是优化版的毛病**：
  本机所有出站流量走 egress 代理（`HTTPS_PROXY`），而 `Pa` 的自定义 agent 会绕过代理直连，直连 `198.18.104.131`
  拿不到 TLS，所以报 `socket hang up`。用**原版文件在同一环境测，现象一模一样**（同样 1.51 秒、同样空列表、
  同样 `socket hang up`），对照组已验证。curl/默认 agent 走代理所以能通。你自己的服务器是直连网络，不存在这个问题，
  不用管；等部署后在你那边列表正常出就好。
- 第一版曾怀疑过 `ALPN http/1.1` 或 keep-alive 导致握手失败，实测是代理绕过问题，与之无关，已排除。

## 部署

1. 把 `index.optimized.js` 覆盖到服务器上的 `dist/index.js`（`index.config.js` 不用动，照原样放一边）。
2. 重启服务。
3. 想用第 2 条的新默认值：设置页点“恢复默认”或调 `POST /m3u8/reset`。
4. 出问题先回退：换回原版 `index.js` 即可（结构、接口都没变）。

## GitHub 加速（用户要求实测）

下 2.4MB 的 `dist/index.js`、md5 校验通过才算有效（本机在海外）：
直连 1.3~3.4 秒；**gh-proxy.org 1.6~3.3 秒（最快可用镜像）**；jsDelivr 3.0 秒；
gh-proxy.com 3.4 秒；ghproxy.net 19.8 秒；mirror.ghproxy.com、hub.gitmirror.com、ghps.cc、ghproxy.cn 均失效。
国内用优先 gh-proxy.org，备选 jsDelivr（`cdn.jsdelivr.net/gh/fangkuia/XPTV@main/dist/index.js`）。
