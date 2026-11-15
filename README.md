<div align="center">

# Share600 VPS 促销雷达

实时聚合 **LowEndTalk Offers** 与 **LowEndBox** 的公开 VPS 促销信息，自动解析配置规格、折扣力度、循环优惠与**库存状态**，构建纯静态 Web 站点。

**零运行时依赖（仅 Python 标准库） · GitHub Actions 自动刷新 · GitHub Pages 静态托管**

[![在线站点](https://img.shields.io/badge/%E5%9C%A8%E7%BA%BF%E7%AB%99%E7%82%B9-share600.de5.net-0f766e?style=for-the-badge)](https://share600.de5.net)
[![资源主站](https://img.shields.io/badge/%E8%B5%84%E6%BA%90%E4%B8%BB%E7%AB%99-100xx.xx.kg-2563eb?style=for-the-badge)](https://100xx.xx.kg/)
[![协议](https://img.shields.io/badge/%E5%8D%8F%E8%AE%AE-MIT-yellow?style=for-the-badge)](LICENSE)

</div>

---

## 📍 相关站点

| 用途 | 地址 |
|---|---|
| **在线站点** | https://share600.de5.net |
| **隐私政策** | https://share600.de5.net/privacy.html |
| **开源工具与资源主站** | https://100xx.xx.kg/ |

---

## ✨ 核心特性

- **双数据源聚合**：同步抓取 LowEndTalk Offers RSS 与 LowEndBox Feed，自动跨源去重。
- **回帖级库存判定**：读取帖子末页厂商官方回复（Original Poster），区分 `有货`、`部分售罄 (PARTIAL)`、`已售罄` 与 `待确认`。
- **独立站群与合规构建**：
  - 自动生成 `index.html`、`privacy.html`、`sitemap.xml`、`robots.txt`、`CNAME` 与 `ads.txt`。
  - 内置 **VPS 选购与线路速查** 指南卡片，辅助判断 CN2 GIA / AS9929 / CMI 线路与循环折扣规则。
- **零依赖纯静态**：核心抓取与渲染仅使用 Python 标准库，数据内嵌于 `site/index.html` 与 `site/data/deals.json`。

---

## 🚀 本地运行与测试

```bash
# 抓取双数据源并生成静态站（输出至 site/）
python -m letdeals

# 同时抓取帖子末页回帖（提升库存判定准确率）
python -m letdeals --with-replies

# 运行单元测试
python -m pytest tests -q
```

生成完成后，直接打开 `site/index.html` 即可本地预览。

---

## ⚙️ 常用环境变量

部署或通过 GitHub Actions 运行时，可通过以下环境变量自定义站点行为：

| 变量名 | 说明 |
|---|---|
| `LDR_SITE_URL` | 站点根地址（末尾不带 `/`），用于生成 `CNAME`、`sitemap.xml` 与 `canonical` |
| `LDR_SITE_TITLE` | 站点标题 |
| `LDR_SITE_DESC` | 站点 Meta Description |
| `LDR_SITE_FOOTER` | 页脚自定义声明 |
| `LDR_HEADER_LINKS` | 页头导航链接，格式 `名称\|URL,名称2\|URL2` |
| `LDR_ADSENSE_ID` | Google AdSense 发布商 ID（如 `ca-pub-xxxxxxxx`），自动生成页面脚本与 `ads.txt` |
| `LDR_AFFILIATES` | 商家返利参数 JSON 映射 |

---

## 📂 目录结构

```text
letdeals/           核心抓取、解析、返利注入与静态站渲染模块
data/               抓取缓存与历史状态数据
site/               生成的静态站点产物（部署至 GitHub Pages）
tests/              单元测试套件
.github/workflows/  定时抓取与 GitHub Pages 自动部署流水线
```

---

## 🙏 致敬与开源协议

- 本项目基于 **MIT License** 开源，详见 [LICENSE](LICENSE)。
- 特别致敬上游开源项目 **[lowseekai/let-deals-radar](https://github.com/lowseekai/let-deals-radar)**，感谢原作者设计的零依赖 Python 架构、LET/LEB 数据解析器与回帖库存判定机制。本项目在其开源基础上进行了独立站点品牌化、界面差异化、合规页面补充与自动化部署改造。
