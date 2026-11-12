// 黄果+51吃瓜（GitHub版）- 映匣 (YingXia) / TVBox T3 独立无依赖扩展源
// 数据来源：GitHub Rxsume/XPTV-optimized 仓库预生成的 JSON
//   hg/cg/huangguo_51chigua_gh.json（生成器每小时刷新，播放链接现取、不怕过期）
// T3 本身不调黄果/吃瓜的站、也不调任何 Worker，只从 GitHub（gh-proxy / jsDelivr）拉 JSON，
// 播放地址直接取 JSON 里的 m3u8（视频 CDN 手机直连可达），所以不连 VPN 也能用。
//
// 集标识编码约定（detail <-> play）：
//   detail 的 vod_play_url 是单线路多集：
//     "第1集$https://hzjvlo.cn/xxx.m3u8#第2集$https://...m3u8#..."
//   即每集直接写 m3u8 URL。播放器把 URL 原样传给 play 时，play 按模板惯例直接返回它。
//   另外 play 也接受 "视频id#集序号" 形式的集标识（如 "hg:12#0"、"cg:/archives/282398/#3"），
//   从内存缓存 JSON 里查该集 URL 返回；只传视频 id 时默认第 1 集。
//   两种形式都不要调任何 API，纯本地解析。

const DATA_URLS = [
  "https://gh-proxy.org/https://raw.githubusercontent.com/Rxsume/XPTV-optimized/main/hg/cg/huangguo_51chigua_gh.json",
  "https://cdn.jsdelivr.net/gh/Rxsume/XPTV-optimized@main/hg/cg/huangguo_51chigua_gh.json"
];
const CACHE_TTL_MS = 10 * 60 * 1000; // 10 分钟
const PAGE_SIZE = 30;

let CACHE = null;      // 解析后的 JSON
let CACHE_AT = 0;      // 上次成功拉取的时间戳
let CACHE_ERR = "";    // 最近一次拉取失败的错误摘要

function clean(s) {
  return String(s === null || s === undefined ? "" : s)
    .replace(/<[^>]*>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/\s+/g, " ")
    .trim();
}

// 依次尝试 req / fetch / Widget.http.get 三个通道取文本，全部失败返回 null
async function fetchText(url) {
  let lastErr = null;
  if (typeof req === "function") {
    try {
      const res = await req(url);
      const text = res && res.content !== undefined ? res.content : res;
      if (typeof text === "string" && text.trim()) return text;
      if (text) return String(text);
    } catch (e) { lastErr = e; }
  }
  if (typeof fetch === "function") {
    try {
      const res = await fetch(url);
      if (res && typeof res.text === "function") {
        const text = await res.text();
        if (text && text.trim()) return text;
      }
    } catch (e) { lastErr = e; }
  }
  try {
    if (typeof Widget !== "undefined" && Widget && Widget.http && typeof Widget.http.get === "function") {
      const res = await Widget.http.get(url);
      const text = res && res.content !== undefined ? res.content : res;
      if (typeof text === "string" && text.trim()) return text;
      if (text) return String(text);
    }
  } catch (e) { lastErr = e; }
  CACHE_ERR = lastErr ? String(lastErr && lastErr.message ? lastErr.message : lastErr) : "empty response";
  return null;
}

// 主备两个 GitHub 地址依次拉取，任一成功且能解析成带 videos 的 JSON 即缓存
async function loadData(force) {
  const now = Date.now();
  if (!force && CACHE && now - CACHE_AT < CACHE_TTL_MS) return CACHE;
  for (let i = 0; i < DATA_URLS.length; i++) {
    try {
      const text = await fetchText(DATA_URLS[i]);
      if (!text) continue;
      const json = JSON.parse(text);
      if (!json || typeof json !== "object" || !json.videos || typeof json.videos !== "object") continue;
      if (!Array.isArray(json.categories)) json.categories = [];
      if (!json.lists || typeof json.lists !== "object") json.lists = {};
      CACHE = json;
      CACHE_AT = now;
      CACHE_ERR = "";
      return CACHE;
    } catch (e) {
      CACHE_ERR = String(e && e.message ? e.message : e);
    }
  }
  return CACHE; // 两个地址都失败：返回旧缓存（可能为 null），由调用方按空处理
}

function isDataReady() {
  return !!(CACHE && CACHE.videos && typeof CACHE.videos === "object");
}

function tipItem() {
  return {
    vod_id: "",
    vod_name: "数据拉取失败，请稍后重试",
    vod_pic: "",
    vod_remarks: CACHE_ERR ? ("GitHub 数据拉取失败: " + CACHE_ERR) : "GitHub 数据拉取失败"
  };
}

function sourceLabel(source) {
  return source === "cg" ? "51吃瓜" : "黄果";
}

function toVodCard(v) {
  if (!v || typeof v !== "object") return null;
  const remarks = [];
  remarks.push(sourceLabel(v.source));
  if (v.episode_count) remarks.push("共 " + v.episode_count + " 集");
  return {
    vod_id: String(v.id),
    vod_name: clean(v.title || v.id),
    vod_pic: String(v.cover || ""),
    vod_remarks: remarks.join(" · ")
  };
}

function listCards(ids) {
  const out = [];
  for (let i = 0; i < ids.length; i++) {
    const v = CACHE.videos[String(ids[i])];
    if (!v) continue;
    const card = toVodCard(v);
    if (card) out.push(card);
  }
  return out;
}

function pageSlice(arr, page, pageSize) {
  const p = Math.max(1, parseInt(page, 10) || 1);
  const start = (p - 1) * pageSize;
  return {
    page: p,
    pagecount: Math.max(1, Math.ceil(arr.length / pageSize)),
    total: arr.length,
    items: arr.slice(start, start + pageSize)
  };
}

export default {
  meta: {
    key: "user_huangguo_51chigua_gh",
    name: "黄果+51吃瓜（GitHub版）",
    type: 3
  },

  async init(ext) {
    await loadData(false);
  },

  async home(filter) {
    await loadData(false);
    if (!isDataReady()) {
      return JSON.stringify({
        class: [],
        filters: {},
        list: [tipItem()]
      });
    }
    const cats = (CACHE.categories || []).map(function (c) {
      return { type_id: String(c.id), type_name: String(c.name) };
    });
    return JSON.stringify({
      class: cats,
      filters: {}
    });
  },

  async homeVod(pg) {
    await loadData(false);
    if (!isDataReady()) {
      return JSON.stringify({ list: [tipItem()] });
    }
    const ids = (CACHE.lists && CACHE.lists["hg:hot"]) || [];
    const sliced = pageSlice(ids, pg, PAGE_SIZE);
    return JSON.stringify({
      page: sliced.page,
      pagecount: sliced.pagecount,
      limit: PAGE_SIZE,
      total: sliced.total,
      list: listCards(sliced.items)
    });
  },

  async category(tid, pg, filter, extend) {
    await loadData(false);
    if (!isDataReady()) {
      return JSON.stringify({
        page: parseInt(pg, 10) || 1,
        pagecount: 1,
        limit: PAGE_SIZE,
        total: 0,
        list: [tipItem()]
      });
    }
    const key = String(tid || "");
    const ids = (CACHE.lists && CACHE.lists[key]) || [];
    const sliced = pageSlice(ids, pg, PAGE_SIZE);
    return JSON.stringify({
      page: sliced.page,
      pagecount: sliced.pagecount,
      limit: PAGE_SIZE,
      total: sliced.total,
      list: listCards(sliced.items)
    });
  },

  async detail(id) {
    await loadData(false);
    if (!isDataReady()) return JSON.stringify({ list: [] });
    const v = CACHE.videos[String(id)];
    if (!v || typeof v !== "object") return JSON.stringify({ list: [] });

    const eps = Array.isArray(v.episodes) ? v.episodes : [];
    const epLines = [];
    for (let i = 0; i < eps.length; i++) {
      const ep = eps[i] || {};
      const name = clean(ep.name) || ("第" + (i + 1) + "集");
      const url = String(ep.url || "");
      if (!url) continue;
      epLines.push(name + "$" + url);
    }

    const vod = {
      vod_id: String(v.id),
      vod_name: clean(v.title || v.id),
      vod_pic: String(v.cover || ""),
      vod_remarks: v.episode_count ? ("共 " + v.episode_count + " 集") : "",
      vod_content: clean(v.desc || ""),
      vod_play_from: sourceLabel(v.source),
      vod_play_url: epLines.join("#")
    };
    return JSON.stringify({ list: [vod] });
  },

  async play(flag, id, flags) {
    const rawId = String(id || "");
    // 1) 直接就是 m3u8 URL：按模板惯例原样返回
    if (/^https?:\/\//i.test(rawId)) {
      return JSON.stringify({
        parse: 0,
        url: rawId,
        header: { "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1" }
      });
    }
    // 2) "视频id#集序号" 的集标识，或裸视频 id（默认第 1 集）：从缓存查 URL
    let videoId = rawId;
    let epIndex = 0;
    const hashPos = rawId.lastIndexOf("#");
    if (hashPos > 0) {
      videoId = rawId.slice(0, hashPos);
      const n = parseInt(rawId.slice(hashPos + 1), 10);
      if (!isNaN(n) && n >= 0) epIndex = n;
    }
    await loadData(false);
    let playUrl = "";
    if (isDataReady()) {
      const v = CACHE.videos[videoId];
      if (v && Array.isArray(v.episodes) && v.episodes[epIndex]) {
        playUrl = String(v.episodes[epIndex].url || "");
      }
    }
    return JSON.stringify({
      parse: 0,
      url: playUrl,
      header: { "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1" }
    });
  },

  async search(wd, quick, pg) {
    const kw = String(wd || "").trim();
    if (!kw) return JSON.stringify({ list: [] });
    await loadData(false);
    if (!isDataReady()) return JSON.stringify({ list: [] });
    const needle = kw.toLowerCase();
    const out = [];
    const vs = CACHE.videos;
    const keys = Object.keys(vs);
    for (let i = 0; i < keys.length; i++) {
      const v = vs[keys[i]];
      if (!v) continue;
      const title = String(v.title || "");
      if (title.toLowerCase().indexOf(needle) >= 0) {
        const card = toVodCard(v);
        if (card) out.push(card);
      }
    }
    const page = parseInt(pg, 10) || 1;
    const sliced = pageSlice(out, page, PAGE_SIZE);
    return JSON.stringify({
      page: sliced.page,
      pagecount: sliced.pagecount,
      total: sliced.total,
      list: sliced.items
    });
  }
};
