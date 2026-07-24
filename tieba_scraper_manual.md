# 百度贴吧帖子爬虫 · 项目说明书（Agent 交接/运维手册）

> 本文档面向**负责本项目的 Agent（或接手维护者）**。
> 读完后你应能：独立运行、定位故障、判断"是不是脚本 bug"、在红线内做安全改动。
> 关联代码：`tieba_scraper.py`（单文件，零外部服务依赖）。

---

## 0. 一句话定位

一个**实时**抓取百度贴吧某个吧"当前最新前 N 帖"的爬虫：发帖人 ID、发帖时间、正文、点赞数、照片（下载到本地），并自动排除广告。每次运行都重新抓取，不写死结果。

---

## 1. 它能做到 / 做不到什么（先建立正确预期）

| 维度 | 现状 | 说明 |
|---|---|---|
| 字段完整性 | ✅ | 发帖人 ID、时间、正文、点赞数、回复数、照片都在 |
| 照片下载 | ✅ | 已修复（见 §5），下到的是真实帖子配图 |
| 广告排除 | ✅ | 列表推广帖 + 详情页注入广告块 + 评论区天然规避 |
| **未登录 + 云服务器 IP** | ⚠️ **只能 ~20 帖** | 百度封了翻页（第 2 页 403），只拿得到首页 |
| **未登录 + 家庭宽带 IP** | ✅ 可满 100 帖 | 翻页接口对家庭 IP 正常 |
| **登录态 BDUSS** | ✅ 稳定满 100 帖 | 走官方接口，最稳，推荐 |

> **红线认知**：脚本逻辑本身支持 100 帖翻页；拿不到 100 是**百度反爬/网络环境**限制，不是脚本没写完。不要在脚本里"硬凑" 100——它已经在翻页了，只是被目标站点拦了。

---

## 2. 运行方式

### 依赖
```bash
pip install requests beautifulsoup4 lxml
```

### 命令
```bash
# 默认：抓「电脑吧」前 100 帖
python tieba_scraper.py

# 试跑前 5 帖（调参/验证用）
python tieba_scraper.py --limit 5

# 换贴吧（任意吧名，脚本自动 URL 编码）
python tieba_scraper.py --kw 显卡

# 网络慢 → 调大读取超时（秒）
python tieba_scraper.py --limit 100 --timeout 60

# 登录态（最稳，稳定翻满 100）
python tieba_scraper.py --limit 100 --bduss "你的BDUSS"

# 自定义输出目录
python tieba_scraper.py --out /path/to/out
```

### 命令行参数（全部已验证存在）
| 参数 | 默认 | 作用 |
|---|---|---|
| `--kw` | `电脑` | 贴吧名称 |
| `--limit` | `100` | 目标帖子数 |
| `--bduss` | 无 | 登录 cookie，启用官方接口通道 |
| `--timeout` | `30.0` | **读取**超时秒数（连接超时固定 10s） |
| `--no-ipv4` | 关闭 | 关掉"强制 IPv4"（默认开启） |
| `--out` | `./tieba_<kw>/output` | 输出目录 |

### 启动时会打印配置行（**排查第一手证据**）
```
[配置] 脚本: <绝对路径>
[配置] 超时: 连接 10s / 读取 30.0s | 强制IPv4: True
[配置] 目标: 电脑吧 前 100 帖
```
若你看到 `读取 10s` 但传了 `--timeout 60`，说明**跑的不是这份文件**（旧版残留），先确认文件路径与版本。

---

## 3. 输出结构

```
tieba_<贴吧名>/output/
├── posts.csv        # 扁平表，每帖一行。images 列用 ";" 分隔本地相对路径
├── posts.json       # 结构化，含 image_urls + images + local_images + replies
├── replies.csv      # 回复扁平表，一行一条回复，用 tid 关联主贴
├── replies.json     # 回复结构化数据
└── images/<tid>/    # 每帖一个文件夹；主贴图 1.jpg/2.jpg，回复图 r<楼>_<n>.jpg
```

**字段说明**
- 主贴（posts）：`index, tid, title, author, post_time, like_count, reply_count, reply_count_crawled, list_date, image_count, images, local_images, image_urls, content, replies`
- 回复（replies）：`tid, floor, author, time, post_time, image_count, images, content`
- `reply_count` 是贴吧显示的总回复数；`reply_count_crawled` 是实际抓到的回复数（首页约30条，非全部）

> **图片路径**：`images` / `local_images` 存**本地相对路径**（如 `images/108xxx/1.jpg`，前端经 `/community-images/` 访问）；`image_urls` 存贴吧**远程 URL**（带令牌，可直接 `<img src>` 引用）。打开 `images/` 看到一堆文件夹、根目录没有散落图片，**属正常**，不要误判为"没图"。

---

## 4. 模块职责（改东西前先读这段）

| 函数 | 职责 | 关键约束 |
|---|---|---|
| `force_ipv4(enable)` | 强制 socket 走 IPv4 | 解决"连接建立但不返回数据 / Read timed out"的头号原因（IPv6 路由残缺）。默认开启 |
| `is_security_check(r)` | 检测是否触发百度安全验证 | 403 或页面含"安全验证"/"Bioc" 即判定 |
| `safe_get(session,url,**kw)` | 带重试的 GET | 重试 `MAX_RETRY` 次；**返回 `(response, error_type)`**：`None` 成功 / `security_check` / `request_error` |
| `is_ad_account(author)` | 作者是否广告账号 | 命中 `AD_ACCOUNT_HINTS`（广告/推广/赞助/贴吧广告…）整帖/整条回复判广告 |
| `_el_is_ad(el)` / `block_has_ad(block)` | 判断 DOM 元素/块是否贴吧注入广告 | 按 **class 关键字** 或 **独立广告标签文字** 判定 |
| `parse_list_wap(html)` | 解析 WAP 列表页 | 兼容 `kz=` 与 `/p/` 两种链接；提取点赞/回复数/is_pinned；返回 `(threads, 下一页链接)`；**不过滤正文含"广告"二字的正常帖** |
| `collect_via_bduss(...)` | 登录态列表通道 | 调 `c/f/frs/page`，50/页，点赞数来自 `agree_num`；返回 `(list, status)` |
| `collect_via_wap(...)` | 未登录列表通道 | `lp=5011&lm=0&pn=0/20/40/60/80` 分页 + `tn=bdFBW` 首页兜底；返回 `(list, status)` |
| `decode_img_url(data_url)` | 解出可下载图片地址 | ⚠️ **必须返回完整带令牌地址**，不要只抠 `pic/item/` 段（见 §5） |
| `_extract_post(content_div, is_op)` | 从单个 `div.content` 提取一条（主贴或回复） | 主贴与回复**共用**；命中广告账号返回 `{"__ad__":True}`；剔除注入广告子块 |
| `parse_detail(html, want_replies=True)` | 解析详情页：楼主主贴 + 首页回复（约30条） | 楼主用 `lz="1"`；回复楼层从 2 开始；回复复用广告过滤；返回 `{..., replies:[...]}` |
| `parse_replies(html)` | 兼容包装：只取回复列表 | 旧接口，供历史代码调用 |
| `download_images(..., prefix="")` | 下载并落盘 | 有**占位图防火墙**（见 §5）；`prefix` 用于回复图命名 `r<楼>_<n>.jpg` |
| `run_scraper(...)` | **后端调用入口** | 返回 `(results, status)`，status 含 `ok/security_blocked/message/post_count/reply_count`；写 posts.json/replies.json |
| `main()` | CLI 入口 | 解析参数后调 `run_scraper()` |

---

## 5. 两个"修了很久才搞定"的坑（改图相关代码前必读）

### 坑 A：图片全是同一张占位图（已修复，勿回退）
- **现象**：每个帖子都下载了同一张 238×238 灰图，MD5 恒为 `e9fa8e3af5`。
- **根因**：旧 `decode_img_url` 只从 `pb_img_item` 的 `data-url` 抠出 `pic/item/<hash>.jpg` 一小段，**丢掉了百度图床必须的 `sign` 签名 + `tbpicau` 时效令牌**。百度对缺令牌地址统一返回占位图。
- **修复**：`decode_img_url` 直接返回**完整 data-url**（带令牌），实测 `w=250` 可下到真实 250×255 照片。
- **防火墙**：`download_images` 用 `PLACEHOLDER_MD5 = "e9fa8e3af5"` 识别占位图，既不入库也支持重跑覆盖。改图片逻辑时**务必保留这道防火墙**。

### 坑 B：Read timed out / 连接挂起（网络层，非脚本 bug）
- **根因**：系统把 `tieba.baidu.com` 解析到不通的 IPv6 地址 → 默认开启 `force_ipv4` 缓解。
- **叠加因素**：百度对云服务器 IP 间歇 403 限流；公司/校园网代理会"吞"响应。
- **对策优先级**：① 加大 `--timeout`；② 默认已强制 IPv4，仍不行试 `--no-ipv4`；③ 换手机热点；④ 上 `--bduss`。

---

## 6. 广告过滤总原则（避免误杀）

设计哲学：**判定广告只看"广告账号作者"和"贴吧注入的独立广告标签/class"，绝不因正文里出现"广告"二字就删帖。**

- 曾误杀「近期招聘一批吧务」帖（正文提到"外网广告类"被关键词误判）——已改为只看广告账号/注入标签。
- 评论区广告因只抓 `lz="1"` 主贴，**天然不会进数据**，无需额外处理。
- 若未来要新增广告识别：加到 `AD_ACCOUNT_HINTS` / `AD_LABELS` / `AD_CLASS_HINTS`，**保持"精准标签匹配"而非"正文关键词模糊匹配"**。

---

## 7. 故障排查清单（Agent 接手后照着走）

**Q1：一个帖都没抓到 / 全 timeout**
1. 浏览器开 `https://tieba.baidu.com` 确认站点可达；
2. 看启动日志的 `强制IPv4` 与 `读取 Xs` 是否符合预期；
3. 加 `--timeout 60`；
4. 换网络（热点）；
5. 上 `--bduss`。

**Q2：只抓到 ~20 帖（预期 100）**
→ 未登录 + 当前 IP 被百度封翻页。这是**环境限制，脚本无 bug**。解法：家庭 IP 重跑，或加 `--bduss`。

**Q3：图片文件夹"是空的"**
→ 先确认不是"子文件夹里没图"（见 §3）。若真 0 图且帖子本应有图：检查是否占位图防火墙误杀（看日志 `[!] 跳过占位图`）、或 `decode_img_url` 又被改回抠段模式（见 §5 坑 A）。

**Q4：CSV 打开中文乱码**
→ 已用 `utf-8-sig` 写 CSV，Excel 应正常。若仍乱码，检查打开方式，不是脚本问题。

---

## 8. 二次开发红线

- ✅ 可改：翻页节奏（`SLEEP_SEC`）、重试次数（`MAX_RETRY`）、广告词表（§6）、超时默认值。
- ✅ 可加：字段（在 `parse_detail` 增提取 + `main()` 写文件处加列）。
- ❌ **勿回退** `decode_img_url` 为"只抠 pic/item 段"（会复现坑 A）。
- ❌ **勿删** `download_images` 的占位图防火墙。
- ❌ **勿把广告判定改成"正文关键词模糊匹配"**（会复现误杀）。
- ⚠️ 改列表解析前，先用本地保存的 `wap_list.html` / `wap_detail.html` 离线验证 `parse_list_wap` / `parse_detail`，不要每改一次就打真实请求（易触发限流）。

---

## 9. 实测基线（供回归对照）

| 场景 | 结果 |
|---|---|
| 云服务器 IP 实跑 | 20 帖 / 13 真实照片，广告已排除，无占位图混入 |
| 离线解析器 | `parse_list_wap` 正确解析 20 帖 + 点赞/回复数 + 下一页链接 |
| 图片去重/覆盖 | 已存在非占位图跳过；占位图自动删后重下 |

> 若后续在你的环境跑出明显偏离上述基线的结果（如突然全占位图、突然大量误杀广告），优先回到本说明书 §5、§6、§7 定位，不要先怀疑"脚本没写对"。
