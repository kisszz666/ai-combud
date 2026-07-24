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

# 协议配置（部分网络环境HTTPS会超时，HTTP反而可用）
USE_HTTP = True  # 设置为 False 可切换回 HTTPS
PROTOCOL = "http" if USE_HTTP else "https"

# 列表接口
WAP_LIST     = f"{PROTOCOL}://tieba.baidu.com/mo/q/m?kw={{kw}}&lp=5011&lm=0&pn={{pn}}"
WAP_LIST_FBW = f"{PROTOCOL}://tieba.baidu.com/mo/m?kw={{kw}}&tn=bdFBW"
DETAIL_URL   = f"{PROTOCOL}://tieba.baidu.com/mo/m?kz={{tid}}"
FRS_API      = f"{PROTOCOL}://tieba.baidu.com/c/f/frs/page?kw={{kw}}&pn={{pn}}&rn=50&tbs={{tbs}}"

# 其他需要完整域名的 URL
BASE_URL = f"{PROTOCOL}://tieba.baidu.com"


# ------------------------- 工具函数 -------------------------
def is_security_check(r):
    """检测是否触发了百度安全验证。"""
    if r is None:
        return False
    if r.status_code == 403:
        return True
    if "安全验证" in r.text or "Bioc" in r.text or "bioc" in r.text.lower():
        return True
    return False


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
                print(f"  [⚠️] 触发百度安全验证！需要登录或使用Selenium。")
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

        threads.append({
            "tid": tid,
            "title": title,
            "author": "",
            "like_count": like,
            "reply_count": reply,
            "list_date": date,
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
        r = session.get(f"{BASE_URL}/dc/common/tbs", timeout=TIMEOUT)
        return r.json().get("tbs")
    except Exception:
        return None


def check_response(r, log_prefix=""):
    """检查 safe_get 返回值，返回 (response, error_type)。"""
    if isinstance(r, tuple):
        return r
    return (r, None)


def collect_via_bduss(session, kw_enc, limit, bduss):
    """使用登录态 BDUSS 调官方 JSON 列表接口，50/页，稳定翻满目标数。"""
    session.cookies.set("BDUSS", bduss, domain=".baidu.com")
    tbs = get_tbs(session)
    if not tbs:
        print("  [!] 获取 tbs 失败，BDUSS 可能无效")
        return [], "auth_failed"
    collected, seen = [], set()
    pages = math.ceil(limit / 50) + 1
    headers = {"User-Agent": UA_DESK, "Accept": "application/json, text/plain, */*",
               "Referer": f"{BASE_URL}/f?kw=" + kw_enc}
    security_blocked = False
    for pn in range(1, pages + 1):
        if len(collected) >= limit:
            break
        url = FRS_API.format(kw=kw_enc, pn=pn, tbs=tbs)
        r, err = safe_get(session, url, headers=headers)
        if err == "security_check":
            security_blocked = True
            break
        if r is None:
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
                "detail_url": DETAIL_URL.format(tid=tid),
            })
            if len(collected) >= limit:
                break
        time.sleep(SLEEP_SEC)
    status = "security_blocked" if security_blocked else "ok"
    return collected[:limit], status


# ------------------------- 列表收集（WAP 分页 + 兜底） -------------------------
def collect_via_wap(session, kw_enc, limit):
    collected, seen = [], set()
    security_blocked = False
    # 1) WAP "最新"分页：pn = 0,20,40,60,80 ...
    for pn in range(0, max(limit, 100) + 1, 20):
        if len(collected) >= limit:
            break
        r, err = safe_get(session, WAP_LIST.format(kw=kw_enc, pn=pn))
        if err == "security_check":
            security_blocked = True
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
    if len(collected) < limit and not security_blocked:
        r, err = safe_get(session, WAP_LIST_FBW.format(kw=kw_enc))
        if err == "security_check":
            security_blocked = True
        elif r:
            threads, _ = parse_list_wap(r.text)
            for t in threads:
                if t["tid"] in seen:
                    continue
                seen.add(t["tid"])
                collected.append(t)
                if len(collected) >= limit:
                    break
    status = "security_blocked" if security_blocked else "ok"
    return collected[:limit], status


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


def parse_detail(html):
    """提取楼主主贴（lz="1"）的内容、作者、时间、图片。"""
    soup = BeautifulSoup(html, _PARSER)
    op = soup.find("div", class_="content", attrs={"lz": "1"})
    if not op:
        return None

    # 主贴容器（用于取作者/时间），最多向上找 4 层
    root = op
    for _ in range(4):
        root = root.parent
        if root is None:
            break

    # 作者
    author = ""
    un = root.find("span", class_="user_name")
    if un:
        author = un.get_text(strip=True)
    # 广告账号直接判定为广告帖并跳过
    if is_ad_account(author):
        return {"__ad__": True}

    # 时间
    post_time = ""
    tm = root.find("span", class_="list_item_time")
    if tm:
        post_time = tm.get_text(strip=True)

    # 剔除主贴内被贴吧注入的推广/广告子块（按 class / 独立广告标签）
    for el in op.find_all(True):
        if _el_is_ad(el):
            el.decompose()

    # 内容（<br> 转换行）
    for br in op.find_all("br"):
        br.replace_with("\n")
    content = op.get_text("\n").strip()
    content = re.sub(r"\n{3,}", "\n\n", content)

    # 图片
    images = []
    for item in op.find_all("div", class_="pb_img_item"):
        du = item.get("data-url")
        if du:
            u = decode_img_url(du)
            if u and u not in images:
                images.append(u)
    # 兜底：直接出现在内容里的贴吧图床图片
    for img in op.find_all("img"):
        src = img.get("src") or ""
        if any(h in src for h in ("tiebapic", "imgsrc.baidu", "hiphotos")):
            if "user_img" in (img.get("class") or []) or "emoticon" in src:
                continue
            u = src if src.startswith("http") else "http:" + src
            if u not in images:
                images.append(u)

    return {
        "author": author,
        "post_time": post_time,
        "content": content,
        "images": images,
    }


# ------------------------- 评论 / 回复 解析 -------------------------

def parse_replies(html):
    """从 WAP 详情页解析所有回复（跳过广告、吧务标记等非真实回复）。
    返回 list[dict]，每条含 author / content / time。
    """
    soup = BeautifulSoup(html, _PARSER)
    replies = []

    candidates = []
    for cls in ("reply_item", "reply_box", "lzl_item", "reply_list_item"):
        candidates.extend(soup.find_all("div", class_=cls))
    if not candidates:
        for tag in ("div", "li"):
            candidates.extend(soup.find_all(tag, class_=lambda c: c and "reply" in c.lower() if c else False))

    seen_texts = set()
    for item in candidates:
        # 跳过主贴（楼主层）
        if item.get("lz") == "1":
            continue
        if _el_is_ad(item):
            continue
        txt_all = item.get_text(" ", strip=True)
        if not txt_all or txt_all in AD_LABELS:
            continue

        # 作者
        author = ""
        un = (item.find("span", class_="user_name") or
              item.find("a", class_="user_name") or
              item.find(class_=lambda c: c and "user" in str(c).lower() if c else False))
        if un:
            author = un.get_text(strip=True)
        if not author:
            m = re.match(r"^([^\s:：]+)[:：]", txt_all)
            if m:
                author = m.group(1)
        if is_ad_account(author):
            continue

        # 内容
        content = ""
        cd = (item.find("div", class_=lambda c: c and "content" in str(c).lower() if c else False) or
              item.find("span", class_=lambda c: c and "content" in str(c).lower() if c else False) or
              item.find("p"))
        if cd:
            for br in cd.find_all("br"):
                br.replace_with("\n")
            content = cd.get_text("\n").strip()
        if not content:
            content = re.sub(r"^[^\s:：]+[:：]\s*", "", txt_all).strip()
        if not content or len(content) < 2:
            continue

        # 去重
        dedup_key = f"{author}|{content[:30]}"
        if dedup_key in seen_texts:
            continue
        seen_texts.add(dedup_key)

        # 时间
        time_str = ""
        tm = (item.find("span", class_=lambda c: c and "time" in str(c).lower() if c else False) or
              item.find(class_=lambda c: c and "time" in str(c).lower() if c else False))
        if tm:
            time_str = tm.get_text(strip=True)
        if not time_str:
            m = re.search(r"(\d{1,2}:\d{2}|\d{4}-\d{2}-\d{2}|\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2})", txt_all)
            if m:
                time_str = m.group(1)

        replies.append({"author": author, "content": content, "time": time_str or ""})

    return replies


# ------------------------- 图片下载 -------------------------
# 百度对“裸地址/缺令牌”的请求会返回统一的 238x238 占位图，MD5 固定为下面这个值。
# 用它做防火墙：既不让占位图混入结果，也能在重跑时自动覆盖历史假图。
PLACEHOLDER_MD5 = "e9fa8e3af5"


def _md5(b):
    import hashlib
    return hashlib.md5(b).hexdigest()[:10]


def _looks_like_image(b):
    return b[:3] == b"\xff\xd8\xff" or b[:8] == b"\x89PNG\r\n\x1a\n"


def download_images(session, tid, image_urls, out_dir):
    saved = []
    img_dir = os.path.join(out_dir, "images", tid)
    os.makedirs(img_dir, exist_ok=True)
    for idx, url in enumerate(image_urls, 1):
        fn = f"{idx}.jpg"
        path = os.path.join(img_dir, fn)
        rel = f"images/{tid}/{fn}"
        # 已存在且不是占位图 → 跳过
        if os.path.exists(path) and os.path.getsize(path) > 0:
            if _md5(open(path, "rb").read()) != PLACEHOLDER_MD5:
                saved.append(rel)
                continue
            os.remove(path)  # 是占位图，删掉重下
        r, err = safe_get(session, url, headers={"Referer": BASE_URL + "/"})
        if r is None or not r.content:
            if err == "security_check":
                print(f"    [⚠️] 图片下载被安全验证阻止")
            else:
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


# ------------------------- 可复用的核心入口 -------------------------

def run_scraper(kw="电脑", limit=20, out_dir=None, bduss=None, timeout=30.0,
                no_ipv4=False, progress_callback=None, include_replies=True):
    """爬虫核心逻辑 —— 可被 CLI 和外部代码（如社区后端）直接调用。

    参数：
        kw:               贴吧名
        limit:            目标帖子数
        out_dir:          输出目录（None=自动生成）
        bduss:            登录态 cookie
        timeout:          读取超时秒数
        no_ipv4:          关闭强制 IPv4
        progress_callback:进度回调 fn(msg: str)
        include_replies:  是否抓取评论

    返回：(results list, status dict)
        status dict 包含: {'ok': bool, 'security_blocked': bool, 'message': str}
    """
    global TIMEOUT
    TIMEOUT = (10, timeout)
    force_ipv4(not no_ipv4)

    def _log(msg):
        print(msg)
        if progress_callback:
            progress_callback(msg)

    status = {'ok': False, 'security_blocked': False, 'message': ''}

    _log(f"[配置] 目标: {kw}吧 前 {limit} 帖 | 协议: {PROTOCOL} | 超时: {TIMEOUT[1]}s | 强制IPv4: {not no_ipv4}")

    kw_enc = urllib.parse.quote(kw)
    if out_dir is None:
        out_dir = os.path.join(os.getcwd(), f"tieba_{kw}", "output")
    os.makedirs(out_dir, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})

    # 1) 收集列表
    _log(f"开始收集「{kw}吧」帖子列表...")
    if bduss:
        collected, collect_status = collect_via_bduss(session, kw_enc, limit, bduss)
    else:
        collected, collect_status = collect_via_wap(session, kw_enc, limit)
    
    if collect_status == "security_blocked":
        status['security_blocked'] = True
        status['message'] = '贴吧触发安全验证，需要登录或使用Selenium模式'
        _log("[⚠️] 触发安全验证！建议使用 --bduss 登录态或改用 Selenium 模式")
        return [], status
    
    if not collected:
        status['message'] = '列表收集为空，无法获取帖子'
        _log("[!] 列表收集为空")
        return [], status
    
    _log(f"已收集 {len(collected)} 帖，开始抓取详情...")

    # 2) 逐个抓取
    results = []
    for i, t in enumerate(collected, 1):
        _log(f"  [{i}/{len(collected)}] 《{t['title'][:24]}》")
        r, err = safe_get(session, t["detail_url"])
        if err == "security_blocked":
            status['security_blocked'] = True
            status['message'] = '详情页触发安全验证'
            _log("[⚠️] 详情页触发安全验证，停止抓取")
            break
        if r is None:
            continue
        detail = parse_detail(r.text)
        if detail is None or detail.get("__ad__"):
            continue

        author = detail["author"] or t.get("author", "")
        saved_imgs = download_images(session, t["tid"], detail["images"], out_dir)

        replies = []
        if include_replies:
            replies = parse_replies(r.text)

        rec = {
            "index": i,
            "tid": t["tid"],
            "title": t["title"],
            "author": author,
            "post_time": detail["post_time"],
            "like_count": t["like_count"],
            "reply_count": t["reply_count"],
            "list_date": t["list_date"],
            "content": detail["content"],
            "image_count": len(saved_imgs),
            "images": detail["images"],          # 原始 URL（带令牌，前端可直接引用）
            "local_images": saved_imgs,          # 本地相对路径
            "replies": replies,
        }
        results.append(rec)
        time.sleep(SLEEP_SEC * (0.5 + 0.5 * random.random()))

    # 3) 写出文件
    if results:
        _log("正在保存数据...")
        csv_path = os.path.join(out_dir, "posts.csv")
        json_path = os.path.join(out_dir, "posts.json")

        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            cols = ["index", "tid", "title", "author", "post_time", "like_count",
                    "reply_count", "list_date", "image_count", "images", "content"]
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for rec in results:
                row = dict(rec)
                row.pop("image_urls", None)
                row.pop("replies", None)
                row["images"] = ";".join(rec.get("local_images", []))
                w.writerow(row)

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        total_imgs = sum(r.get("image_count", 0) for r in results)
        _log(f"完成！成功 {len(results)} 帖，下载图片 {total_imgs} 张")
        status['ok'] = True
        status['message'] = f"成功 {len(results)} 帖，下载 {total_imgs} 张图片"

    return results, status


# ------------------------- 命令行入口 -------------------------
def main():
    ap = argparse.ArgumentParser(description='百度贴吧帖子爬虫')
    ap.add_argument('--kw', default=BAR_NAME, help='贴吧名称，如 电脑')
    ap.add_argument('--limit', type=int, default=PAGE_LIMIT, help='抓取帖子数量')
    ap.add_argument('--bduss', default=None, help='登录态 BDUSS cookie（稳定翻满100）')
    ap.add_argument('--out', default=None, help='输出目录')
    ap.add_argument('--timeout', type=float, default=30.0,
                    help='读取超时秒数（网络慢就调大，如 45 / 60）')
    ap.add_argument('--no-ipv4', action='store_true',
                    help='关闭强制IPv4（默认开启，用于解决IPv6导致的读取超时）')
    ap.add_argument('--no-replies', action='store_true',
                    help='不抓取评论/回复（默认抓取）')
    args = ap.parse_args()

    print(f'[配置] 脚本: {os.path.abspath(__file__)}')
    print(f'[配置] 协议: {PROTOCOL} | 超时: 连接 10s / 读取 {args.timeout}s | 强制IPv4: {not args.no_ipv4}')
    repl_yes = chr(26159)
    repl_no = chr(21542)
    print(f'[配置] 目标: {args.kw}吧 前 {args.limit} 帖 | 评论: {repl_yes if not args.no_replies else repl_no}')

    results, status = run_scraper(
        kw=args.kw,
        limit=args.limit,
        out_dir=args.out,
        bduss=args.bduss,
        timeout=args.timeout,
        no_ipv4=args.no_ipv4,
        include_replies=not args.no_replies,
    )

    if status.get('security_blocked'):
        msg = chr(10) + '[!] 触发百度安全验证！'
        print(msg)
        print('    解决方案：')
        print('      1) 使用 --bduss 参数提供登录态（推荐）')
        print('         如何获取BDUSS：浏览器登录贴吧后，F12 -> Application -> Cookies -> 找到BDUSS值')
        print('      2) 使用 Selenium 模式：python tieba_selenium.py')
        print('      3) 等待一段时间后重试（IP可能被临时限制）')
        sys.exit(2)

    if not results:
        msg = chr(10) + '[!] 一个帖子都没收集到 -- ' + status.get('message', '未知错误')
        print(msg)
        print('    排查顺序：')
        print('      1) 先用浏览器打开 ' + PROTOCOL + '://tieba.baidu.com 确认能正常访问；')
        print('      2) 当前使用 ' + PROTOCOL + ' 协议，如需切换请修改代码中的 USE_HTTP 变量；')
        print('      3) 公司/校园网常有代理或防火墙会吞掉响应，换手机热点再试；')
        print('      4) 最稳方案：加 --bduss 走登录态官方接口。')
        sys.exit(1)

    total_imgs = sum(r.get('image_count', 0) for r in results)
    out_dir_final = args.out or os.path.join(os.getcwd(), 'tieba_' + args.kw, 'output')
    done_msg = chr(10) + '==> 完成！成功 ' + str(len(results)) + ' 帖，下载图片 ' + str(total_imgs) + ' 张'
    print(done_msg)
    json_name = chr(112)+chr(111)+chr(115)+chr(116)+chr(115)+chr(46)+chr(106)+chr(115)+chr(111)+chr(110)
    img_name = chr(105)+chr(109)+chr(97)+chr(103)+chr(101)+chr(115)
    print('    JSON: ' + os.path.join(out_dir_final, json_name))
    print('    图片: ' + os.path.join(out_dir_final, img_name))
    tip1 = len(results)
    tip2 = args.limit
    if not args.bduss and tip1 < tip2:
        print(f'    [提示] 未登录时百度对列表翻页有限制，仅抓到 {tip1} 帖。'
              f'加 --bduss 登录态可稳定翻满 {tip2} 帖。')


if __name__ == '__main__':
    main()
