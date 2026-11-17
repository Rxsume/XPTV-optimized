/**
 * @title 青龙命令
 * @rule raw ^(同步cookie|同步|跑幸运星|幸运星|查任务|任务|日志|帮助|菜单)$
 * @rule raw ^日志\s+\S+$
 * @status true
 */

// 青龙命令插件：通过 QQ/微信控制青龙面板
// 前置条件：在 SillyGirl 管理后台「青龙容器」中添加青龙面板（id 从 1 开始）。
// 命令（仅管理员可用）：
//   同步cookie / 同步   - 运行 elm_cookie 任务（经 YYB 取码同步饿了么 cookie）
//   跑幸运星 / 幸运星   - 运行 elmxyx 幸运星任务
//   查任务 / 任务       - 列出青龙定时任务（最近 15 个）及状态
//   日志 <任务名关键词> - 查看某任务最近日志（取最后 15 行）
//   帮助 / 菜单         - 显示本帮助

function ql() {
  return new container.QingLong({ id: 1 });
}

function findCrons(all, keyword) {
  var list = (all && all.data) || all || [];
  if (!Array.isArray(list)) return [];
  keyword = (keyword || "").toLowerCase();
  return list.filter(function (c) {
    var name = (c.name || "") + " " + (c.command || "");
    return name.toLowerCase().indexOf(keyword) !== -1;
  });
}

function fmtCron(c) {
  var status = c.isDisabled ? "已禁用" : "启用";
  var last = c.last_running_time ? new Date(c.last_running_time * 1000).toLocaleString("zh-CN", { hour12: false }) : "未运行";
  return "[" + c.id + "] " + (c.name || c.command) + " | " + status + " | 上次: " + last;
}

function runTask(keyword, label) {
  var client = ql();
  var all = client.request("GET", "/open/crons");
  var found = findCrons(all, keyword);
  if (found.length === 0) {
    s.reply("没找到包含「" + keyword + "」的定时任务。");
    return;
  }
  var c = found[0];
  if (c.isDisabled) {
    s.reply("任务「" + (c.name || c.command) + "」已禁用，先在青龙里启用。");
    return;
  }
  client.request("PUT", "/open/crons/run", [c.id]);
  s.reply("已启动「" + (c.name || c.command) + "」(" + label + ")，稍后用「日志 " + (c.name || keyword) + "」查看结果。");
}

function listTasks() {
  var client = ql();
  var all = client.request("GET", "/open/crons");
  var list = (all && all.data) || all || [];
  if (!Array.isArray(list) || list.length === 0) {
    s.reply("青龙里没有定时任务。");
    return;
  }
  var lines = list.slice(0, 15).map(fmtCron);
  s.reply("青龙任务（前15个，共" + list.length + "个）：\n" + lines.join("\n"));
}

function showLog(keyword) {
  var client = ql();
  var all = client.request("GET", "/open/crons");
  var found = findCrons(all, keyword);
  if (found.length === 0) {
    s.reply("没找到包含「" + keyword + "」的定时任务。");
    return;
  }
  var c = found[0];
  var log = client.request("GET", "/open/crons/" + c.id + "/log");
  var text = "";
  if (typeof log === "string") text = log;
  else if (log && log.data) text = typeof log.data === "string" ? log.data : JSON.stringify(log.data);
  else text = JSON.stringify(log);
  var lines = text.split("\n").filter(function (l) { return l.trim() !== ""; });
  var tail = lines.slice(-15).join("\n");
  if (!tail) tail = "（暂无日志）";
  s.reply("「" + (c.name || c.command) + "」最近日志：\n" + tail);
}

function help() {
  s.reply("青龙命令（仅管理员）：\n同步cookie - 运行 elm_cookie 同步饿了么 cookie\n跑幸运星 - 运行 elmxyx 幸运星任务\n查任务 - 列出青龙定时任务\n日志 <关键词> - 查看某任务最近日志\n帮助 - 显示本帮助");
}

(function main() {
  if (!s.isAdmin()) {
    s.reply("只有管理员能用青龙命令。");
    return;
  }
  var msg = (s.getMsg() || "").trim();
  if (msg === "同步cookie" || msg === "同步") {
    runTask("elm_cookie", "同步cookie");
  } else if (msg === "跑幸运星" || msg === "幸运星") {
    runTask("elmxyx", "幸运星");
  } else if (msg === "查任务" || msg === "任务") {
    listTasks();
  } else if (msg.indexOf("日志") === 0) {
    var kw = msg.replace(/^日志\s*/, "").trim();
    if (!kw) { s.reply("用法：日志 <任务名关键词>，例如：日志 幸运星"); return; }
    showLog(kw);
  } else if (msg === "帮助" || msg === "菜单") {
    help();
  } else {
    help();
  }
})();
