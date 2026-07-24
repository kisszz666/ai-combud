#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
商品价格查询工具（中关村在线版）
================================
功能：在终端输入商品名称，自动识别中关村在线(detail.zol.com.cn)的配件分类
      与型号/规格参数，先按型号筛选，再按「最新时间」排序抓取商品，
      输出京东实时价、中关村参考价、天猫价，并保存到 prices.txt。

技术栈：
- urllib 直接请求（处理 GBK 编码 + gzip 压缩）
- lxml + XPath 解析商品列表
- 筛选：从用户输入提取 ZOL 参数，构建 /category/s1/s2/.../new.html
- 排序：始终使用 ZOL 的「时间」排序（URL 后缀 /new.html）

运行方式：
    python price_search.py              # 直接输入商品名，按时间排序抓中关村在线
    python price_search.py --no-filter  # 关闭套条/单条等关键词过滤

环境准备：
    pip install lxml
"""

import argparse
import gzip
import http.cookiejar
import os
import re
import sys
import time
import urllib.request
from urllib.parse import quote

from lxml import etree


# ---------- 配置 ----------
OUTPUT_FILE = "prices.txt"          # 爬取结果保存文件
MAX_RESULTS = 15                     # 终端显示多少条结果
MAX_PAGES = 3                        # 精确型号匹配不到时最多翻几页
ZOL_BASE = "https://detail.zol.com.cn"  # 中关村在线商品库基地址
# 这些分类支持「带具体型号查询」，精确匹配不到时翻页继续找
PAGINATE_CATEGORIES = ("cpu", "motherboard", "vga", "memory", "solid_state_drive")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Referer": "https://www.zol.com.cn/",
}


# 电脑配件分类：关键词片段 -> ZOL 分类目录
# 注意：r3/r5/r7/r9、i3/i5/i7/i9 用 detect_category 里的词边界正则处理，
#       不放进本表（否则 "ddr5" 会被 "r5" 误判为 CPU）。
CATEGORY_MAP = [
    ("ddr",        "memory"),        # ddr4/ddr5 直接归内存
    ("内存",       "memory"),
    ("rtx",        "vga"),
    ("rx",         "vga"),
    ("显卡",       "vga"),
    ("显示卡",     "vga"),
    ("主板",       "motherboard"),
    ("cpu",        "cpu"),
    ("处理器",     "cpu"),
    ("ryzen",      "cpu"),
    ("锐龙",       "cpu"),
    ("酷睿",       "cpu"),
    ("ultra",      "cpu"),
    ("固态",       "solid_state_drive"),
    ("ssd",        "solid_state_drive"),
    ("固态硬盘",   "solid_state_drive"),
    ("机械硬盘",   "hard_drives"),   # 仅明确写「机械」才搜机械盘
    ("硬盘",       "solid_state_drive"),  # 默认：硬盘 = 固态硬盘（机械盘已淘汰）
    ("b660",       "motherboard"),
    ("b760",       "motherboard"),
    ("b550",       "motherboard"),
    ("b650",       "motherboard"),
    ("z690",       "motherboard"),
    ("z790",       "motherboard"),
    ("h610",       "motherboard"),
    ("h510",       "motherboard"),
    ("x570",       "motherboard"),
    ("x670",       "motherboard"),
    ("电源",       "power"),
    ("机箱",       "case"),
    ("显示器",     "lcd"),
    ("散热",       "cooling_product"),
    ("键盘",       "keyboard"),
    ("鼠标",       "mouse"),
    ("耳机",       "headphone"),
    ("声卡",       "sound_card"),
    ("网卡",       "net_card"),
    ("光驱",       "dvdrw"),
]


# 显卡芯片参数映射（可写多个别名，按用户说法匹配）
# 格式：ZOL 参数 ID -> [别名1, 别名2, ...]
GPU_CHIP_FILTERS = {
    # NVIDIA 40/50 系
    "s11096": ["rtx 5090 d", "5090 d"],
    "s11094": ["rtx 5080", "5080"],
    "s11069": ["rtx 5070 ti", "5070 ti", "5070ti"],
    "s11071": ["rtx 5070", "5070"],
    "s11070": ["rtx 5060 ti", "5060 ti", "5060ti"],
    "s11072": ["rtx 5060", "5060"],
    "s11026": ["rtx 4090 d", "4090 d"],
    "s10074": ["rtx 4090", "4090"],
    "s11033": ["rtx 4080 super", "4080 super"],
    "s10075": ["rtx 4080", "4080"],
    "s10085": ["rtx 4070 ti", "4070 ti", "4070ti"],
    "s11034": ["rtx 4070 super", "4070 super"],
    "s11035": ["rtx 4070ti super", "4070ti super", "4070 ti super"],
    "s10739": ["rtx 4070", "4070"],
    "s10916": ["rtx 4060 ti", "4060 ti", "4060ti"],
    "s10917": ["rtx 4060", "4060"],
    "s9593":  ["rtx 3090 ti", "3090 ti", "3090ti"],
    "s8469":  ["rtx 3090", "3090"],
    "s9043":  ["rtx 3080 ti", "3080 ti", "3080ti"],
    "s8468":  ["rtx 3080", "3080"],
    "s9044":  ["rtx 3070 ti", "3070 ti", "3070ti"],
    "s8467":  ["rtx 3070", "3070"],
    "s8716":  ["rtx 3060 ti", "3060 ti", "3060ti"],
    "s8802":  ["rtx 3060", "3060"],
    "s9299":  ["rtx 3050", "3050"],
    # AMD
    "s11097": ["rx 9070", "9070"],
    "s11098": ["rx 9070 xt", "9070 xt", "9070xt"],
    "s11099": ["rx 9070 gre", "9070 gre", "9070gre"],
    "s10741": ["rx 7900 xtx", "7900 xtx", "7900xtx"],
    "s10742": ["rx 7900 xt", "7900 xt", "7900xt"],
    "s10941": ["rx 7800 xt", "7800 xt", "7800xt"],
    "s10942": ["rx 7700 xt", "7700 xt", "7700xt"],
    "s10943": ["rx 7600", "7600"],
    "s9621":  ["rx 6650 xt", "6650 xt", "6650xt"],
    "s9620":  ["rx 6750 xt", "6750 xt", "6750xt"],
    "s8952":  ["rx 6700 xt", "6700 xt", "6700xt"],
    "s9180":  ["rx 6600", "6600"],
    "s9363":  ["rx 6500 xt", "6500 xt", "6500xt"],
}


# 内存相关参数映射
MEMORY_TYPE_FILTERS = {
    "s11117": ["ddr2"],
    "s11118": ["ddr3"],
    "s11119": ["ddr4"],
    "s11120": ["ddr5"],
}

MEMORY_FREQ_FILTERS = {
    "s8129": ["4000mhz", "4000", "6000mhz", "6000", "5600mhz", "5600", "6400mhz", "6400", "7200mhz", "7200"],
    "s8130": ["3600mhz", "3600"],
    "s8131": ["3400mhz", "3400"],
    "s8132": ["3200mhz", "3200"],
    "s8133": ["3000mhz", "3000"],
    "s5973": ["2800mhz", "2800"],
    "s5972": ["2666mhz", "2666"],
    "s1915": ["2400mhz", "2400"],
    "s1917": ["2133mhz", "2133"],
    "s1919": ["1866mhz", "1866"],
    "s1921": ["1600mhz", "1600"],
    "s1922": ["1333mhz", "1333"],
}

MEMORY_APPLY_FILTERS = {
    "s5974": ["台式机", "desktop"],
    "s5975": ["笔记本", "laptop", "手提"],
    "s6018": ["系统内存", "系统"],
}


# CPU 系列参数映射
CPU_SERIES_FILTERS = {
    # AMD
    "s8259": ["r9", "ryzen 9", "锐龙9"],
    "s7274": ["r7", "ryzen 7", "锐龙7"],
    "s7275": ["r5", "ryzen 5", "锐龙5"],
    "s7328": ["r3", "ryzen 3", "锐龙3"],
    "s7329": ["threadripper", "线程撕裂者"],
    # Intel
    "s11027": ["ultra 9", "酷睿 ultra 9"],
    "s11029": ["ultra 7", "酷睿 ultra 7"],
    "s11028": ["ultra 5", "酷睿 ultra 5"],
    "s7313": ["i9", "酷睿i9", "core i9"],
    "s1584": ["i7", "酷睿i7", "core i7"],
    "s1079": ["i5", "酷睿i5", "core i5"],
    "s1739": ["i3", "酷睿i3", "core i3"],
}


# 主板芯片组参数映射
MOTHERBOARD_CHIPSET_FILTERS = {
    # Intel 600/700 系
    "s9297": ["b660"],
    "s10084": ["b760"],
    "s11110": ["b860"],
    "s9298": ["h610"],
    "s8801": ["h510"],
    "s8800": ["b560", "b560m"],
    "s9189": ["z690"],
    "s10073": ["z790"],
    "s11060": ["z890"],
    # AMD
    "s8446": ["b550"],
    "s11111": ["b850"],
    "s10121": ["b650"],
    "s8465": ["a520"],
    "s10940": ["a620"],
    "s8184": ["x570"],
    "s10072": ["x670"],
    "s11061": ["x870"],
}


# 固态硬盘品牌（ZOL 用 slug 而非 sXXXX 参数）
SSD_BRAND_FILTERS = {
    "wd":       ["西部数据", "西数", "wd"],
    "samsung":  ["三星", "samsung"],
    "kioxia":   ["铠侠", "kioxia"],
    "zhitai":   ["致态", "长江存储"],
    "kingston": ["金士顿", "kingston"],
    "crucial":  ["英睿达", "crucial"],
    "sandisk":  ["闪迪", "sandisk"],
    "galaxy":   ["影驰", "galaxy"],
    "hynixcn":  ["海力士", "sk hynix", "hynix"],
}


# 固态硬盘容量（ZOL 为区间筛选，keyword_match 再做精确容量过滤）
SSD_CAPACITY_FILTERS = {
    "s5982": ["1tb", "1t", "1tbb", "960g"],   # 960GB-1TB
    "s5976": ["2tb", "2t", "4tb", "4t"],       # 2TB以上
    "s5983": ["512g", "512gb", "480g"],        # 480GB-512GB
    "s5984": ["256g", "256gb", "240g"],        # 240GB-256GB
    "s5985": ["128g", "128gb", "120g"],        # 120GB-128GB
}


# 电源额定功率筛选（ZOL 为区间）
POWER_WATTAGE_FILTERS = {
    "s1173": ["300w", "350w"],          # 301W-350W
    "s1174": ["400w"],                   # 351W-400W
    "s1175": ["450w"],                   # 401W-450W
    "s5806": ["500w"],                   # 451W-500W
    "s5807": ["550w", "600w"],           # 501W-600W
    "s2742": ["650w", "700w", "750w", "800w"],  # 601W-800W
    "s8142": ["850w", "900w", "1000w"],  # 801W-1000W
}


# 分类 -> 支持的筛选参数类型
# 每个类型是一组互斥参数，按类别顺序拼接 URL
CATEGORY_FILTER_TYPES = {
    "vga": [GPU_CHIP_FILTERS],                # 显卡只有芯片筛选
    "memory": [MEMORY_TYPE_FILTERS, MEMORY_APPLY_FILTERS, MEMORY_FREQ_FILTERS],
    "cpu": [CPU_SERIES_FILTERS],
    "motherboard": [MOTHERBOARD_CHIPSET_FILTERS],
    "solid_state_drive": [SSD_BRAND_FILTERS, SSD_CAPACITY_FILTERS],
    "power": [POWER_WATTAGE_FILTERS],
}


# 显式分类关键词——这些词明确表达了用户意图，优先级最高
EXPLICIT_CATEGORIES = [
    ("主板", "motherboard"),
    ("显卡", "vga"),
    ("显示卡", "vga"),
    ("CPU", "cpu"),
    ("处理器", "cpu"),
    ("内存", "memory"),
    ("固态硬盘", "solid_state_drive"),
    ("固态", "solid_state_drive"),
    ("SSD", "solid_state_drive"),
    ("ssd", "solid_state_drive"),
    ("机械硬盘", "hard_drives"),
    ("电源", "power"),
    ("机箱", "case"),
    ("显示器", "lcd"),
    ("散热器", "cooling_product"),
    ("散热", "cooling_product"),
    ("键盘", "keyboard"),
    ("鼠标", "mouse"),
    ("耳机", "headphone"),
]


def detect_category(keyword: str) -> str:
    """根据关键词判断 ZOL 分类目录。显式分类词优先级高于组件术语。"""
    kw = keyword.lower()

    # 第一优先级：显式分类词（如"主板"明确就是主板，即使含"ddr4"也不该判为内存）
    for fragment, cat in EXPLICIT_CATEGORIES:
        if fragment in kw:
            return cat

    # 第二优先级：Ryzen / 酷睿 系列用词边界匹配
    for pat, cat in [(r"\br[3579]\b", "cpu"), (r"\bi[3579]\b", "cpu")]:
        if re.search(pat, kw):
            return cat

    # 第三优先级：组件术语兜底
    for fragment, cat in CATEGORY_MAP:
        if fragment in kw:
            return cat

    return "memory"  # 最终兜底：默认内存


def extract_filter_ids(keyword: str, category: str) -> list[str]:
    """
    从用户输入中提取当前分类对应的 ZOL 筛选参数 ID。
    每个筛选类别最多取一个最匹配的参数。
    """
    kw = keyword.lower()
    matched = []
    for filter_group in CATEGORY_FILTER_TYPES.get(category, []):
        best_id = None
        best_len = 0  # 优先匹配更长的别名（更精确）
        for param_id, aliases in filter_group.items():
            for alias in aliases:
                if alias in kw and len(alias) > best_len:
                    best_id = param_id
                    best_len = len(alias)
        if best_id:
            matched.append(best_id)
    return matched


def build_url(category: str, filter_ids: list[str], page: int = 1) -> str:
    """
    构造组合筛选 + 时间排序 URL。
    例：
      /memory/s11119/s8132/new.html  -> DDR4 + 3200MHz + 时间排序
      /vga/s10917/new.html           -> RTX 4060 + 时间排序
      /cpu/s1079/new_2.html          -> i5 系列第 2 页（时间排序）
    """
    base = f"{ZOL_BASE}/{category}"
    if filter_ids:
        base += "/" + "/".join(filter_ids)
    if page <= 1:
        return base + "/new.html"
    return base + f"/new_{page}.html"


def fetch_zol_page(url: str) -> str | None:
    """请求 ZOL 页面，处理 GBK 编码 + gzip 压缩，返回解码后的 HTML。"""
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        resp = opener.open(req, timeout=20)
        raw = resp.read()
    except Exception as e:
        print(f"[!] 请求失败：{e}")
        return None

    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass

    return raw.decode("gbk", errors="ignore")


def verify_time_sort(html: str) -> bool:
    """检查页面当前是否处于「时间」排序。"""
    tree = etree.HTML(html)
    active = tree.xpath('//span[@class="active"]/em/text()')
    return "时间" in "".join(active)


def parse_products(html: str) -> list[dict]:
    """解析商品列表，返回 [{title, zol_price, jd_price, tmall_price, link}]。"""
    tree = etree.HTML(html)
    items = tree.xpath('//div[@class="pic-mode-box"]//li')
    if not items:
        items = tree.xpath('//ul[@class="clearfix"]/li')

    results = []
    for li in items:
        title_nodes = li.xpath('.//h3/a/@title')
        if not title_nodes:
            continue
        title = title_nodes[0].strip()

        # 中关村参考价
        zol_price = None
        price_nodes = li.xpath('.//b[@class="price-type"]/text()')
        if price_nodes:
            try:
                zol_price = float(price_nodes[0])
            except ValueError:
                pass

        # 京东实时价
        jd_price = None
        jd_el = li.xpath('.//a[contains(@onclick, "detail_product_list_jd")]')
        if jd_el:
            jd_text = "".join(jd_el[0].xpath(".//text()")).strip()
            m = re.search(r"[¥￥]\s*(\d{1,6}(?:\.\d{1,2})?)\s*(?:万)?", jd_text)
            if m:
                val = float(m.group(1))
                if "万" in jd_text:
                    val *= 10000
                jd_price = val

        # 天猫实时价
        tmall_price = None
        tmall_el = li.xpath('.//a[contains(@onclick, "detail_product_list_tmall")]')
        if tmall_el:
            tmall_text = "".join(tmall_el[0].xpath(".//text()")).strip()
            m = re.search(r"[¥￥]\s*(\d{1,6}(?:\.\d{1,2})?)\s*(?:万)?", tmall_text)
            if m:
                val = float(m.group(1))
                if "万" in tmall_text:
                    val *= 10000
                tmall_price = val

        link_nodes = li.xpath('.//h3/a/@href')
        link = link_nodes[0].strip() if link_nodes else ""
        if link and not link.startswith("http"):
            link = ZOL_BASE + link

        results.append({
            "title": title,
            "zol_price": zol_price,
            "jd_price": jd_price,
            "tmall_price": tmall_price,
            "link": link,
        })
    return results


def to_simplified(text: str) -> str:
    """繁体转简体（常用品牌名）"""
    trad_to_simp = {
        '金士頓': '金士顿', '頓': '顿',
        '威刚': '威刚',
        '芝奇': '芝奇',
        '海盗船': '海盗船',
        '影驰': '影驰',
        '技嘉': '技嘉',
        '微星': '微星',
        '華碩': '华硕', '華': '华',
        '美光': '美光',
        '英睿达': '英睿达',
        '海力士': '海力士',
        '三星': '三星',
        '東芝': '东芝', '東': '东',
        '日立': '日立',
    }
    for trad, simp in trad_to_simp.items():
        text = text.replace(trad, simp)
    return text


def keyword_match(keyword: str, title: str, category: str = "", relaxed: bool = False) -> bool:
    """
    抓取后二次过滤：容量/套条/CPU型号/主板芯片组/SSD品牌等 ZOL 筛选页无法直接过滤的维度。
    
    Args:
        keyword: 查询关键词
        title: 产品标题
        category: 产品分类
        relaxed: 是否使用宽松匹配模式（用于fallback场景），会跳过品牌检查
    """
    keyword = to_simplified(keyword)
    title = to_simplified(title)
    # 全角括号标准化为 ASCII 括号（AI 输出常含全角符号）
    keyword = keyword.replace("（", "(").replace("）", ")")
    kw = keyword.lower()
    title_l = title.lower()

    # ---- 固态硬盘：品牌 + 精确容量 + 接口类型 ----
    if category == "solid_state_drive":
        # 接口类型：默认偏好 M.2 NVMe（现代装机主流）
        #   - 明确要求 m.2/nvme/pci-e → 标题必须含对应词
        #   - 明确要求 sata/2.5 → 允许 SATA
        #   - 都没说 → 排除纯 SATA 盘（标题含 "SATA" 但不含 "M.2" 的）
        has_m2 = bool(re.search(r"m\.?2|nvme|pci.?e", kw))
        has_sata = bool(re.search(r"sata|2\.5寸|2\.5英寸", kw))
        if has_m2:
            if not re.search(r"m\.?2|nvme|pci.?e", title_l):
                return False
        elif not has_sata:
            # 默认模式：排除明确是 SATA 但不是 M.2 的盘
            if (re.search(r"sata", title_l)
                    and not re.search(r"m\.?2|nvme|pci.?e", title_l)):
                return False

        # 品牌（如 西部数据 → 标题含"西部数据"/"wd"）
        ssd_brand = re.search(r"西部数据|西数|wd|三星|samsung|铠侠|kioxia|致态|长江存储|金士顿|kingston|英睿达|crucial|闪迪|sandisk|影驰|galaxy|海力士|hynix", kw)
        if ssd_brand:
            brand_token = ssd_brand.group(0)
            brand_variants = {
                "西部数据": ["西部数据", "wd"], "西数": ["西部数据", "wd"], "wd": ["西部数据", "wd"],
                "三星": ["三星"], "samsung": ["三星"],
                "铠侠": ["铠侠"], "kioxia": ["铠侠"],
                "致态": ["致态"], "长江存储": ["致态", "长江存储"],
                "金士顿": ["金士顿"], "kingston": ["金士顿"],
                "英睿达": ["英睿达"], "crucial": ["英睿达"],
                "闪迪": ["闪迪"], "sandisk": ["闪迪"],
                "影驰": ["影驰"], "galaxy": ["影驰"],
                "海力士": ["海力士"], "hynix": ["海力士"], "sk hynix": ["海力士"],
            }.get(brand_token, [brand_token])
            if not any(v in title_l for v in brand_variants):
                return False

    # ---- 电源：额定功率（如 750w → 标题含 750w / 750瓦）----
    #     注意：此过滤在 fallback 模式下也必须生效（见 search_price 末尾 fallback logic）
    #     用 (?![a-zA-Z0-9]) 而非 \b 或 (?!\w)，因为中文字符是 Unicode word char，
    #     在 "550w电源" 中 w 后面紧接中文，\b 和 (?!\w) 都不会触发
    if category == "power":
        watt_m = re.search(r"(\d+)\s*w(?![a-zA-Z0-9])", kw)
        if watt_m:
            watt = int(watt_m.group(1))
            if not re.search(rf"{watt}\s*(?:w|瓦)", title_l):
                return False

    # ---- 散热器：排除机箱风扇/硅脂，只留真正的 CPU 散热器 ----
    if category == "cooling_product":
        # 直接命中风扇/硅质/导热类产品则排除
        #   "反叶"=机箱风扇特有（反向叶片），"棱镜/ARGB灯效"常见于装饰风扇
        if re.search(r"风扇|硅脂|导热|散热垫|机箱扇|静音扇|机箱风扇|反叶|rgb灯|argb灯|led灯", title_l):
            return False
        # 必须是真正的 CPU 散热器（含塔式/热管/下吹/风冷+cpu/水冷/一体等标识）
        # 单纯含"风冷"/"散热"但不带 cpu/塔/热管/水冷 的，通常是机箱风扇或伪散热配件
        if not re.search(r"(?:散热器|塔式|塔|热管|下吹|水冷|一体|cpu|下压).*?(?:散热|风冷|冷却)|(?:cpu\s*散热|cpu.*?散热器|塔式.*?散热|热管.*?散热)", title_l):
            return False

    # ---- 内存：排除笔记本条 + 校验频率 + 品牌检查 + 排除金士顿 ----
    if category == "memory":
        # 排除金士顿品牌（因为该品牌价格数据不准确）
        excluded_brands = [r"金士顿", r"kingston", r"金士頓"]
        for excluded_brand in excluded_brands:
            if re.search(excluded_brand, title_l, re.IGNORECASE):
                return False
        
        # 未明确说笔记本时，排除笔记本/SO-DIMM 条
        if not re.search(r"笔记本|laptop|手提|so-?dimm", kw):
            if re.search(r"笔记本|laptop|so-?dimm", title_l):
                return False
        # 明确要求某频率（如 6000mhz）时，标题必须含该数字
        freq_m = re.search(r"(\d{3,4})\s*mhz", kw)
        if freq_m and freq_m.group(1) not in title_l:
            return False
        
        # 品牌检查（如 威刚、海盗船等）- 仅在非relaxed模式下检查
        if not relaxed:
            memory_brand_patterns = [
                (r"威刚|adata|xpg", ["威刚", "xpg"]),
                (r"芝奇|g.skill|gskill", ["芝奇"]),
                (r"海盗船|corsair", ["海盗船"]),
                (r"影驰|galax", ["影驰"]),
                (r"技嘉|gigabyte", ["技嘉"]),
                (r"微星|msi", ["微星"]),
                (r"华硕|asus", ["华硕"]),
                (r"美光|micron|crucial|英睿达", ["美光", "英睿达"]),
                (r"海力士|hynix|sk\s*hynix", ["海力士"]),
                (r"三星|samsung", ["三星"]),
                (r"玖和|jiuhe", ["玖和"]),
                (r"长城|great\s*wall", ["长城"]),
                (r"科赋|klevv", ["科赋"]),
                (r"光威|gloway", ["光威"]),
                (r"雷克沙|lexar", ["雷克沙"]),
            ]
            for brand_pattern, brand_keywords in memory_brand_patterns:
                if re.search(brand_pattern, kw, re.IGNORECASE):
                    if not any(kw in title_l for kw in brand_keywords):
                        return False
                    break

    # ---- 主板：匹配芯片组 + 品牌型号 ----
    mb_chipset = re.search(r"\b([bhxz]\d{3,4})\w*", kw)
    if mb_chipset:
        chip = mb_chipset.group(1)
        if chip not in title_l:
            return False
        # 进一步校验品牌（如果查询中指定了品牌）
        mb_brands = {
            "微星": ["微星", "msi"], "华硕": ["华硕", "asus"],
            "技嘉": ["技嘉", "gigabyte"], "华擎": ["华擎", "asrock"],
            "七彩虹": ["七彩虹", "colorful"], "铭瑄": ["铭瑄", "maxsun"],
        }
        for brand_pat, brand_words in mb_brands.items():
            if brand_pat in kw:
                if not any(w in title_l for w in brand_words):
                    return False
                break
        # 进一步校验具体型号（如 MORTAR → mortar）
        model_fragments = re.findall(r'\b(mortar|tuf|rog|strix|aorus|elite|pro|master|ace|tomahawk|gaming|plus|edge|carbon|steel\s*legend|pg\s*lightning)\b', kw)
        for frag in model_fragments:
            frag_clean = frag.replace(" ", "").replace("-", "")
            title_clean = title_l.replace(" ", "").replace("-", "")
            if frag_clean not in title_clean:
                return False
        return True

    # ---- CPU：匹配型号数字 ----
    # 检查是否有 CPU 相关关键词（需要更精确的匹配）
    has_cpu_keyword = bool(re.search(r'(?:cpu|processor|处理|amd|intel|ryzen|core|酷睿|锐龙|\br[3579][\s-]|\bi[3579][\s-]|\bultra\s)', kw))

    if has_cpu_keyword:
        # 从输入中提取 CPU 型号数字（如 r5 5600 → 5600，i5-12400 → 12400）
        # 注意：i5-13600KF 中间是连字符不是空格
        cpu_model = re.search(r"(?:r[3579][\s-]+|ryzen\s+[3579][\s-]+|i[3579][\s-]+|ultra\s+[3579][\s-]+)(\d{3,5}[a-z0-9]*)", kw)
        if cpu_model:
            model_num = cpu_model.group(1)
            # 排除频率相关的（如 6000mhz, 3000mhz 等）
            freq_match = re.search(r'(\d{3,4})\s*mhz', kw)
            if freq_match:
                freq_num = freq_match.group(1)
                if model_num.startswith(freq_num) or freq_num in model_num:
                    pass  # 这是频率，不是 CPU 型号，跳过
                else:
                    # 精确型号匹配：型号数字必须在标题中出现
                    if model_num not in title_l:
                        return False
                    return True
            else:
                if model_num not in title_l:
                    return False
                return True
        # 有 CPU 关键词但没提取到型号 → 进一步检查：标题也必须有 CPU 相关词
        if not re.search(r'(?:intel|amd|ryzen|core|酷睿|锐龙|线程撕裂者)', title_l):
            return False

    # ---- 内存 / 固态：容量 ----
    # 检测是否有 "总容量GB(单条容量x数量)" 格式，如 "32GB(16x2)" 或 "32GB(16Gx2)" 或 "32GB(16G×2)"
    kit_format = re.search(r"(\d+)\s*g[b]?\s*\(\s*(\d+)\s*g[b]?\s*[×*x]\s*2\s*\)", kw, re.IGNORECASE)
    
    # 检测是否有 "总容量GB 单条容量x2" 格式，如 "32GB 16x2" 或 "32GB 16GBx2"
    kit_format2 = re.search(r"(\d+)\s*g[b]?\s+(\d+)\s*(?:g[b]?)?\s*[×*x]\s*2", kw, re.IGNORECASE)
    
    # 是否要求套条（仅内存相关）
    is_kit = bool(re.search(r"[x×*]\s*2|双通道|套条|kit", kw))

    # 容量：支持 TB 与 GB（如 1tb、512g）
    cap_tb = re.search(r"(\d+)\s*tb?", kw)
    cap_gb = re.search(r"(\d+)\s*g(?:b)?", kw)
    cap_val = None
    cap_unit = None
    single_cap_val = None  # 单条容量（如 16x2 中的 16）
    
    if kit_format:
        cap_val = int(kit_format.group(1))  # 总容量，如 32
        single_cap_val = int(kit_format.group(2))  # 单条容量，如 16
        cap_unit = "g"
    elif kit_format2:
        cap_val = int(kit_format2.group(1))  # 总容量，如 32
        single_cap_val = int(kit_format2.group(2))  # 单条容量，如 16
        cap_unit = "g"
    elif cap_tb:
        cap_val = int(cap_tb.group(1))
        cap_unit = "t"
    elif cap_gb:
        cap_val = int(cap_gb.group(1))
        cap_unit = "g"

    if is_kit and cap_val and cap_unit == "g":
        if kit_format or kit_format2:
            # 格式为 "总容量GB(单条容量x2)" 或 "总容量GB 单条容量x2"，如 "32GB(16x2)" 或 "32GB 16x2"
            # 匹配：总容量GB、单条容量GBx2、2x单条容量GB 任一即可
            kit_patterns = [
                rf"{cap_val}\s*gb",                                    # 32GB（总容量）
                rf"{single_cap_val}\s*gb?\s*[×*x]\s*2",               # 16GBx2 或 16x2
                rf"2\s*[×*x]\s*{single_cap_val}\s*gb?",               # 2x16GB 或 2x16
            ]
        else:
            # 格式为 "单条容量GB x2"，如 "16GB x2"
            total_gb = cap_val * 2  # 计算总容量
            kit_patterns = [
                rf"{cap_val}\s*gb\s*[×*x]\s*2",                      # 16GB×2
                rf"2\s*[×*x]\s*{cap_val}\s*gb",                       # 2×16GB
                rf"{total_gb}\s*gb",                                  # 32GB（总容量）
                rf"{cap_val}\s*gb[\s\S]*?套条",
            ]
        
        if not any(re.search(p, title_l, re.IGNORECASE) for p in kit_patterns):
            return False
    elif cap_val:
        # 精确容量匹配：标题中应有「Nt」或「Ng」（如 1TB / 512G）
        cap_pat = f"{cap_val}{cap_unit}"
        # 兼容标题里的写法（1TB / 1T / 512G / 512GB）
        pat_alt = rf"{cap_val}\s*{cap_unit}(?:b)?\b"
        if cap_pat not in title_l and not re.search(pat_alt, title_l):
            return False

    return True


def save_results(keyword: str, url: str, results: list[dict]) -> None:
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("商品价格查询结果（中关村在线 · 按最新时间排序）\n")
        f.write("=" * 64 + "\n")
        f.write(f"查询商品：{keyword}\n")
        f.write(f"查询时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"筛选/排序 URL：{url}\n")
        f.write("价格说明：JD=京东实时价 | ZOL=参考价 | TM=天猫价\n")
        f.write("=" * 64 + "\n\n")

        if results:
            f.write(f"共找到 {len(results)} 条匹配商品（按最新上架时间排列）：\n\n")
            for i, r in enumerate(results, 1):
                jd = f"¥{r['jd_price']:.0f}" if r["jd_price"] is not None else "无"
                zol = f"¥{r['zol_price']:.0f}" if r["zol_price"] is not None else "无"
                tm = f"¥{r['tmall_price']:.0f}" if r["tmall_price"] is not None else "无"
                f.write(f"{i}. {r['title']}\n")
                f.write(f"   京东价：{jd} ｜ 中关村参考价：{zol} ｜ 天猫价：{tm}\n")
                f.write(f"   链接：{r['link']}\n\n")
        else:
            f.write("未找到匹配的商品。\n")


def print_results(keyword: str, url: str, results: list[dict]) -> None:
    if not results:
        print("\n[!] 未解析到匹配商品，可能筛选参数不对或页面结构变化。")
        return

    jd_prices = [(i, r) for i, r in enumerate(results) if r["jd_price"] is not None]
    zol_prices = [(i, r) for i, r in enumerate(results) if r["zol_price"] is not None]

    print("\n" + "=" * 64)
    print(f"  查询：{keyword}")
    print(f"  来源：中关村在线（按最新时间排序）")
    print("=" * 64)

    if jd_prices:
        best = min(jd_prices, key=lambda x: x[1]["jd_price"])
        r = best[1]
        print(f"\n  ★ 最低京东价：¥{r['jd_price']:.0f}")
        print(f"  └─ {r['title']}")
        if r["zol_price"] is not None:
            print(f"  └─ 中关村参考价：¥{r['zol_price']:.0f}")
        if r["tmall_price"] is not None:
            print(f"  └─ 天猫价：¥{r['tmall_price']:.0f}")
        print(f"  └─ 链接：{r['link']}")
        print(f"\n  （共 {len(results)} 条商品参与比价，京东有报价 {len(jd_prices)} 条，详见 prices.txt）")
    elif zol_prices:
        best = min(zol_prices, key=lambda x: x[1]["zol_price"])
        r = best[1]
        print(f"\n  ★ 最低参考价：¥{r['zol_price']:.0f}（无京东报价）")
        print(f"  └─ {r['title']}")
        print(f"  └─ 链接：{r['link']}")
        print(f"\n  （共 {len(results)} 条商品，均无京东报价，详见 prices.txt）")
    else:
        print("\n  [!] 所有商品均无明确报价，详见 prices.txt 中的完整列表。")


def search_price(query: str, category: str = None) -> dict:
    """
    程序化调用接口：输入商品查询词，返回最低价结果。

    Args:
        query: 商品查询词
        category: 可选，显式指定分类（如"主板"/"CPU"等），跳过自动检测

    返回：
        {
            "query": str,           # 原始查询
            "category": str,        # 识别的分类
            "url": str,             # 抓取地址
            "title": str | None,    # 最低价商品标题（京东优先）
            "jd_price": float | None,
            "zol_price": float | None,
            "tmall_price": float | None,
            "link": str,            # 商品链接
            "total_found": int,     # 匹配商品总数
            "error": str | None,    # 错误信息
        }
    """
    query = query.strip()
    if not query:
        return {"query": query, "error": "查询不能为空", "title": None}

    if category:
        # 中文分类名转换为 ZOL 目录名
        CAT_CN_TO_ZOL = {
            "CPU": "cpu", "显卡": "vga", "内存": "memory",
            "固态硬盘": "solid_state_drive", "主板": "motherboard",
            "电源": "power", "机箱": "case", "散热器": "cooling_product",
        }
        category = CAT_CN_TO_ZOL.get(category, category)
    else:
        category = detect_category(query)
    filter_ids = extract_filter_ids(query, category)
    url = build_url(category, filter_ids)

    html = fetch_zol_page(url)
    if not html:
        return {"query": query, "category": category, "url": url,
                "error": f"无法抓取ZOL页面: {url}", "title": None}

    products = parse_products(html)
    matched = [p for p in products if keyword_match(query, p["title"], category)]

    # 翻页查找精确匹配
    if not matched and category in PAGINATE_CATEGORIES:
        for pg in range(2, MAX_PAGES + 1):
            next_url = build_url(category, filter_ids, page=pg)
            html2 = fetch_zol_page(next_url)
            if not html2:
                break
            prods2 = parse_products(html2)
            if not prods2:
                break
            products.extend(prods2)
            m2 = [p for p in prods2 if keyword_match(query, p["title"], category)]
            if m2:
                matched = m2
                break

    # 无精确匹配时回退到全部结果
    fallback = False
    if not matched:
        fallback = True
        # 关键过滤类别在 fallback 模式下仍需保留核心约束
        _critical_categories = ("power", "cooling_product")
        if category in _critical_categories:
            matched = [p for p in products if keyword_match(query, p["title"], category)]
            if not matched and category == "power":
                # 电源特殊处理：精确瓦数匹配不到时，按「标题中瓦数与目标瓦数的距离」
                # 选最接近的（避免 550W 查询返回 750W 杂牌）
                kw_w = re.search(r"(\d+)\s*w\b", query.lower())
                if kw_w:
                    target_w = int(kw_w.group(1))
                    scored = []
                    for p in products:
                        tl = p["title"].lower()
                        # 提取标题中的第一个瓦数值
                        wm = re.search(r"(\d{2,4})\s*(?:w|瓦)", tl)
                        if wm:
                            title_w = int(wm.group(1))
                            dist = abs(title_w - target_w)
                            # 超过 ±150W 的直接不要
                            if dist <= 150:
                                scored.append((dist, p))
                    if scored:
                        scored.sort(key=lambda x: x[0])
                        matched = [s[1] for s in scored]
                    else:
                        matched = products  # 最终兜底
                else:
                    matched = products
            elif not matched:
                matched = products  # 散热器等：二次兜底
        elif category == "memory":
            # 内存特殊处理：使用relaxed模式（跳过品牌检查），保留频率、容量等核心过滤
            matched = [p for p in products if keyword_match(query, p["title"], category, relaxed=True)]
            if not matched:
                # 兜底：排除金士顿后取全部（金士顿价格数据不准）
                matched = [p for p in products
                           if not re.search(r"金士顿|kingston|金士頓", p["title"].lower())]
            if not matched:
                matched = products  # 最后兜底
        else:
            matched = products

    # 取京东最低价（优先），其次 ZOL 最低价
    best = None
    jd_items = [r for r in matched if r.get("jd_price") is not None]
    zol_items = [r for r in matched if r.get("zol_price") is not None]

    if jd_items:
        best = min(jd_items, key=lambda x: x["jd_price"])
    elif zol_items:
        best = min(zol_items, key=lambda x: x["zol_price"])

    if best is None and matched:
        best = matched[0]  # 兜底：取第一个

    result = {
        "query": query,
        "category": category,
        "url": url,
        "title": best["title"] if best else None,
        "jd_price": best.get("jd_price") if best else None,
        "zol_price": best.get("zol_price") if best else None,
        "tmall_price": best.get("tmall_price") if best else None,
        "link": best.get("link", "") if best else "",
        "total_found": len(matched),
        "fallback": fallback,
        "error": None,
    }

    # 最终有效价格（优选策略：JD和ZOL取更合理的）
    jd = result["jd_price"]
    zol = result["zol_price"]
    if jd and zol and zol > 0:
        ratio = jd / zol
        if ratio > 2.0:
            # JD 价格是 ZOL 参考价 2 倍以上 → 明显虚高，用 ZOL 价
            result["price"] = zol
        elif ratio < 0.5:
            # JD 价格不到 ZOL 参考价一半 → ZOL 可能过时，用 JD 价
            result["price"] = jd
        else:
            # 两者接近 → 优先 JD（实时成交价）
            result["price"] = jd
    else:
        result["price"] = jd or zol
    return result


# ---------- CLI 入口 ----------
def main():
    parser = argparse.ArgumentParser(description="中关村在线商品价格查询（按最新时间排序）")
    parser.add_argument("--no-filter", "-n", action="store_true",
                        help="关闭关键词过滤（如套条/单条），显示全部筛选结果")
    args = parser.parse_args()

    print("=" * 64)
    print("   中关村在线 · 商品价格查询（按最新时间排序）")
    print("=" * 64)

    keyword = input("\n请输入商品名称：").strip()
    if not keyword:
        print("[!] 商品名称不能为空，请重新运行。")
        sys.exit(0)

    category = detect_category(keyword)
    filter_ids = extract_filter_ids(keyword, category)
    url = build_url(category, filter_ids)

    print(f"\n[>] 识别分类：{category}")
    print(f"[>] 筛选参数：{filter_ids}")
    print(f"[>] 抓取地址：{url}")

    html = fetch_zol_page(url)
    if not html:
        sys.exit(1)

    sorted_ok = verify_time_sort(html)
    print(f"[>] 排序状态：{'✓ 已按「时间」排序' if sorted_ok else '⚠ 未检测到时间排序标记'}")

    products = parse_products(html)
    print(f"[>] 本页共抓取商品：{len(products)} 条")

    if args.no_filter:
        matched = products
        fallback = False
    else:
        matched = [p for p in products if keyword_match(keyword, p["title"], category)]
        fallback = False

        # 精确过滤无结果时，翻页继续找（主要针对带具体型号的 CPU/主板/显卡等）
        if not matched and category in PAGINATE_CATEGORIES:
            for pg in range(2, MAX_PAGES + 1):
                next_url = build_url(category, filter_ids, page=pg)
                html2 = fetch_zol_page(next_url)
                if not html2:
                    break
                prods2 = parse_products(html2)
                if not prods2:
                    break
                products.extend(prods2)
                m2 = [p for p in prods2 if keyword_match(keyword, p["title"], category)]
                if m2:
                    print(f"[>] 在第 {pg} 页找到精确匹配型号")
                    matched = m2
                    break

        # 仍无精确匹配 → 回退同系列，但明确标注「非精确型号」
        if not matched:
            fallback = True
            matched = products

    if fallback:
        print(f"[⚠] 未找到精确型号「{keyword}」的匹配商品，下面显示的是同系列全部商品的最低价（非该精确型号），仅供参考。")

    print_results(keyword, url, matched)
    save_results(keyword, url, matched)
    print(f"\n[✓] 结果已保存到：{os.path.abspath(OUTPUT_FILE)}")

    try:
        input("\n按 Enter 键退出...")
    except EOFError:
        pass


if __name__ == "__main__":
    main()
