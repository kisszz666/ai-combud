#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
百度贴吧「电脑吧」帖子爬虫（实时抓取前 N 帖）
================================================
特性：
  - 每次启动都实时抓取该贴吧当前最新的前 N 个帖子（默认 100，不写死）。
  - 字段：发帖人ID、发帖时间、帖子内容、点赞数、照片（自动下载到本地）。
  - 自动排除广告：列表页推广帖 + 详情页被注入的推广/广告区块；只抓“楼主主贴”
    (lz="1")，天然不抓取评论区广告。
  - 多策略列表抓取，确保在不同网络环境下尽量翻满目标数量：
      ① 提供 --bduss（登录cookie）→ 官方JSON列表接口，50/页，稳定 100；
      ② 未登录 → WAP “最新”分页接口（lp=5011&lm=0&pn=...），家庭IP可翻满 100；
      ③ 兜底 → WAP 首页（tn=bdFBW）。

依赖：pip install requests beautifulsoup4 lxml
用法：
  python tieba_scraper.py                      # 默认爬 电脑吧 前100帖
  python tieba_scraper.py --limit 5           # 试跑前5帖
  python tieba_scraper.py --kw 显卡            # 换贴吧
  python tieba_scraper.py --bduss "你的BDUSS"  # 登录态，稳定翻满100（推荐）

输出（./tieba_<贴吧名>/output/）：
  posts.csv       扁平表，每帖一行
  posts.json      结构化数据（含图片原始URL与本地路径）
  images/<tid>/   每帖照片
"""

import os
import re
import sys
import csv
import time
import json
import math
import random
import argparse
import urllib.parse
import warnings
from html import unescape

try:
    import requests
except ImportError:
    sys.exit("缺少依赖，请先运行: pip install requests beautifulsoup4 lxml")

try:
    from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except ImportError:
    sys.exit("缺少依赖，请先运行: pip install beautifulsoup4 lxml")

try:
    import lxml  # noqa
    _PARSER = "lxml"
except Exception:
    _PARSER = "html.parser"

import socket


def force_ipv4(enable=True):
    """强制所有连接走 IPv4。

    很多“连接能建立但一直不返回数据 / Read timed out”的现象，根因是系统把
    tieba.baidu.com 解析到了一个不通的 IPv6 地址（v6 路由残缺），TCP 握手卡死。
    强制 IPv4 通常能直接解决。
    """
    if not enable:
        return
    _orig = socket.getaddrinfo

    def _getaddrinfo_ipv4(host, port, family=0, type=0, proto=0, flags=0):
        return _orig(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = _getaddrinfo_ipv4


# ------------------------- 配置 -------------------------
BAR_NAME   = "电脑"          # 贴吧名称（中文即可，脚本自动编码）
PAGE_LIMIT = 100             # 目标帖子数量
SLEEP_SEC  = 1.0             # 每次请求间隔（秒），降低被风控概率
MAX_RETRY  = 3               # 单请求失败重试次数
TIMEOUT    = (10, 30)        # (连接超时, 读取超时) 秒；网络慢可加大读取超时
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
      "Mobile/15E148 Safari/604.1")
UA_DESK = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 广告账号特征（作者命中则整帖视为广告）
AD_ACCOUNT_HINTS = ["广告", "推广", "赞助", "百度推广", "贴吧广告", "商务合作"]
# 独立的广告标签文字（精准匹配，避免误伤正文含“广告”二字的正常帖）
AD_LABELS = {"广告", "推广", "赞助", "赞助商", "广告主"}
# 广告区块的 class 特征（用于剔除详情页主贴内被注入的推广子块）
AD_CLASS_HINTS = ("ad_", "promote", "sponsor", "guess", "_ad", "adv")

# 列表接口
WAP_LIST     = "https://tieba.baidu.com/mo/q/m?kw={kw}&lp=5011&lm=0&pn={pn}"
WAP_LIST_FBW = "https://tieba.baidu.com/mo/m?kw={kw}&tn=bdFBW"
DETAIL_URL   = "https://tieba.baidu.com/mo/m?kz={tid}"
FRS_API      = "https://tieba.baidu.com/c/f/frs/page?kw={kw}&pn={pn}&rn=50&tbs={tbs}"


# ------------------------- 工具函数 -------------------------
def is_security_check(r):
    """检测是否触发了百度安全验证。"""
    if r is None:
        return False
    if r.status_code == 403:
        return True
    try:
        t = r.text
    except Exception:
        return False
    return ("安全验证" in t) or ("Bioc" in t) or ("bioc" in t.lower())


def safe_get(session, url, **kw):
    """带重试的 GET，返回 (response, error_type) 元组。
    error_type: None=成功, 'security_check'=安全验证, 'request_error'=请求错误
    """
    kw.setdefault("timeout", TIMEOUT)
    headers = kw.pop("headers", {})
    for attempt in range(1, MAX_RETRY + 1):
        try:
            r = session.get(url, headers=headers, **kw)
            if is_security_check(r):
                print("  [WARN] 触发百度安全验证！")
                return (None, "security_check")
            if r.status_code == 200 and len(r.content) > 500:
                return (r, None)
            print(f"  [!] 状态码 {r.status_code} / 长度 {len(r.content)}，重试({attempt})")
        except Exception as e:
            print(f"  [!] 请求异常 {e}，重试({attempt})")
        time.sleep(SLEEP_SEC * attempt)
    return (None, "request_error")


def is_ad_account(author):
    """作者是否为广告账号（如“贴吧广告”）。"""
    if not author:
        return False
    return any(h in author for h in AD_ACCOUNT_HINTS)


def _el_is_ad(el):
    """单个 DOM 元素是否为贴吧注入的广告子块（按 class / 独立广告标签判断）。"""
    cls = " ".join(el.get("class", [])).lower()
    if any(h in cls for h in AD_CLASS_HINTS):
        return True
    if el.get_text(strip=True) in AD_LABELS:
        return True
    return False


def block_has_ad(block):
    """块内是否含有贴吧注入的广告标记（用于列表页过滤推广帖）。"""
    if block is None:
        return False
    return any(_el_is_ad(el) for el in block.find_all(True))


# ------------------------- 列表页解析（WAP） -------------------------
def parse_list_wap(html):
    """解析 WAP 列表页，返回帖子列表与“下一页”链接。兼容 kz= 与 /p/ 两种链接格式。"""
    soup = BeautifulSoup(html, _PARSER)
    threads = []
    for a in soup.find_all("a"):
        href = a.get("href", "")
        m = re.search(r"kz=(\d+)", href) or re.search(r"/p/(\d+)", href)
        if not m:
            continue
        tid = m.group(1)
        title = re.sub(r"^\s*\d+\.\s*", "", a.get_text(strip=True))
        block = a.find_parent("div", class_="i") or a.parent
        btext = block.get_text(" ", strip=True) if block else ""

        # 广告过滤：含贴吧注入的广告标记（按独立标签/class，避免误伤正常帖）
        if block_has_ad(block):
            print(f"  [广告] 跳过列表帖: {title[:30]}")
            continue

        like = reply = 0
        dm = re.search(r"点\s*(\d+)\s*回\s*(\d+)", btext)
        if dm:
            like, reply = int(dm.group(1)), int(dm.group(2))
        date = ""
        dm2 = re.search(r"(\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2})?)", btext)
        if dm2:
            date = dm2.group(1)
        is_pinned = bool(re.search(r"\[顶\]|\[精\]|置顶", btext))

        threads.append({
            "tid": tid,
            "title": title,
            "author": "",
            "like_count": like,
            "reply_count": reply,
            "list_date": date,
            "is_pinned": is_pinned,
            "detail_url": DETAIL_URL.format(tid=tid),
        })

    nxt = None
    for a in soup.find_all("a"):
        if "下一页" in a.get_text(strip=True):
            nxt = a.get("href")
            break
    return threads, nxt


# ------------------------- 列表页抓取（BDUSS 官方接口） -------------------------
def get_tbs(session):
    try:
        r = session.get("https://tieba.baidu.com/dc/common/tbs", timeout=TIMEOUT)
        return r.json().get("tbs")
    except Exception:
        return None


def collect_via_bduss(session, kw_enc, limit, bduss):
    """使用登录态 BDUSS 调官方 JSON 列表接口，50/页，稳定翻满目标数。
    返回 (collected, status)，status ∈ {'ok','security_blocked','empty'}。"""
    session.cookies.set("BDUSS", bduss, domain=".baidu.com")
    tbs = get_tbs(session)
    if not tbs:
        print("  [!] 获取 tbs 失败，BDUSS 可能无效")
        return [], "empty"
    collected, seen = [], set()
    status = "empty"
    pages = math.ceil(limit / 50) + 1
    headers = {"User-Agent": UA_DESK, "Accept": "application/json, text/plain, */*",
               "Referer": "https://tieba.baidu.com/f?kw=" + kw_enc}
    for pn in range(1, pages + 1):
        if len(collected) >= limit:
            break
        url = FRS_API.format(kw=kw_enc, pn=pn, tbs=tbs)
        r, err = safe_get(session, url, headers=headers)
        if err == "security_check":
            return collected, "security_blocked"
        if not r:
            break
        try:
            d = r.json()
        except Exception:
            break
        tl = d.get("thread_list") or []
        if not tl:
            break
        for t in tl:
            tid = str(t.get("thread_id") or t.get("id") or "")
            if not tid or tid in seen:
                continue
            seen.add(tid)
            author = t.get("author_name") or (t.get("author") or {}).get("name", "")
            if is_ad_account(author):
                continue
            collected.append({
                "tid": tid,
                "title": (t.get("title") or "").strip(),
                "author": author,
                "like_count": int(t.get("agree_num") or 0),
                "reply_count": int(t.get("reply_num") or 0),
                "list_date": "",
                "is_pinned": False,
                "detail_url": DETAIL_URL.format(tid=tid),
            })
            if len(collected) >= limit:
                break
        time.sleep(SLEEP_SEC)
    if collected:
        status = "ok"
    return collected[:limit], status


# ------------------------- 列表收集（WAP 分页 + 兜底） -------------------------
def collect_via_wap(session, kw_enc, limit):
    """返回 (collected, status)，status ∈ {'ok','security_blocked','empty'}。"""
    collected, seen = [], set()
    blocked = False
    # 1) WAP “最新”分页：pn = 0,20,40,60,80 ...
    for pn in range(0, max(limit, 100) + 1, 20):
        if len(collected) >= limit:
            break
        r, err = safe_get(session, WAP_LIST.format(kw=kw_enc, pn=pn))
        if err == "security_check":
            blocked = True
            break
        if not r:
            continue
        threads, _ = parse_list_wap(r.text)
        if not threads:
            break  # 分页结束或被限流
        for t in threads:
            if t["tid"] in seen:
                continue
            seen.add(t["tid"])
            collected.append(t)
            if len(collected) >= limit:
                break
        time.sleep(SLEEP_SEC)
    # 2) 兜底：WAP 首页
    if len(collected) < limit:
        r, err = safe_get(session, WAP_LIST_FBW.format(kw=kw_enc))
        if err == "security_check":
            blocked = True
        elif r:
            threads, _ = parse_list_wap(r.text)
            for t in threads:
                if t["tid"] in seen:
                    continue
                seen.add(t["tid"])
                collected.append(t)
                if len(collected) >= limit:
                    break
    if collected:
        return collected[:limit], "ok"
    return collected[:limit], ("security_blocked" if blocked else "empty")


# ------------------------- 详情页解析（WAP，已验证） -------------------------
def decode_img_url(data_url):
    """从 pb_img_item 的 data-url 中解出可下载的原图地址。

    关键点：data-url 本身已是完整且带 sign/tbpicau 令牌的图床地址，
    直接用它就能下到真实图片。若只抽取 pic/item 段会丢失令牌，
    百度会返回统一的 238x238 占位图（所有帖子都变成同一张）。
    """
    du = unescape(data_url).strip()
    if du.startswith("http://") or du.startswith("https://"):
        return du
    # 兜底：极少数格式把原图放在 src 参数里
    q = urllib.parse.parse_qs(urllib.parse.urlparse(du).query)
    src = q.get("src")
    if src:
        return src[0]
    return None


def _extract_post(content_div, is_op=False):
    """从单个 div.content 提取一条帖子/回复信息（作者/时间/内容/图片）。

    主贴与回复共用此函数。命中广告账号返回 {"__ad__": True}。
    """
    # 容器（用于取作者/时间），最多向上找 4 层
    root = content_div
    for _ in range(4):
        root = root.parent
        if root is None:
            break

    # 作者
    author = ""
    un = root.find("span", class_="user_name") if root else None
    if un:
        author = un.get_text(strip=True)
    if is_ad_account(author):
        return {"__ad__": True}

    # 时间
    post_time = ""
    if root:
        tm = root.find("span", class_="list_item_time")
        if tm:
            post_time = tm.get_text(strip=True)

    # 剔除被贴吧注入的推广/广告子块（按 class / 独立广告标签）
    for el in content_div.find_all(True):
        if _el_is_ad(el):
            el.decompose()

    # 内容（<br> 转换行）
    for br in content_div.find_all("br"):
        br.replace_with("\n")
    content = content_div.get_text("\n").strip()
    content = re.sub(r"\n{3,}", "\n\n", content)

    # 图片
    images = []
    for item in content_div.find_all("div", class_="pb_img_item"):
        du = item.get("data-url")
        if du:
            u = decode_img_url(du)
            if u and u not in images:
                images.append(u)
    # 兜底：直接出现在内容里的贴吧图床图片
    for img in content_div.find_all("img"):
        src = img.get("src") or ""
        if any(h in src for h in ("tiebapic", "imgsrc.baidu", "hiphotos")):
            if "user_img" in (img.get("class") or []) or "emoticon" in src:
                continue
            u = src if src.startswith("http") else "http:" + src
            if u not in images:
                images.append(u)

    return {"author": author, "post_time": post_time, "content": content, "images": images}


def parse_detail(html, want_replies=True):
    """提取楼主主贴（lz="1"）；可选抓取详情页首页回复（约30条）。

    返回 dict：{author, post_time, content, images, replies:[...]}。
    命中广告账号返回 {"__ad__": True}；未找到主贴返回 None。
    回复复用广告过滤：广告账号 / 注入广告子块自动剔除。
    回复楼层从 2 开始（楼主为 1 楼），按页面顺序编号。
    """
    soup = BeautifulSoup(html, _PARSER)
    contents = soup.find_all("div", class_="content")
    op_div = next((c for c in contents if c.get("lz") == "1"), None)
    if not op_div:
        return None

    op = _extract_post(op_div, is_op=True)
    if op.get("__ad__"):
        return {"__ad__": True}

    replies = []
    if want_replies:
        floor = 2  # 楼主为 1 楼
        for c in contents:
            if c.get("lz") == "1":
                continue
            rp = _extract_post(c, is_op=False)
            if rp.get("__ad__"):
                continue
            rp["floor"] = floor
            replies.append(rp)
            floor += 1
    op["replies"] = replies
    return op


# ------------------------- 图片下载 -------------------------
# 百度对“裸地址/缺令牌”的请求会返回统一的 238x238 占位图，MD5 固定为下面这个值。
# 用它做防火墙：既不让占位图混入结果，也能在重跑时自动覆盖历史假图。
PLACEHOLDER_MD5 = "e9fa8e3af5"


def _md5(b):
    import hashlib
    return hashlib.md5(b).hexdigest()[:10]


def _looks_like_image(b):
    return b[:3] == b"\xff\xd8\xff" or b[:8] == b"\x89PNG\r\n\x1a\n"


def download_images(session, tid, image_urls, out_dir, prefix=""):
    saved = []
    img_dir = os.path.join(out_dir, "images", tid)
    os.makedirs(img_dir, exist_ok=True)
    for idx, url in enumerate(image_urls, 1):
        fn = f"{prefix}{idx}.jpg"
        path = os.path.join(img_dir, fn)
        rel = f"images/{tid}/{fn}"
        # 已存在且不是占位图 → 跳过
        if os.path.exists(path) and os.path.getsize(path) > 0:
            if _md5(open(path, "rb").read()) != PLACEHOLDER_MD5:
                saved.append(rel)
                continue
            os.remove(path)  # 是占位图，删掉重下
        r, _err = safe_get(session, url, headers={"Referer": "https://tieba.baidu.com/"})
        if r is None or not r.content:
            print(f"    [!] 图片下载失败: {url[:60]}")
            continue
        # 占位图 / 非图片（如错误页）直接丢弃
        if _md5(r.content) == PLACEHOLDER_MD5 or not _looks_like_image(r.content):
            print(f"    [!] 跳过占位图/无效图: {url[:60]}")
            continue
        try:
            with open(path, "wb") as f:
                f.write(r.content)
            saved.append(rel)
        except Exception as e:
            print(f"    [!] 写图失败: {e}")
        time.sleep(0.3)
    return saved


# ------------------------- 可复用爬取核心（供 CLI 与后端调用） -------------------------
def run_scraper(kw="电脑", limit=20, out_dir=None, bduss=None, timeout=30.0,
                no_ipv4=False, include_replies=True, progress_callback=None):
    """爬虫核心逻辑 —— 可被 CLI 和外部代码（如社区后端 community_service）直接调用。

    参数：
        kw:               贴吧名
        limit:            目标帖子数
        out_dir:          输出目录（None=自动生成 ./tieba_<kw>/output）
        bduss:            登录态 cookie（可选，提供最稳定翻页）
        timeout:          读取超时秒数（连接超时固定 10s）
        no_ipv4:          关闭“强制 IPv4”（默认开启）
        include_replies:  是否抓取评论
        progress_callback:进度回调 fn(msg: str)

    返回：(results list, status dict)
        status dict 包含: {'ok': bool, 'security_blocked': bool, 'message': str,
                          'post_count': int, 'reply_count': int}

    数据契约（与前端 community.html 对齐）：
        每条主贴: {index, tid, title, author, post_time, like_count, reply_count,
                   reply_count_crawled, list_date, content,
                   images[=本地相对路径 list], image_urls[=远程URL list],
                   local_images[=同 images，冗余字段], replies=[...]}
        每条回复: {tid, floor, author, time, post_time, content, image_count,
                   images[=本地相对路径], image_urls}
        注：images 存“本地相对路径”（前端通过 /community-images/ 访问），
            image_urls 存贴吧远程 URL（带令牌，可直接 <img src> 引用）。
    """
    global TIMEOUT
    TIMEOUT = (10, timeout)
    force_ipv4(not no_ipv4)

    def _log(msg):
        try:
            print(msg)
        except UnicodeEncodeError:
            print(msg.encode("ascii", errors="replace").decode("ascii"))
        if progress_callback:
            progress_callback(msg)

    status = {"ok": False, "security_blocked": False, "message": "",
              "post_count": 0, "reply_count": 0}

    kw_enc = urllib.parse.quote(kw)
    if out_dir is None:
        out_dir = os.path.join(os.getcwd(), f"tieba_{kw}", "output")
    os.makedirs(out_dir, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})

    # 1) 收集列表
    _log(f"开始收集「{kw}吧」前 {limit} 帖 ...")
    if bduss:
        collected, collect_status = collect_via_bduss(session, kw_enc, limit, bduss)
    else:
        collected, collect_status = collect_via_wap(session, kw_enc, limit)

    if collect_status == "security_blocked":
        status["security_blocked"] = True
        status["message"] = "贴吧触发安全验证，建议改用 --bduss 登录态"
        _log("[WARN] 列表页触发安全验证")
        return [], status
    if not collected:
        status["message"] = "列表收集为空（网络受限或目标不可达）"
        _log("[!] 列表收集为空")
        return [], status
    _log(f"已收集 {len(collected)} 帖，开始抓取详情与图片 ...")

    # 2) 逐个抓取详情
    results, all_replies = [], []
    for i, t in enumerate(collected, 1):
        # 跳过置顶公告/规则帖（无实质内容）
        if t.get("is_pinned") and t.get("reply_count", 0) < 10:
            _log(f"  [{i}/{len(collected)}] [跳过置顶] 《{t['title'][:24]}》")
            continue

        _log(f"  [{i}/{len(collected)}] tid={t['tid']} 《{t['title'][:24]}》")
        r, err = safe_get(session, t["detail_url"])
        if err == "security_check":
            status["security_blocked"] = True
            status["message"] = "详情页触发安全验证，抓取中断"
            _log("[WARN] 详情页触发安全验证，停止抓取")
            break
        if r is None:
            _log("    [!] 详情抓取失败，跳过")
            continue
        detail = parse_detail(r.text, want_replies=include_replies)
        if detail is None:
            _log("    [!] 未解析到主贴，跳过")
            continue
        if detail.get("__ad__"):
            _log("    [广告] 详情命中广告账号，跳过")
            continue

        author = detail["author"] or t.get("author", "")
        saved_imgs = download_images(session, t["tid"], detail["images"], out_dir)

        replies_data = []
        if include_replies:
            for rp in detail.get("replies", []):
                rp_imgs = download_images(session, t["tid"], rp["images"], out_dir,
                                          prefix=f"r{rp['floor']}_")
                replies_data.append({
                    "tid": t["tid"],
                    "floor": rp["floor"],
                    "author": rp["author"],
                    "time": rp["post_time"],        # 前端用 r.time
                    "post_time": rp["post_time"],   # 冗余，兼容旧数据
                    "content": rp["content"],
                    "image_count": len(rp_imgs),
                    "images": rp_imgs,
                    "image_urls": rp["images"],
                })
        all_replies.extend(replies_data)
        if replies_data:
            _log(f"    └ 回复 {len(replies_data)} 条（广告已过滤）")

        results.append({
            "index": len(results) + 1,
            "tid": t["tid"],
            "title": t["title"],
            "author": author,
            "post_time": detail["post_time"],
            "like_count": t["like_count"],
            "reply_count": t["reply_count"],
            "reply_count_crawled": len(replies_data),
            "list_date": t["list_date"],
            "content": detail["content"],
            "image_count": len(saved_imgs),
            "images": saved_imgs,          # 本地相对路径（前端用）
            "local_images": saved_imgs,    # 冗余，兼容旧前端字段
            "image_urls": detail["images"],  # 远程 URL
            "replies": replies_data,
        })
        time.sleep(SLEEP_SEC * (0.7 + 0.6 * random.random()))

    # 3) 写出文件
    if results:
        _log("正在保存数据 ...")
        csv_path = os.path.join(out_dir, "posts.csv")
        json_path = os.path.join(out_dir, "posts.json")
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            cols = ["index", "tid", "title", "author", "post_time", "like_count",
                    "reply_count", "reply_count_crawled", "list_date",
                    "image_count", "images", "content"]
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for rec in results:
                row = dict(rec)
                row.pop("image_urls", None)
                row.pop("replies", None)
                row.pop("local_images", None)
                row["images"] = ";".join(rec["images"])
                w.writerow(row)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        rep_csv = os.path.join(out_dir, "replies.csv")
        rep_json = os.path.join(out_dir, "replies.json")
        with open(rep_csv, "w", encoding="utf-8-sig", newline="") as f:
            cols = ["tid", "floor", "author", "time", "image_count", "images", "content"]
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for rp in all_replies:
                row = dict(rp)
                row.pop("image_urls", None)
                row.pop("post_time", None)
                row["images"] = ";".join(rp["images"])
                w.writerow(row)
        with open(rep_json, "w", encoding="utf-8") as f:
            json.dump(all_replies, f, ensure_ascii=False, indent=2)

    total_imgs = sum(r["image_count"] for r in results)
    status["ok"] = bool(results)
    status["post_count"] = len(results)
    status["reply_count"] = len(all_replies)
    if results:
        status["message"] = (f"成功抓取 {len(results)} 帖 / {len(all_replies)} 回复 / "
                             f"{total_imgs} 张图片")
        if status["security_blocked"]:
            status["message"] += "（中途触发安全验证，仅部分完成）"
    else:
        status["message"] = status.get("message") or "未能抓取任何帖子"
    _log(f"完成：{status['message']}")
    return results, status


# ------------------------- 兼容包装：parse_replies -------------------------
def parse_replies(html):
    """从详情页 HTML 提取回复列表（旧接口，供历史代码调用）。
    返回 [{'floor','author','time','content','images'}, ...]。"""
    d = parse_detail(html, want_replies=True)
    if not d or d.get("__ad__"):
        return []
    return [{"floor": r["floor"], "author": r["author"], "time": r["post_time"],
             "content": r["content"], "images": r["images"]}
            for r in d.get("replies", [])]


# ------------------------- 主流程（CLI） -------------------------
def main():
    ap = argparse.ArgumentParser(description="百度贴吧帖子爬虫")
    ap.add_argument("--kw", default=BAR_NAME, help="贴吧名称，如 电脑")
    ap.add_argument("--limit", type=int, default=PAGE_LIMIT, help="抓取帖子数量")
    ap.add_argument("--bduss", default=None, help="登录态 BDUSS cookie（稳定翻满100）")
    ap.add_argument("--out", default=None, help="输出目录")
    ap.add_argument("--timeout", type=float, default=30.0,
                    help="读取超时秒数（网络慢就调大，如 45 / 60）")
    ap.add_argument("--no-ipv4", action="store_true",
                    help="关闭“强制IPv4”（默认开启，用于解决IPv6导致的读取超时）")
    ap.add_argument("--no-replies", action="store_true", help="不抓取评论/回复（默认抓取）")
    args = ap.parse_args()

    out_dir = args.out or os.path.join(os.getcwd(), f"tieba_{args.kw}", "output")
    results, status = run_scraper(
        kw=args.kw, limit=args.limit, out_dir=out_dir, bduss=args.bduss,
        timeout=args.timeout, no_ipv4=args.no_ipv4,
        include_replies=not args.no_replies,
    )

    print()
    if status["security_blocked"]:
        print("[!] 触发百度安全验证：" + status["message"])
        print("    建议：加 --bduss \"你的BDUSS\" 走登录态官方接口。")
        sys.exit(2)
    if not status["ok"]:
        print("[!] " + status["message"])
        print("    排查顺序：")
        print("      1) 先用浏览器打开 https://tieba.baidu.com 确认能正常访问；")
        print("      2) 本脚本默认已强制 IPv4，若仍超时，试试 --no-ipv4 关掉它；")
        print("      3) 公司/校园网常有代理或防火墙会‘吞’掉响应，换手机热点再试；")
        print("      4) 最稳方案：加 --bduss 走登录态官方接口。")
        sys.exit(1)

    print(f"==> {status['message']}")
    print(f"    帖子 CSV : {os.path.join(out_dir, 'posts.csv')}")
    print(f"    帖子 JSON: {os.path.join(out_dir, 'posts.json')}")
    print(f"    回复 CSV : {os.path.join(out_dir, 'replies.csv')}")
    print(f"    回复 JSON: {os.path.join(out_dir, 'replies.json')}")
    print(f"    图片: {os.path.join(out_dir, 'images')}")
    if not args.bduss and len(results) < args.limit:
        print(f"    [提示] 未登录时百度对列表翻页有限制，仅抓到 {len(results)} 帖。"
              f"加 --bduss 登录态可稳定翻满 {args.limit} 帖。")


if __name__ == "__main__":
    main()
