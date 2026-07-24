"""
社区数据服务 —— 从本地 JSON 读取帖子，后台爬取由 tieba_scraper2.run_scraper() 驱动。
支持两种爬取模式：
  1. requests 模式（默认）- 可能被百度安全验证阻止
  2. selenium 模式 - 使用真实浏览器，可绕过安全验证
"""
import os
import sys
import json
import time
import threading
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tieba_scraper2 import run_scraper

# 尝试导入 selenium 模块
try:
    from tieba_selenium import run_selenium_scraper
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False
    print("[INFO] Selenium 模块不可用")

# ------------------------- 路径 -------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(PROJECT_ROOT, "tieba_电脑", "output", "posts.json")
DATA_DIR = os.path.dirname(DATA_FILE)

# ------------------------- 爬取状态 -------------------------
_state = {
    "running": False,
    "progress": "",
    "started_at": None,
    "finished_at": None,
    "result": None,
    "message": "",
    "post_count": 0,
    "mode": "requests",  # requests 或 selenium
    "security_blocked": False,
    "lock": threading.Lock(),
}

def get_scrape_status():
    with _state["lock"]:
        return {k: v for k, v in _state.items() if k != "lock"}

def reset_scrape_state():
    """强制重置爬取状态（用于解除卡死）。"""
    with _state["lock"]:
        _state["running"] = False
        _state["progress"] = ""
        _state["result"] = "cancelled"
        _state["message"] = "上一次爬取已被手动取消"
        _state["security_blocked"] = False
        _state["finished_at"] = datetime.now().strftime("%H:%M:%S")

def _set_state(**kw):
    with _state["lock"]:
        for k, v in kw.items():
            _state[k] = v

# ------------------------- 数据读取 -------------------------

def load_posts_from_file():
    if not os.path.exists(DATA_FILE):
        return None
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            posts = json.load(f)
        if isinstance(posts, list) and len(posts) > 0:
            for p in posts:
                if "replies" not in p:
                    p["replies"] = []
            return posts
    except Exception as e:
        print(f"[社区] 读取 posts.json 失败: {e}")
    return None

def get_posts():
    posts = load_posts_from_file()
    if posts:
        return posts, "scraped"
    return [], "empty"

# ------------------------- 后台爬取 -------------------------

def run_scrape_background(kw="电脑", limit=20, timeout=45.0, mode="requests", bduss=None):
    """
    后台线程运行爬虫。

    Args:
        kw: 贴吧名称
        limit: 目标帖子数
        timeout: 请求超时
        mode: 爬取模式 - "requests"（默认）或 "selenium"
        bduss: 登录态 BDUSS cookie（提供则走官方接口，稳定翻满 limit）

    Returns:
        bool: 是否成功启动
    """
    if _state["running"]:
        return False

    _set_state(
        running=True,
        progress="正在初始化...",
        started_at=datetime.now().strftime("%H:%M:%S"),
        finished_at=None,
        result=None,
        message="",
        post_count=0,
        mode=mode,
        security_blocked=False,
    )

    out_dir = os.path.join(PROJECT_ROOT, f"tieba_{kw}", "output")

    def _run():
        try:
            if mode == "selenium":
                if not SELENIUM_AVAILABLE:
                    _set_state(
                        running=False,
                        result="error",
                        message="Selenium 模式不可用，请先安装 selenium: pip install selenium",
                        finished_at=datetime.now().strftime("%H:%M:%S"),
                        progress="",
                    )
                    return
                
                _set_state(progress="启动浏览器...")
                results, scrape_status = run_selenium_scraper(
                    kw=kw,
                    limit=limit,
                    out_dir=out_dir,
                    manual_mode=False,
                    headless=False,
                    progress_callback=lambda msg: _set_state(progress=msg),
                )
            else:  # requests 模式
                results, scrape_status = run_scraper(
                    kw=kw,
                    limit=limit,
                    out_dir=out_dir,
                    bduss=bduss,
                    timeout=timeout,
                    include_replies=True,
                    progress_callback=lambda msg: _set_state(progress=msg),
                )
            
            # 处理结果
            if scrape_status.get('security_blocked'):
                _set_state(
                    running=False,
                    result="security_blocked",
                    message="触发百度安全验证！建议使用 Selenium 模式或提供 BDUSS",
                    security_blocked=True,
                    finished_at=datetime.now().strftime("%H:%M:%S"),
                    progress="",
                )
            elif scrape_status.get('ok') and results:
                total_imgs = sum(r.get("image_count", 0) for r in results)
                total_replies = sum(len(r.get("replies", [])) for r in results)
                _set_state(
                    running=False,
                    result="ok",
                    message=f"成功抓取 {len(results)} 帖 / {total_replies} 回复，下载 {total_imgs} 张图片",
                    post_count=len(results),
                    finished_at=datetime.now().strftime("%H:%M:%S"),
                    progress="",
                )
            else:
                _set_state(
                    running=False,
                    result="empty",
                    message=scrape_status.get('message', '未能获取任何帖子'),
                    finished_at=datetime.now().strftime("%H:%M:%S"),
                    progress="",
                )
                
        except Exception as e:
            _set_state(
                running=False,
                result="error",
                message=f"爬取出错: {str(e)[:150]}",
                finished_at=datetime.now().strftime("%H:%M:%S"),
                progress="",
            )

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return True


# ============ 命令行入口 ============
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="社区数据爬取")
    ap.add_argument("--kw", default="电脑")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--timeout", type=float, default=45.0)
    ap.add_argument("--mode", default="requests", choices=["requests", "selenium"],
                   help="爬取模式 (requests 或 selenium)")
    args = ap.parse_args()

    print(f"开始爬取「{args.kw}吧」前 {args.limit} 帖 (模式: {args.mode})...")
    run_scrape_background(kw=args.kw, limit=args.limit, timeout=args.timeout, mode=args.mode)

    while _state["running"]:
        print(f"\r  {_state['progress']}", end="", flush=True)
        time.sleep(0.5)
    print()
    if _state["result"] == "ok":
        print(f"OK {_state['message']}")
    elif _state["result"] == "security_blocked":
        print(f"SECURITY BLOCKED {_state['message']}")
    else:
        print(f"FAIL {_state['message']}")
