"""渲染：Markdown 总览表 + 静态站点。

站点是**纯静态**产物（数据内嵌，无运行时依赖），
可直接部署到 Nginx / Cloudflare Pages / GitHub Pages。
站点元信息（标题、域名、sitemap）全部取自 config，便于绑定自有域名。
"""

from __future__ import annotations

import html
import json
import os
import shutil
from datetime import datetime, timezone

from . import articles, config
from .extract import AVAIL_CN, EVENT_CN

# 活动性质的「力度权重」，用于综合评分
EVENT_WEIGHT = {
    "anniversary": 25, "black_friday": 25, "holiday": 15,
    "launch": 10, "restock": 8, "sale": 5, "regular": 0,
}


# --------------------------------------------------------------------------
# 评分与格式化
# --------------------------------------------------------------------------

def deal_score(rec: dict) -> int:
    """活动力度综合评分（0-120）。分越高越「值得看」。"""
    s = 0.0
    d = rec.get("discount") or {}
    if d.get("recurring"):
        s += 30                       # 循环/永久折扣最值钱
    pm = d.get("percent_max")
    if pm:
        s += min(pm, 70) * 0.8
    s += EVENT_WEIGHT.get(rec.get("event_type"), 0)
    if d.get("extras"):
        s += 10
    if d.get("codes"):
        s += 5
    s += (rec.get("confidence") or 0) * 10
    return int(round(s))


def annual_eur(rec: dict) -> float | None:
    """年化价格（欧元近似），仅用于排序。"""
    p = rec.get("flagship_price") or (
        (rec.get("prices") or [None])[0])
    if not p:
        return None
    mult = config.CYCLE_TO_YEAR.get(p.get("cycle"), 12.0)
    rate = config.FX_TO_EUR.get(p.get("currency"), 1.0)
    return round(p["amount"] * mult * rate, 2)


CYCLE_CN = {"year": "/年", "quarter": "/季", "month": "/月"}


def price_text(rec: dict) -> str:
    p = rec.get("flagship_price") or ((rec.get("prices") or [None])[0])
    if not p:
        return "—"
    return "%s%g%s" % (p["currency"], p["amount"],
                       CYCLE_CN.get(p.get("cycle"), ""))


def spec_text(rec: dict) -> str:
    s = rec.get("specs") or {}
    bits = []
    if s.get("vcpu"):
        bits.append("%dC" % s["vcpu"])
    if s.get("ram_gb"):
        bits.append("%dG" % int(s["ram_gb"]))
    if s.get("storage_gb"):
        st = s.get("storage_type") or ""
        if st.lower() == "unspecified":
            st = ""
        bits.append("%dG%s" % (int(s["storage_gb"]), st))
    head = "/".join(bits)
    cpu = ", ".join((rec.get("cpu_models") or [])[:2])
    # 规格与 CPU 各自可能缺失：缺一个就只显示另一个，
    # 两个都缺才用占位符，避免「— | Xeon」这种残缺拼接。
    if head and cpu:
        return "%s | %s" % (head, cpu)
    return head or cpu or "—"


def to_markdown(rows: list[dict]) -> str:
    lines = ["| 来源 | 厂商 | 活动性质 | 力度 | 配置/CPU | 旗舰价 | 状态 | 置信度 |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
            (r.get("source") or "").upper(),
            (r.get("provider") or "").replace("|", "/"),
            EVENT_CN.get(r.get("event_type"), r.get("event_type")),
            ((r.get("discount") or {}).get("strength") or "—").replace("|", "/"),
            spec_text(r).replace("|", "/"),
            price_text(r),
            AVAIL_CN.get(r.get("availability"), r.get("availability")),
            "%.2f" % (r.get("confidence") or 0),
        ))
    return "\n".join(lines)


def _load_forum_links() -> dict:
    """读取已发布到论坛的记录，返回 {thread_id: 论坛URL}。

    站点建站发生在发帖之前，所以这里读到的是**上一轮**的结果 ——
    新发的帖要等下一轮建站才出现在站点上（延迟一个周期）。

    文件不存在或损坏时返回空 dict：此时全部回退到原帖链接，
    不会出现死链。
    """
    path = str(config.FORUM_POSTED_LOG)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for tid, info in data.items():
        if isinstance(info, dict) and info.get("url"):
            out[str(tid)] = info["url"]
    return out


def enrich(records: list[dict]) -> list[dict]:
    """为展示补充派生字段。"""
    forum_links = _load_forum_links()
    for r in records:
        r["score"] = deal_score(r)
        r["annual_eur"] = annual_eur(r)
        # 论坛对应帖子的链接；没有则为 None（渲染时回退到原帖）
        r["forum_url"] = forum_links.get(str(r.get("thread_id")))
    return records


# --------------------------------------------------------------------------
# 静态站
# --------------------------------------------------------------------------

def _json_for_script(obj) -> str:
    """把 JSON 安全内嵌进 <script>，防止 </script> 逃逸。"""
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026"))


def _header_links_html() -> str:
    """渲染页头附加链接。未配置时返回空串（不影响布局）。"""
    links = getattr(config, "HEADER_LINKS", None) or []
    if not links:
        return ""
    parts = []
    for name, url in links:
        parts.append('<span><a href="%s" target="_blank" rel="noopener">%s</a>'
                     '</span>' % (html.escape(url, quote=True),
                                  html.escape(name)))
    return "\n    ".join(parts)


SITE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>@@TITLE@@</title>
<meta name="description" content="@@DESC@@">
@@CANONICAL@@
@@EXTRA_HEAD@@
<!-- 站点图标：沿用论坛的 LS 品牌图标，保持视觉一致。
     ico 供浏览器标签页（多尺寸），PNG 供 Android/PWA 与 iOS 主屏。 -->
<link rel="icon" href="favicon.ico" sizes="any">
<link rel="icon" type="image/png" sizes="192x192" href="icon-192.png">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<link rel="manifest" href="manifest.webmanifest">
<meta name="theme-color" content="#0f766e">
<style>
:root{
  --bg:#f6f8f8; --card:#fff; --ink:#16181d; --muted:#6b7280; --line:#e5e7eb;
  --accent:#0f766e; --accent-soft:#f0fdfa; --good:#059669; --warn:#d97706;
  --bad:#dc2626; --chip:#f3f4f6;
}
@media (prefers-color-scheme:dark){
  :root{ --bg:#0f1214; --card:#171c1f; --ink:#e8eaed; --muted:#9aa0a6;
         --line:#272d33; --accent:#2dd4bf; --accent-soft:#134e4a;
         --good:#34d399; --warn:#fbbf24; --bad:#f87171; --chip:#1f2529; }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans SC",sans-serif}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.wrap{max-width:1400px;margin:0 auto;padding:24px 18px 64px}
header h1{margin:0 0 6px;font-size:24px;letter-spacing:-.3px}
header p{margin:0;color:var(--muted);font-size:13px}
.meta{margin-top:10px;display:flex;flex-wrap:wrap;gap:14px;
  color:var(--muted);font-size:12px}
.meta b{color:var(--ink);font-weight:600}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:16px;margin-top:18px}
.controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
input[type=search],select{background:var(--card);color:var(--ink);
  border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:13px;
  font-family:inherit;outline:none}
input[type=search]{min-width:240px;flex:1}
input[type=search]:focus,select:focus{border-color:var(--accent)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
.chip{background:var(--chip);border:1px solid var(--line);border-radius:999px;
  padding:5px 12px;font-size:12.5px;cursor:pointer;user-select:none;
  color:var(--ink);white-space:nowrap}
.chip:hover{border-color:var(--accent)}
.chip.on{background:var(--accent);border-color:var(--accent);color:#fff}
.chip .n{opacity:.6;margin-left:5px;font-variant-numeric:tabular-nums}
.chip.on .n{opacity:.85}
label.toggle{display:inline-flex;align-items:center;gap:6px;font-size:13px;
  color:var(--ink);cursor:pointer;white-space:nowrap}
.tblwrap{overflow-x:auto;margin-top:4px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:10px 10px;text-align:left;border-bottom:1px solid var(--line);
  vertical-align:top}
th{position:sticky;top:0;background:var(--card);font-weight:600;font-size:12px;
  color:var(--muted);text-transform:uppercase;letter-spacing:.4px;z-index:1;
  cursor:pointer;user-select:none;white-space:nowrap}
th:hover{color:var(--accent)}
tr.row{cursor:pointer}
tr.row:hover{background:var(--accent-soft)}
tr.row.open{background:var(--accent-soft)}
.pv{font-weight:600}
/* 厂商名可点击跳官网：默认与正文同色，只用虚线下划线提示可点，
   避免整列变蓝影响扫读。无官网链接时渲染为纯文本，样式无差别。 */
a.pv-link{color:inherit;text-decoration:none;cursor:pointer;
  border-bottom:1px dotted var(--muted)}
a.pv-link:hover{color:var(--accent);border-bottom-color:var(--accent)}
.spec{font-variant-numeric:tabular-nums;white-space:nowrap}
.price{font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}
/* 「发布于」与「置信度」列禁止折行：
   前者如 “20 天前” 会在数字与中文之间的空格处断开；
   后者是「进度条 + 百分比」，窄列时会被拆成两行。 */
.hidesm,td.cf{white-space:nowrap}
.badge{display:inline-block;border-radius:6px;padding:2px 7px;font-size:11.5px;
  font-weight:600;white-space:nowrap}
.b-anniversary,.b-black_friday{background:#fee2e2;color:#b91c1c}
.b-holiday{background:#fef3c7;color:#b45309}
.b-launch{background:#dbeafe;color:#1d4ed8}
.b-restock{background:#d1fae5;color:#047857}
.b-sale{background:#ede9fe;color:#6d28d9}
.b-regular{background:var(--chip);color:var(--muted)}
@media (prefers-color-scheme:dark){
  .b-anniversary,.b-black_friday{background:#450a0a;color:#fca5a5}
  .b-holiday{background:#451a03;color:#fcd34d}
  .b-launch{background:#172554;color:#93c5fd}
  .b-restock{background:#022c22;color:#6ee7b7}
  .b-sale{background:#2e1065;color:#c4b5fd}
}
.av{font-size:12px;white-space:nowrap}
.av-ON_SALE{color:var(--good);font-weight:600}
.av-SOLD_OUT{color:var(--bad);font-weight:600}
.av-ENDED{color:var(--bad)}
.av-RESTOCKING{color:var(--warn);font-weight:600}
.av-PARTIAL{color:var(--warn);font-weight:600}
.av-LIKELY_SOLD_OUT{color:var(--warn)}
.av-UNVERIFIED,.av-UNVERIFIED_STALE{color:var(--muted)}
.bar{display:inline-block;width:44px;height:6px;border-radius:3px;
  background:var(--line);vertical-align:middle;margin-right:6px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--accent)}
.det{background:var(--bg)}
.det>td{padding:0}
.detin{padding:14px 16px;border-left:3px solid var(--accent)}
.detin h4{margin:0 0 8px;font-size:12px;color:var(--muted);
  text-transform:uppercase;letter-spacing:.4px}
.pt{width:100%;border-collapse:collapse;font-size:12.5px;margin-bottom:12px}
.pt th,.pt td{padding:5px 8px;border-bottom:1px solid var(--line)}
.pt th{position:static;background:transparent;font-size:11px}
.kv{display:flex;flex-wrap:wrap;gap:8px 20px;font-size:12.5px;color:var(--muted)}
.kv b{color:var(--ink);font-weight:600}
.tag{display:inline-block;background:var(--chip);border-radius:5px;
  padding:1px 6px;font-size:11.5px;margin-right:4px;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
/* 详情底部的链接区：主链接（中文详情）实心、次链接（原帖）描边，
   视觉上引导用户先看论坛，同时保留核对原帖的入口。 */
.links{margin-top:12px;display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.lk{display:inline-block;padding:5px 12px;border-radius:6px;
  font-size:12.5px;text-decoration:none;white-space:nowrap}
.lk-main{background:var(--accent);color:#fff;font-weight:600}
.lk-main:hover{filter:brightness(1.08);text-decoration:none}
.lk-sub{color:var(--accent);border:1px solid var(--line-strong)}
.lk-sub:hover{border-color:var(--accent);text-decoration:none}
.empty{text-align:center;color:var(--muted);padding:48px 0}
footer{margin-top:28px;color:var(--muted);font-size:12px;line-height:1.9}
.note{background:var(--chip);border-left:3px solid var(--warn);
  border-radius:0 8px 8px 0;padding:10px 14px;font-size:12.5px;
  color:var(--muted);margin-top:14px}
.guide h2{margin:0 0 12px;font-size:16px}
.guide-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}
.guide-item{background:var(--chip);border-radius:8px;padding:12px 14px;font-size:12.5px;line-height:1.7}
.guide-item b{display:block;color:var(--ink);margin-bottom:4px;font-size:13px}
@media(max-width:760px){
  .hidesm{display:none}
  input[type=search]{min-width:100%}
}
</style>
</head>
<body>
<nav aria-label="Share600 站群导航" style="background:#0f172a;color:#cbd5e1;border-bottom:1px solid #1e293b;font-size:12.5px;">
  <div style="max-width:1180px;margin:0 auto;padding:7px 16px;display:flex;align-items:center;gap:10px;overflow-x:auto;white-space:nowrap;">
    <span style="font-weight:600;color:#94a3b8;font-size:12px;flex-shrink:0;">Share600 工具矩阵</span>
    <a href="https://100xx.xx.kg/" target="_blank" rel="noopener" style="color:#e2e8f0;text-decoration:none;padding:3px 10px;border-radius:999px;background:rgba(255,255,255,.08);">🛠️ 开源资源站</a>
    <a href="https://share600.de5.net/" style="color:#fff;font-weight:600;text-decoration:none;padding:3px 10px;border-radius:999px;background:#2563eb;">🖥️ VPS 促销雷达</a>
    <a href="https://freebie.share600.de5.net/" target="_blank" rel="noopener" style="color:#e2e8f0;text-decoration:none;padding:3px 10px;border-radius:999px;background:rgba(255,255,255,.08);">🎁 福利资源雷达</a>
    <a href="https://sms.share600.de5.net/" target="_blank" rel="noopener" style="color:#e2e8f0;text-decoration:none;padding:3px 10px;border-radius:999px;background:rgba(255,255,255,.08);">📱 SMS 接码比价</a>
  </div>
</nav>
<div class="wrap">

<header>
  <h1>@@TITLE@@</h1>
  <p>@@DESC@@</p>
  <div class="meta">
    <span>数据来源 · 公开优惠信息</span>
    <span>共 <b id="m-total">@@COUNT@@</b> 条</span>
    <span>生成于 <b>@@GENERATED@@</b></span>
    <span><a href="articles.html">行情专栏</a></span>
    <span><a href="data/deals.json">原始 JSON</a></span>
    @@HEADER_LINKS@@
  </div>
</header>

<div class="card">
  <div class="controls">
    <input type="search" id="q" placeholder="搜索厂商 / CPU / 优惠码 / 地点…">
    <select id="src">
      <option value="all">全部来源</option>
      <option value="let">LET</option>
      <option value="leb">LEB</option>
    </select>
    <select id="avail">
      <option value="all">全部状态</option>
      <option value="ON_SALE">在售</option>
      <option value="SOLD_OUT">售罄</option>
      <option value="RESTOCKING">补货中</option>
      <option value="PARTIAL">部分售罄</option>
      <option value="UNVERIFIED">待确认</option>
    </select>
    <select id="sort">
      <option value="score">按活动力度</option>
      <option value="new">按最新</option>
      <option value="price">按价格(低→高)</option>
      <option value="conf">按置信度</option>
      <option value="provider">按厂商</option>
    </select>
    <label class="toggle"><input type="checkbox" id="bigonly"> 只看大力度</label>
    <label class="toggle"><input type="checkbox" id="hideold"> 隐藏 30 天前</label>
  </div>
  <div class="chips" id="chips"></div>
  <div class="note">
    <b>关于「库存」</b>：优先采信<b>商家本人在回帖中的表态</b>（点开任意一行看证据原文）；
    无商家表态时退回帖文关键词；仍无法判定则标「待确认」。
    <b>「待确认」不等于有货</b>，下单前请点进原帖或到厂商官网核实。
  </div>
</div>

<div class="card">
  <div class="tblwrap">
    <table>
      <thead>
        <tr>
          <th data-s="provider">厂商 <span class="ar"></span></th>
          <th data-s="event">活动性质 <span class="ar"></span></th>
          <th data-s="score">力度 <span class="ar"></span></th>
          <th data-s="spec">配置 / CPU <span class="ar"></span></th>
          <th data-s="price">旗舰价 <span class="ar"></span></th>
          <th data-s="avail">状态 <span class="ar"></span></th>
          <th data-s="conf">置信度 <span class="ar"></span></th>
          <th class="hidesm" data-s="age">发布于 <span class="ar"></span></th>
        </tr>
      </thead>
      <tbody id="tb"></tbody>
    </table>
  </div>
  <div class="empty" id="empty" style="display:none">没有匹配的条目</div>
</div>

@@LATEST_ARTICLES@@

<section class="card guide" aria-label="VPS 选购与线路速查">
  <h2>VPS 选购与线路速查</h2>
  <div class="guide-grid">
    <div class="guide-item">
      <b>1. 常见网络线路参考</b>
      CN2 GIA / AS9929 / CMI 属于高优先级回国优化线路，晚高峰丢包率较低；普通 BGP 或直连线路适合建站、中转及海外业务，下单前建议先测试商家提供的 Looking Glass 或测试 IP。<a href="guide-routes.html">查看线路深度解析 &rarr;</a>
    </div>
    <div class="guide-item">
      <b>2. 循环折扣 vs 首年特价</b>
      标注「循环」的优惠码在后续续费时保持同等折扣；未标注循环的促销通常仅限首期账单有效，长期使用请以厂商结算页显示的续费原价为准。<a href="faq.html">查看选购 FAQ &rarr;</a>
    </div>
    <div class="guide-item">
      <b>3. 虚拟化与验收测试</b>
      KVM 支持独立内核与完整容器运行环境，适合 Docker 与常规服务；LXC / OpenVZ 共享宿主机内核。新购机器建议先做回程路由与磁盘 I/O 验收。<a href="guide-benchmark.html">查看验收测速指南 &rarr;</a>
    </div>
  </div>
</section>

<footer>
  <div>本站汇总公开渠道的优惠信息，仅作信息聚合，<b>不构成购买建议</b>。</div>
  <div>@@FXNOTE@@</div>
  <div>库存与价格以厂商官网为准；标注「待确认」表示未能自动验证。 · <a href="articles.html">行情专栏</a> · <a href="about.html">关于本站</a> · <a href="faq.html">选购 FAQ</a> · <a href="guide-routes.html">线路指南</a> · <a href="guide-benchmark.html">验收测速</a> · <a href="privacy.html">隐私政策</a></div>
  @@FOOTER@@
</footer>

</div>

<script type="application/json" id="deals-data">@@DATA@@</script>
<script>
(function(){
  var DATA = JSON.parse(document.getElementById('deals-data').textContent);
  var R = DATA.records || [];
  var EVENT_CN = @@EVENT_CN@@;
  var AVAIL_CN = @@AVAIL_CN@@;

  var st = { q:'', event:'all', src:'all', avail:'all', big:false,
             hideold:false, sort:'score', open:{} };

  function esc(s){
    return String(s==null?'':s).replace(/[&<>"']/g, function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }
  function specText(r){
    var s=r.specs||{}, b=[];
    if(s.vcpu) b.push(s.vcpu+'C');
    if(s.ram_gb) b.push(Math.round(s.ram_gb)+'G');
    if(s.storage_gb){
      var t=(s.storage_type||'').toLowerCase()==='unspecified'?'':(s.storage_type||'');
      b.push(Math.round(s.storage_gb)+'G'+t);
    }
    var cpu=(r.cpu_models||[]).slice(0,2).join(', ');
    var head=b.join('/');
    // 规格与 CPU 各自可能缺失：缺一个就只显示另一个，
    // 两个都缺才用占位符，避免出现「— | Xeon」这种残缺拼接。
    if(head&&cpu) return head+' | '+cpu;
    if(head) return head;
    if(cpu) return cpu;
    return '—';
  }
  function priceText(r){
    var p=r.flagship_price||((r.prices||[])[0]);
    if(!p) return '—';
    var c={year:'/年',quarter:'/季',month:'/月'}[p.cycle]||'';
    return p.currency+(+p.amount)+c;
  }
  function ageText(r){
    if(r.age_days==null) return '—';
    if(r.age_days===0) return '今天';
    return r.age_days+' 天前';
  }
  function bigDeal(r){
    var d=r.discount||{};
    return !!(d.recurring || (d.percent_max&&d.percent_max>=30)
              || r.event_type==='anniversary' || r.event_type==='black_friday');
  }
  function passes(r){
    if(st.event!=='all' && r.event_type!==st.event) return false;
    if(st.src!=='all' && r.source!==st.src) return false;
    if(st.avail!=='all'){
      if(st.avail==='UNVERIFIED'){
        if(r.availability!=='UNVERIFIED' && r.availability!=='UNVERIFIED_STALE') return false;
      } else if(r.availability!==st.avail) return false;
    }
    if(st.big && !bigDeal(r)) return false;
    if(st.hideold && r.age_days!=null && r.age_days>30) return false;
    if(st.q){
      var hay=[r.provider,r.title,(r.cpu_models||[]).join(' '),
               (r.locations||[]).join(' '),
               ((r.discount||{}).codes||[]).join(' '),
               ((r.discount||{}).strength||''),
               (r.specs_all?JSON.stringify(r.specs_all):'')]
              .join(' ').toLowerCase();
      if(hay.indexOf(st.q.toLowerCase())<0) return false;
    }
    return true;
  }
  function cmp(a,b){
    switch(st.sort){
      case 'new': return (a.age_days==null?1e9:a.age_days)-(b.age_days==null?1e9:b.age_days);
      case 'price': {
        var x=a.annual_eur==null?1e12:a.annual_eur,
            y=b.annual_eur==null?1e12:b.annual_eur;
        return x-y;
      }
      case 'conf': return (b.confidence||0)-(a.confidence||0);
      case 'provider': return String(a.provider).localeCompare(String(b.provider));
      case 'age': return (a.age_days==null?1e9:a.age_days)-(b.age_days==null?1e9:b.age_days);
      default: return (b.score||0)-(a.score||0);
    }
  }
  function planRows(r){
    var ps=r.plans||[];
    if(!ps.length) return '';
    var firstKey={}, group=0, rowsHtml='';
    ps.forEach(function(p){
      var key=(p.vcpu||'')+'/'+(p.ram_gb||'')+'/'+(p.storage_gb||'')+'/'+
              ((p.price&&p.price.amount)||'');
      if(firstKey[p.name]===undefined){ firstKey[p.name]=key; }
      else if(firstKey[p.name]!==key){ group++; firstKey[p.name]=key; }
      var label=esc(p.name);
      if(group>0) label+=' <span style="opacity:.5;font-size:11px">#'+(group+1)+'</span>';
      rowsHtml+='<tr><td>'+label+'</td>'+
         '<td>'+(p.vcpu?p.vcpu+'C':'—')+'</td>'+
         '<td>'+(p.ram_gb?Math.round(p.ram_gb)+'G':'—')+'</td>'+
         '<td>'+(p.storage_gb?Math.round(p.storage_gb)+'G'+
              ((p.storage_type&&p.storage_type!=='unspecified')?' '+esc(p.storage_type):''):'—')+'</td>'+
         '<td>'+(p.bandwidth_tb?p.bandwidth_tb+'TB':'—')+'</td>'+
         '<td>'+(p.price?(p.price.currency+(+p.price.amount)+'/年'):'—')+'</td></tr>';
    });
    return '<h4>套餐明细（自动解析 '+ps.length+' 条）</h4>'+
           '<table class="pt"><thead><tr><th>套餐</th><th>CPU</th><th>内存</th>'+
           '<th>存储</th><th>流量</th><th>价格</th></tr></thead><tbody>'+
           rowsHtml+'</tbody></table>';
  }
  function detail(r){
    var d=r.discount||{}, codes=d.codes||[], ex=d.extras||[];
    var h='<div class="detin">';
    h+=planRows(r);
    h+='<h4>抽取信息</h4><div class="kv">';
    h+='<span>来源 <b>'+esc((r.source||'').toUpperCase())+'</b></span>';
    h+='<span>地点 <b>'+((r.locations||[]).join(', ')||'—')+'</b></span>';
    h+='<span>活动性质 <b>'+(EVENT_CN[r.event_type]||r.event_type)+'</b></span>';
    h+='<span>置信度 <b>'+Math.round((r.confidence||0)*100)+'%</b></span>';
    h+='<span>循环折扣 <b>'+(d.recurring?'是':'否/未提及')+'</b></span>';
    if(d.percent_max) h+='<span>最大折扣 <b>'+d.percent_max+'%</b></span>';
    if(r.age_days!=null) h+='<span>发布 <b>'+ageText(r)+'</b></span>';
    if(r.author) h+='<span>发帖人 <b>'+esc(r.author)+'</b></span>';
    if(r.also_on&&r.also_on.length) h+='<span>同时出现在 <b>'+esc(r.also_on.join(', ').toUpperCase())+'</b></span>';
    h+='</div>';
    if(codes.length){
      h+='<h4 style="margin-top:12px">优惠码（需复核）</h4><div>';
      codes.forEach(function(c){ h+='<span class="tag">'+esc(c)+'</span>'; });
      h+='</div>';
    }
    if(ex.length){
      h+='<h4 style="margin-top:12px">加赠</h4><div>';
      ex.forEach(function(c){ h+='<span class="tag">'+esc(c)+'</span>'; });
      h+='</div>';
    }
    if(r.stock_hints&&r.stock_hints.length){
      h+='<h4 style="margin-top:12px">帖文库存关键词</h4><div>';
      r.stock_hints.forEach(function(c){ h+='<span class="tag">'+esc(c)+'</span>'; });
      h+='</div>';
    }
    var rp=r.replies;
    if(rp){
      h+='<h4 style="margin-top:12px">回帖证据（库存判定来源）</h4>';
      h+='<div class="kv" style="margin-bottom:6px">';
      h+='<span>回帖总数 <b>'+(rp.reply_count||0)+'</b></span>';
      h+='<span>商家回复 <b>'+(rp.vendor_reply_count||0)+'</b></span>';
      h+='<span>判定来源 <b>'+(rp.source==='vendor'?'商家表态':rp.source==='user'?'用户提及(弱证据)':'无')+'</b></span>';
      h+='</div>';
      var ev=rp.evidence;
      if(ev){
        h+='<div style="background:var(--chip);border-radius:8px;padding:8px 10px;font-size:12.5px;margin-bottom:8px">';
        h+='<div style="color:var(--muted);margin-bottom:3px">'+
           esc(ev.author||'?')+(ev.is_vendor?'（商家）':'（用户）')+
           (ev.datetime?(' · '+esc(String(ev.datetime).slice(0,10))):'')+'</div>';
        h+='<div>'+esc(ev.snippet||'')+'</div></div>';
      }
      if(rp.vendor_signals&&rp.vendor_signals.length){
        h+='<div style="font-size:12px;color:var(--muted);line-height:1.9">';
        rp.vendor_signals.forEach(function(s){
          // 状态码翻成中文；后面的日期与引文保留原文 ——
          // 那是商家回帖的原始证据，翻译会使其失去证据效力。
          h+='<div><span class="tag">'+
             esc(AVAIL_CN[s.status]||s.status)+'</span> '+
             esc(String(s.datetime||'').slice(0,10))+' — '+
             esc((s.snippet||'').slice(0,110))+'</div>';
        });
        h+='</div>';
      }
    }
    // 链接区：优先引导到论坛的中文详情，同时保留原帖供核对。
    // 站点建站早于发帖，所以 forum_url 来自上一轮结果；没有对应
    // 帖子时只显示原帖链接，不会出现死链。
    h+='<div class="links">';
    if(r.forum_url){
      h+='<a class="lk lk-main" href="'+esc(r.forum_url)+
         '" target="_blank" rel="noopener">中文详情 &rarr;</a>';
      h+='<a class="lk lk-sub" href="'+esc(r.url)+
         '" target="_blank" rel="noopener">原帖 &rarr;</a>';
    } else {
      h+='<a class="lk lk-sub" href="'+esc(r.url)+
         '" target="_blank" rel="noopener">查看原帖 &rarr;</a>';
    }
    h+='</div>';
    return h+'</div>';
  }
  function render(){
    var rows=R.filter(passes).sort(cmp);
    var tb=document.getElementById('tb');
    var out='';
    rows.forEach(function(r){
      var open=!!st.open[r.thread_id];
      out+='<tr class="row'+(open?' open':'')+'" data-id="'+esc(r.thread_id)+'">';
      // 厂商名：有推断出官网就做成外链，否则纯文本
      var pv = esc(r.provider);
      if(r.vendor_url){
        pv = '<a class="pv-link" href="'+esc(r.vendor_url)+
             '" target="_blank" rel="noopener nofollow" '+
             'title="访问官网 '+esc(r.vendor_url)+'">'+pv+'</a>';
      }
      out+='<td><div class="pv">'+pv+'</div></td>';
      out+='<td><span class="badge b-'+esc(r.event_type)+'">'+
           esc(EVENT_CN[r.event_type]||r.event_type)+'</span></td>';
      out+='<td>'+(esc((r.discount||{}).strength)||'<span style="color:var(--muted)">—</span>')+'</td>';
      out+='<td class="spec">'+esc(specText(r))+'</td>';
      out+='<td class="price">'+esc(priceText(r))+'</td>';
      out+='<td><span class="av av-'+esc(r.availability)+'">'+
           esc(AVAIL_CN[r.availability]||r.availability)+'</span></td>';
      out+='<td class="cf"><span class="bar"><i style="width:'+
           Math.round((r.confidence||0)*100)+'%"></i></span>'+
           Math.round((r.confidence||0)*100)+'%</td>';
      out+='<td class="hidesm">'+esc(ageText(r))+'</td>';
      out+='</tr>';
      if(open) out+='<tr class="det"><td colspan="8">'+detail(r)+'</td></tr>';
    });
    tb.innerHTML=out;
    document.getElementById('empty').style.display=rows.length?'none':'block';
    document.getElementById('m-total').textContent=rows.length+' / '+R.length;
  }
  function buildChips(){
    var cnt={};
    R.forEach(function(r){ cnt[r.event_type]=(cnt[r.event_type]||0)+1; });
    var keys=Object.keys(cnt).sort(function(a,b){ return cnt[b]-cnt[a]; });
    var h='<div class="chip on" data-e="all">全部<span class="n">'+R.length+'</span></div>';
    keys.forEach(function(k){
      h+='<div class="chip" data-e="'+esc(k)+'">'+esc(EVENT_CN[k]||k)+
         '<span class="n">'+cnt[k]+'</span></div>';
    });
    var box=document.getElementById('chips');
    box.innerHTML=h;
    box.addEventListener('click',function(e){
      var c=e.target.closest('.chip'); if(!c) return;
      st.event=c.getAttribute('data-e');
      Array.prototype.forEach.call(box.querySelectorAll('.chip'),function(x){
        x.classList.toggle('on',x===c);
      });
      render();
    });
  }
  document.getElementById('q').addEventListener('input',function(e){
    st.q=e.target.value.trim(); render(); });
  document.getElementById('src').addEventListener('change',function(e){
    st.src=e.target.value; render(); });
  document.getElementById('avail').addEventListener('change',function(e){
    st.avail=e.target.value; render(); });
  document.getElementById('sort').addEventListener('change',function(e){
    st.sort=e.target.value; render(); });
  document.getElementById('bigonly').addEventListener('change',function(e){
    st.big=e.target.checked; render(); });
  document.getElementById('hideold').addEventListener('change',function(e){
    st.hideold=e.target.checked; render(); });
  document.getElementById('tb').addEventListener('click',function(e){
    // 点在厂商官网链接上时，只跳转、不展开该行
    if(e.target.closest('a')) return;
    var tr=e.target.closest('tr.row'); if(!tr) return;
    var id=tr.getAttribute('data-id');
    st.open[id]=!st.open[id];
    render();
  });
  Array.prototype.forEach.call(document.querySelectorAll('th[data-s]'),function(th){
    th.addEventListener('click',function(){
      st.sort=th.getAttribute('data-s');
      document.getElementById('sort').value=st.sort;
      render();
    });
  });
  buildChips();
  render();
})();
</script>
</body>
</html>
"""


def _render_article_page(
    slug: str,
    page_title: str,
    description: str,
    body_html: str,
    extra_head: str = "",
) -> str:
    """使用统一样式与站群导航渲染独立静态文章/合规页面。"""
    title = html.escape(config.SITE_TITLE)
    site_url = html.escape(config.SITE_URL or "/")
    canonical = (f'<link rel="canonical" href="{site_url}/{slug}">'
                 if config.SITE_URL else "")
    footer_line = (f"<div>{html.escape(config.SITE_FOOTER)}</div>"
                   if config.SITE_FOOTER else "")
    nav_items = [
        ("./", "实时促销雷达"),
        ("articles.html", "行情专栏"),
        ("about.html", "关于本站"),
        ("faq.html", "选购 FAQ"),
        ("guide-routes.html", "优化线路指南"),
        ("guide-benchmark.html", "验收与测速"),
        ("privacy.html", "隐私政策"),
    ]
    subnav = " · ".join(
        f'<b>{label}</b>' if href == slug else f'<a href="{href}">{label}</a>'
        for href, label in nav_items
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(page_title)} - {title}</title>
<meta name="description" content="{html.escape(description, quote=True)}">
{canonical}
{extra_head}
<style>
:root{{--bg:#f6f8f8;--card:#fff;--ink:#16181d;--muted:#6b7280;--line:#e5e7eb;--accent:#0f766e}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0f1214;--card:#171c1f;--ink:#e8eaed;--muted:#9aa0a6;--line:#272d33;--accent:#2dd4bf}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.85 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans SC",sans-serif}}
a{{color:var(--accent);text-decoration:none}}
a:hover{{text-decoration:underline}}
.wrap{{max-width:860px;margin:0 auto;padding:28px 18px 64px}}
.subnav{{font-size:13px;color:var(--muted);margin-bottom:12px;line-height:1.9}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:24px 26px;margin-top:14px}}
h1{{margin:0 0 8px;font-size:22px;line-height:1.35}}
h2{{margin:22px 0 8px;font-size:16px}}
h3{{margin:16px 0 6px;font-size:14.5px}}
p,li{{color:var(--ink);margin:6px 0}}
table{{width:100%;border-collapse:collapse;margin:12px 0;font-size:13px}}
th,td{{border:1px solid var(--line);padding:8px 10px;text-align:left}}
th{{background:rgba(15,118,110,.08)}}
code{{background:rgba(127,127,127,.14);padding:1px 5px;border-radius:4px;font-size:12.5px}}
.muted{{color:var(--muted);font-size:13px}}
footer{{margin-top:24px;color:var(--muted);font-size:12px;line-height:1.8}}
</style>
</head>
<body>
<nav aria-label="Share600 站群导航" style="background:#0f172a;color:#cbd5e1;border-bottom:1px solid #1e293b;font-size:12.5px;">
  <div style="max-width:1180px;margin:0 auto;padding:7px 16px;display:flex;align-items:center;gap:10px;overflow-x:auto;white-space:nowrap;">
    <span style="font-weight:600;color:#94a3b8;font-size:12px;flex-shrink:0;">Share600 工具矩阵</span>
    <a href="https://100xx.xx.kg/" target="_blank" rel="noopener" style="color:#e2e8f0;text-decoration:none;padding:3px 10px;border-radius:999px;background:rgba(255,255,255,.08);">🛠️ 开源资源站</a>
    <a href="https://share600.de5.net/" style="color:#fff;font-weight:600;text-decoration:none;padding:3px 10px;border-radius:999px;background:#2563eb;">🖥️ VPS 促销雷达</a>
    <a href="https://freebie.share600.de5.net/" target="_blank" rel="noopener" style="color:#e2e8f0;text-decoration:none;padding:3px 10px;border-radius:999px;background:rgba(255,255,255,.08);">🎁 福利资源雷达</a>
    <a href="https://sms.share600.de5.net/" target="_blank" rel="noopener" style="color:#e2e8f0;text-decoration:none;padding:3px 10px;border-radius:999px;background:rgba(255,255,255,.08);">📱 SMS 接码比价</a>
  </div>
</nav>
<div class="wrap">
  <div class="subnav">{subnav}</div>
  <article class="card">
    {body_html}
  </article>
  <footer>
    <div>{subnav}</div>
    {footer_line}
  </footer>
</div>
</body>
</html>
"""


def _render_privacy_html(extra_head: str = "") -> str:
    """生成独立隐私政策页面 HTML，满足广告联盟与合规披露要求。"""
    site_url = html.escape(config.SITE_URL or "/")
    body = f"""<h1>隐私政策与声明</h1>
    <p class="muted">适用站点：{site_url}</p>
    <h2>1. 信息收集与本地存储</h2>
    <p>本站为纯静态公开信息聚合站点，不提供用户注册或登录功能，亦不主动收集用户的个人身份信息。</p>
    <h2>2. Cookie 与第三方广告（Google AdSense）</h2>
    <ul>
      <li>本站可能使用 Google AdSense 等第三方广告服务展示广告内容。</li>
      <li>Google 及其合作伙伴可能使用 Cookie 或类似技术，根据您在本站及其他网站的访问记录投放相关广告。</li>
      <li>您可访问 Google 广告设置或通过浏览器设置管理及停用个性化广告 Cookie。</li>
    </ul>
    <h2>3. 外部链接与返利声明</h2>
    <p>本站汇总的 VPS 促销信息均来自公开渠道，部分商家链接可能包含推广返利（Affiliate）参数，但不会额外增加您的购买成本。产品配置、价格与库存以商家官网结算页面为准。</p>"""
    return _render_article_page(
        "privacy.html",
        "隐私政策",
        f"{config.SITE_TITLE} 隐私政策、Cookie 与第三方广告说明",
        body,
        extra_head,
    )


STATIC_GUIDE_PAGES = [
    (
        "about.html",
        "关于本站与监测方法",
        "了解 Share600 VPS 促销雷达的数据来源、库存判定机制与评分逻辑。",
        """<h1>关于本站与数据监测方法</h1>
    <p class="muted">透明、客观的 VPS 促销信息聚合与库存追踪说明</p>
    <h2>1. 为什么做这个监测站</h2>
    <p>海外 VPS 社区（如 LowEndTalk、LowEndBox）每天都会发布大量限时促销、周年庆特价、黑五闪购与补货通知。但原帖往往是纯英文长文，参数分散、优惠码易过期，而且很多低价套餐在发布数小时内就会售罄（Out of Stock）。本站通过自动化脚本定时抓取公开资讯，将核心配置、折后年付/月付价格、是否循环折扣与实时库存状态整理成可筛选的中文看板，帮助开发者和站长快速定位高性价比主机。</p>
    <h2>2. 数据采集与更新频率</h2>
    <ul>
      <li><b>公开数据源</b>：主要采集自 LowEndTalk Offers 版块及 LowEndBox 公开 RSS 订阅源，不抓取任何需要登录权限的私密内容。</li>
      <li><b>自动刷新周期</b>：后台流水线每 12 小时自动拉取最新促销帖并重新解析，更新后的纯静态页面会同步推送至全球边缘缓存节点。</li>
      <li><b>多币种统一折算</b>：为方便横向对比性价比，系统会按公开参考汇率将 EUR、GBP、CAD 等货币折算为 USD 与 CNY 供参考，实际扣款金额以结算页为准。</li>
    </ul>
    <h2>3. 库存状态如何判定</h2>
    <p>许多用户最怕点开特价链接后发现已售罄。针对支持公开讨论串的数据源，解析器会自动读取帖子末页中<b>商家官方账号（Original Poster）</b>的最新发布回复，结合常见补货（Restock）与售罄（Sold Out / Out of Stock）关键词进行交叉判定；若商家未在帖内说明，则标注为「待确认」。</p>
    <h2>4. 独立性与免责说明</h2>
    <p>本站仅提供公开市场信息的整理与检索，不代表任何云服务商的官方立场，也不对商家的服务稳定性做担保。在购买任何年付或特价 VPS 前，建议先查阅服务条款（TOS）、测试 Looking Glass 网络延迟，并做好异地数据备份。</p>""",
    ),
    (
        "faq.html",
        "VPS 选购常见问答 (FAQ)",
        "解答循环折扣、KVM 与 LXC 区别、流量计费、退款政策与防跑路等常见问题。",
        """<h1>VPS 选购常见问答（FAQ）</h1>
    <p class="muted">整理新手与站长在选购海外特价 VPS 时最常遇到的 8 个核心问题</p>
    <h2>Q1：什么是「循环折扣（Recurring）」？和首年特价有什么区别？</h2>
    <p><b>循环折扣</b>指你在首期付款时享受的优惠价格，在后续按月或按年续费时依然保持不变（只要不主动取消服务）。而<b>非循环优惠（One-time）</b>仅限第一张账单打折，第二年续费将恢复原价。长期建站或跑稳定业务时，应优先筛选标注「循环」的套餐。</p>
    <h2>Q2：KVM、LXC 与 OpenVZ 虚拟化该怎么选？</h2>
    <ul>
      <li><b>KVM</b>：完全硬件虚拟化，拥有独立内核，支持自定义操作系统、开启 BBR 拥塞控制以及流畅运行 Docker 容器，是生产环境的首选。</li>
      <li><b>LXC / OpenVZ</b>：操作系统级虚拟化，与宿主机共享内核。虽然价格往往更低，但通常无法加载部分内核模块，且容易受同宿主机邻居资源争抢影响，更适合轻量学习或临时测试。</li>
    </ul>
    <h2>Q3：单向流量和双向流量怎么计算？</h2>
    <p>大部分北美与欧洲廉价 VPS 采用<b>双向计费</b>（入站下载流量 + 出站上传流量合计扣除月度配额）；部分亚太或优化线路商家采用<b>仅出站计费（Outbound Only）</b>，即只统计服务器向外发送数据的流量。如果你的业务需要频繁拉取镜像或同步备份，双向计费会更快消耗额度。</p>
    <h2>Q4：为什么超低价年付套餐通常不支持退款？</h2>
    <p>由于促销款利润空间极低，且新开 IP 资源与支付通道存在固定成本，多数商家在促销条款中会明确规定「特价款 / 清仓款（Clearance）不支持无理由退款」，或仅提供 24～72 小时内的首次故障退款窗口。下单前务必核对商家结账页面的 TOS 勾选框。</p>
    <h2>Q5：如何降低小厂「跑路」或超售严重带来的风险？</h2>
    <ol>
      <li><b>控制预付周期</b>：对成立不满两年的新晋商家，尽量选择月付或季付，不要轻易一次性购买三年付。</li>
      <li><b>使用支持争议处理的支付方式</b>：如 PayPal 或主流信用卡，避免使用不可逆转账方式为高风险特价单大额充值。</li>
      <li><b>坚持异地备份</b>：无论大厂小厂，重要网站数据与数据库都应每天自动备份到对象存储（如 R2/S3）或本地。</li>
    </ol>
    <h2>Q6：什么是 Looking Glass（LG）？下单前为什么要测？</h2>
    <p>Looking Glass 是商家公开提供的网络测试页面，通常包含测试 IPv4/IPv6、100MB/1GB 测速文件下载链接以及在线 Ping/MTR 路由追踪工具。通过测试本地宽带到目标机房的延迟、丢包率与回程路由，可以提前判断该节点是否适合你的访问人群。</p>
    <h2>Q7：标配只有 IPv6 没有独立 IPv4 的机器能建站吗？</h2>
    <p>可以，但需要搭配 Cloudflare 等支持 IPv6 回源的 CDN 使用，或者通过 NAT IPv4 端口映射转发。如果需要在国内直连 SSH 管理，本机网络必须支持 IPv6，否则需要借助跳板机中转。</p>
    <h2>Q8：购买后发现 IP 不通或无法连接 SSH 怎么办？</h2>
    <p>首先通过商家控制台的 VNC（noVNC）登录系统，确认防火墙是否放行了 SSH 端口（默认 22）以及网卡是否正确配置了网关；若 VNC 内可正常联网但外部无法 Ping 通，可能是分配到了被墙段 IP，可及时提交工单（Ticket）说明情况申请免费或付费更换 IP。</p>""",
    ),
    (
        "guide-routes.html",
        "国内访问常见优化线路深度解析",
        "对比 CN2 GIA、联通 AS9929、移动 CMI、日本软银与香港 BGP 线路特征与适用场景。",
        """<h1>国内访问常见优化线路深度解析</h1>
    <p class="muted">从普通直连到高优先级精品网：读懂机房线路标识，避免花冤枉钱</p>
    <h2>1. 为什么同地区机房速度差异巨大？</h2>
    <p>决定国内访问海外 VPS 体验的核心，并不只是机房到中国大陆的物理距离，而是数据包经过的<b>国际出口骨干网级别</b>以及<b>去程/回程路由是否一致</b>。普通民用骨干网（如电信 163、联通 4837 普通段）在晚高峰（20:00–23:30）极易出现跨网拥堵和丢包；而商业精品线路则通过独立优先级通道保障低丢包与低抖动。</p>
    <h2>2. 主流回国优化线路横向对比</h2>
    <table>
      <thead>
        <tr><th>线路名称</th><th>所属运营商</th><th>核心特征</th><th>适用场景</th></tr>
      </thead>
      <tbody>
        <tr><td><b>CN2 GIA (AS4809)</b></td><td>中国电信</td><td>去程与回程均走 59.43 精品节点，晚高峰丢包极低</td><td>面向国内用户的建站、低延迟交互服务</td></tr>
        <tr><td><b>CN2 GT</b></td><td>中国电信</td><td>仅部分国际段走 59.43，国内段仍走 163 骨干</td><td>预算有限的轻量应用，晚高峰仍有一定波动</td></tr>
        <tr><td><b>AS9929 (CUII)</b></td><td>中国联通</td><td>联通高端政企精品网，对标电信 CN2 GIA</td><td>三网均衡优化，性价比通常高于同配置 GIA</td></tr>
        <tr><td><b>AS4837</b></td><td>中国联通</td><td>联通普通民用国际出口，带宽充沛、价格低廉</td><td>大流量下载、常规海外业务与中转备选</td></tr>
        <tr><td><b>CMI / CMIN2</b></td><td>中国移动</td><td>移动国际骨干（CMIN2 为新一代精品网）</td><td>移动宽带直连友好，亚太方向延迟表现优异</td></tr>
        <tr><td><b>日本 SoftBank / IIJ</b></td><td>日本本土运营商</td><td>软银对联通友好、IIJ 晚高峰相对平稳</td><td>亚太低延迟建站与东亚区域节点</td></tr>
      </tbody>
    </table>
    <h2>3. 什么是「三网回程优化」与「单网优化」？</h2>
    <p>部分机房（如美西洛杉矶部分 CERA、GSL 或香港优化机房）会接入多条商业线路，并通过 BGP 策略实现「电信走 CN2 GIA、联通走 9929、移动走 CMIN2」，即所谓的<b>三网各自优化</b>。而有些低价特价款仅在去程接入普通线路、回程走单网优化（例如三网回程统一走联通 4837 或电信 GIA），选购时应看清商家标注是「双程」还是「单程回程」。</p>
    <h2>4. 如何自行验证线路真伪？</h2>
    <ul>
      <li><b>不要只看白天 Ping 值</b>：普通线路在凌晨和工作日上午延迟同样很低，真正拉开差距的是每晚 21:00–22:30 的丢包率与抖动。</li>
      <li><b>用 MTR 观察路由跳数</b>：关注路径中是否出现 <code>59.43.*.*</code>（电信 CN2）、<code>218.105.*.* / 210.51.*.*</code>（联通 9929）或 <code>223.120.*.*</code>（移动 CMI）等特征节点。</li>
    </ul>""",
    ),
    (
        "guide-benchmark.html",
        "新购 VPS 验收与性能测试实操指南",
        "新购 VPS 到手后的标准验收清单：CPU/磁盘 I/O 测试、回程路由验证与基础安全加固。",
        """<h1>新购 VPS 验收与性能测试实操指南</h1>
    <p class="muted">拿到一台新机器后，花 10 分钟确认硬件规格、网络质量与基础安全</p>
    <h2>第一步：核对基础硬件与磁盘性能</h2>
    <p>促销页面标注的 CPU 核心数、内存容量与 NVMe 固态硬盘规格，在开通后应第一时间核验。可以通过以下命令快速确认系统基础信息：</p>
    <ul>
      <li><code>lscpu</code> 或 <code>nproc</code>：核对逻辑核心数与 CPU 型号（关注是否限制了单核主频）。</li>
      <li><code>free -h</code> 与 <code>df -h</code>：检查可用物理内存、Swap 空间与实际挂载的磁盘分区容量。</li>
      <li><b>磁盘 I/O 评估</b>：使用 <code>fio</code> 测试 4K 随机读写（对数据库和网页响应至关重要），不要仅依赖单一的顺序 <code>dd</code> 写入结果。NVMe 宿主机正常的 4K 随机读写通常在数百 MB/s 以上；若长期低于 50 MB/s，说明宿主机磁盘负载较高。</li>
    </ul>
    <h2>第二步：网络吞吐与回程路由检查</h2>
    <p>除了查看商家提供的 Looking Glass，在机器内部也可以结合本地测速与路由追踪工具验证网络质量：</p>
    <ul>
      <li>使用 <code>mtr -rwzbc 20 &lt;本地公网IP&gt;</code> 观察从 VPS 返回你本地宽带的每一跳延迟与丢包情况。</li>
      <li>测试单线程与多线程下载速率，确认是否存在明显的端口限速（例如标称 1Gbps 带宽但单线程被限制在 50Mbps 以内）。</li>
    </ul>
    <h2>第三步：基础系统加固（上线前必做）</h2>
    <p>刚开通的公网 VPS 很快就会遭到自动化扫描器爆破。在部署任何正式业务前，建议完成以下四项基础配置：</p>
    <ol>
      <li><b>切换为 SSH 密钥登录</b>：在 <code>/etc/ssh/sshd_config</code> 中设置 <code>PasswordAuthentication no</code>，彻底杜绝暴力破解风险。</li>
      <li><b>启用系统防火墙</b>：使用 <code>ufw</code> 或 <code>firewalld</code> 仅放行必需端口（如 SSH、80、443）。</li>
      <li><b>开启 BBR 拥塞控制</b>：对于 Linux 4.9+ 内核的 KVM 机器，启用 TCP BBR 可显著提升高延迟或轻度丢包环境下的单线程传输效率。</li>
      <li><b>配置自动安全更新</b>：Debian/Ubuntu 系统可启用 <code>unattended-upgrades</code>，及时修补高危系统漏洞。</li>
    </ol>""",
    ),
]


def build_site(payload: dict, out_dir=None, verbose: bool = True) -> str:
    """把 payload 渲染成静态站，返回输出目录。"""
    out_dir = str(out_dir or config.SITE_DIR)
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, "data"), exist_ok=True)

    enrich(payload.get("records", []))

    # API 端点
    api_path = os.path.join(out_dir, "data", "deals.json")
    with open(api_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    canonical = ""
    if config.SITE_URL:
        canonical = f'<link rel="canonical" href="{config.SITE_URL}/">'

    extra_head_parts = []
    gsc_verify = getattr(config, "GSC_VERIFY", "").strip()
    if gsc_verify:
        extra_head_parts.append(
            f'<meta name="google-site-verification" content="{html.escape(gsc_verify, quote=True)}">'
        )
    adsense_id = getattr(config, "ADSENSE_ID", "").strip()
    if adsense_id:
        extra_head_parts.append(
            f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={html.escape(adsense_id, quote=True)}" crossorigin="anonymous"></script>'
        )
    extra_head = "\n".join(extra_head_parts)

    article_items = articles.load_articles()

    html_out = (SITE_TEMPLATE
                .replace("@@TITLE@@", html.escape(config.SITE_TITLE))
                .replace("@@DESC@@", html.escape(config.SITE_DESC, quote=True))
                .replace("@@CANONICAL@@", canonical)
                .replace("@@EXTRA_HEAD@@", extra_head)
                .replace("@@DATA@@", _json_for_script(payload))
                .replace("@@COUNT@@", str(payload.get("count", 0)))
                .replace("@@GENERATED@@", generated)
                .replace("@@EVENT_CN@@", json.dumps(EVENT_CN, ensure_ascii=False))
                .replace("@@AVAIL_CN@@", json.dumps(AVAIL_CN, ensure_ascii=False))
                .replace("@@FXNOTE@@", html.escape(config.FX_NOTE))
                .replace("@@HEADER_LINKS@@", _header_links_html())
                .replace("@@LATEST_ARTICLES@@",
                         articles.render_latest_articles_section(article_items))
                .replace("@@FOOTER@@",
                         html.escape(config.SITE_FOOTER) if config.SITE_FOOTER
                         else ""))

    index_path = os.path.join(out_dir, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html_out)

    privacy_path = os.path.join(out_dir, "privacy.html")
    with open(privacy_path, "w", encoding="utf-8") as f:
        f.write(_render_privacy_html(extra_head))

    for slug, page_title, desc, body_html in STATIC_GUIDE_PAGES:
        page_path = os.path.join(out_dir, slug)
        with open(page_path, "w", encoding="utf-8") as f:
            f.write(_render_article_page(slug, page_title, desc, body_html, extra_head))

    articles_index_path = os.path.join(out_dir, "articles.html")
    with open(articles_index_path, "w", encoding="utf-8") as f:
        f.write(_render_article_page(
            "articles.html",
            "VPS 行情观察与深度评测专栏",
            "每日 VPS 促销行情分析、高频厂商评测与选购避坑指南。",
            articles.render_articles_index_body(article_items),
            extra_head,
        ))

    for idx, art in enumerate(article_items):
        prev_link = (
            f'<a href="{html.escape(article_items[idx + 1].href)}">&larr; {html.escape(article_items[idx + 1].title)}</a>'
            if idx + 1 < len(article_items) else ""
        )
        next_link = (
            f'<a href="{html.escape(article_items[idx - 1].href)}">{html.escape(article_items[idx - 1].title)} &rarr;</a>'
            if idx > 0 else ""
        )
        nav_footer = (
            '<div style="margin-top:24px;padding-top:14px;border-top:1px solid var(--line);'
            'display:flex;flex-wrap:wrap;justify-content:space-between;gap:10px;font-size:13px;">'
            f'<div>{prev_link}</div>'
            '<div><a href="articles.html">返回专栏列表</a> · <a href="./">查看实时促销雷达</a></div>'
            f'<div>{next_link}</div>'
            '</div>'
        )
        art_body = (
            f'<div class="muted" style="font-size:12.5px;margin-bottom:6px;">'
            f'<a href="articles.html">行情专栏</a> / {html.escape(art.category)} · 发布于 {html.escape(art.date)}</div>'
            f'<h1>{html.escape(art.title)}</h1>'
            f'<p class="muted">{html.escape(art.summary)}</p>'
            f'{art.body_html}'
            f'{nav_footer}'
        )
        art_path = os.path.join(out_dir, art.href)
        with open(art_path, "w", encoding="utf-8") as f:
            f.write(_render_article_page(
                art.href,
                art.title,
                art.summary or art.title,
                art_body,
                extra_head,
            ))

    # 部署辅助文件
    with open(os.path.join(out_dir, ".nojekyll"), "w", encoding="utf-8") as f:
        f.write("")

    if config.SITE_URL:
        from urllib.parse import urlsplit
        host = (urlsplit(config.SITE_URL).hostname or "").strip()
        if host and not host.endswith(".github.io"):
            with open(os.path.join(out_dir, "CNAME"), "w", encoding="utf-8") as f:
                f.write(host + "\n")

    if adsense_id:
        pub_id = adsense_id.removeprefix("ca-")
        with open(os.path.join(out_dir, "ads.txt"), "w", encoding="utf-8") as f:
            f.write(f"google.com, {pub_id}, DIRECT, f08c47fec0942fa0\n")

    robots = ["User-agent: *", "Allow: /"]
    if config.SITE_URL:
        robots += ["", f"Sitemap: {config.SITE_URL}/sitemap.xml"]
    with open(os.path.join(out_dir, "robots.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(robots) + "\n")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # 未配置域名时用相对路径，避免生成无效的绝对地址
    loc = f"{config.SITE_URL}/" if config.SITE_URL else "/"
    sitemap_urls = [
        ("", today),
        ("articles.html", today),
        ("privacy.html", today),
        *((slug, today) for slug, _, _, _ in STATIC_GUIDE_PAGES),
        *((art.href, art.date or today) for art in article_items),
    ]
    sitemap_entries = "".join(
        f'  <url><loc>{loc}{path}</loc><lastmod>{lastmod}</lastmod></url>\n'
        for path, lastmod in sitemap_urls
    )
    with open(os.path.join(out_dir, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                f'{sitemap_entries}'
                '</urlset>\n')

    # 站点图标：从 assets/ 复制到输出目录。
    # 图标是二进制资源，不适合内联进模板；放 assets/ 便于替换品牌素材。
    _copy_icons(out_dir, verbose)

    if verbose:
        print(f"站点已生成 -> {os.path.abspath(out_dir)}")
        print("  index.html      %8.1f KB" % (os.path.getsize(index_path) / 1024))
        print("  data/deals.json %8.1f KB" % (os.path.getsize(api_path) / 1024))
    return out_dir


# 需要随站点发布的图标文件
ICON_FILES = ("favicon.ico", "icon-192.png", "icon-512.png",
              "apple-touch-icon.png")


def _copy_icons(out_dir: str, verbose: bool = True) -> None:
    """把 assets/ 下的图标复制到站点目录，并生成 webmanifest。

    缺失图标时只告警不报错 —— 不应因为图标问题导致建站失败。
    """
    src_dir = config.ASSETS_DIR
    copied = 0
    for name in ICON_FILES:
        src = os.path.join(str(src_dir), name)
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(out_dir, name))
            copied += 1
        elif verbose:
            print("  [warn] 缺少图标 assets/%s（运行 tools/make_icons.py 生成）"
                  % name)

    # webmanifest：让 Android/PWA 也能用上图标
    name = (config.SITE_TITLE or "Deals").strip()
    short = name[:12]
    icons = []
    for fn, size in (("icon-192.png", "192x192"),
                     ("icon-512.png", "512x512")):
        if os.path.exists(os.path.join(out_dir, fn)):
            icons.append({"src": fn, "sizes": size, "type": "image/png",
                          "purpose": "any"})
    manifest = {
        "name": name,
        "short_name": short,
        "icons": icons,
        "theme_color": "#75ADFB",
        "background_color": "#ffffff",
        "display": "standalone",
        "start_url": "./",
    }
    with open(os.path.join(out_dir, "manifest.webmanifest"), "w",
              encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    if verbose and copied:
        print("  图标            %d 个" % copied)
