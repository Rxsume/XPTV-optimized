# XPTV-optimized

优化版 XPTV 服务端打包文件。源文件是 [fangkuia/XPTV](https://github.com/fangkuia/XPTV) 的
`dist/index.js`（2.38 MB 的 Fastify 服务端），本仓库只改了 6 处，不改任何接口和返回结构。

## 文件

- `index.optimized.js` — 优化后的 dist/index.js（2,380,597 字节，md5 `59644fcf18d50e5792aac2145f6b12d2`）
- `OPTIMIZE.md` — 全部改动的说明、实测记录和部署步骤

## 改了什么（摘要）

1. 上游连接池：`maxSockets 16 → 64`、`maxFreeSockets 8 → 32`
2. m3u8 加速默认值调高：`window 8→12`、`rangeParts 2→4`、`maxTsCache 30→48`、`maxInflightPerSession 3→6`
3. `ph()` 辅助请求：`Accept-Encoding` 改 gzip；默认重试 4→3 次、超时 20 秒→15 秒
4. 新增上游 HTML 短时缓存（GET + HTML + 200 + <2MB，90 秒，300 条）

详情见 [OPTIMIZE.md](OPTIMIZE.md)。

## 部署

把 `index.optimized.js` 覆盖到服务器上的 `dist/index.js`，`index.config.js` 不用动，重启服务。

注意：m3u8 加速的新默认值只对新部署生效，已保存过设置的部署需在设置页点“恢复默认”
或调 `POST /m3u8/reset`，否则仍用旧值。详见 [OPTIMIZE.md](OPTIMIZE.md)。

## 下载

- 直连：`https://raw.githubusercontent.com/Rxsume/XPTV-optimized/main/index.optimized.js`
- GitHub 加速（国内优先）：`https://gh-proxy.org/https://raw.githubusercontent.com/Rxsume/XPTV-optimized/main/index.optimized.js`
- 备选加速：`https://cdn.jsdelivr.net/gh/Rxsume/XPTV-optimized@main/index.optimized.js`

## 备用

- 备用配置（TVBox sites 配置，87 个站，39KB）：`https://clun.top/fun.json`
- 本仓库里也存了一份同样的 `fun.json`，clun.top 打不开时直接用仓库里的这份。
