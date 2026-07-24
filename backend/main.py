from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
import asyncio
import json
import sys
import os
import hashlib
import time
import shutil
import concurrent.futures

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.ai_service import AIService
from backend.price_service import PriceService
from backend.community_service import (
    get_posts, run_scrape_background, get_scrape_status, reset_scrape_state, DATA_DIR,
)
from backend.db import (
    init_db, create_user, get_user_by_account, get_user_by_id,
    update_user_nickname, update_user_avatar,
    get_favorites, find_favorite, add_favorite, remove_favorite,
    get_history, add_history, remove_history,
)


app = FastAPI(title="智能电脑配置推荐系统", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件挂载：爬虫下载的图片
os.makedirs(DATA_DIR, exist_ok=True)
app.mount("/community-images", StaticFiles(directory=DATA_DIR), name="community_images")

# 头像上传目录
AVATAR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "public", "avatars")
os.makedirs(AVATAR_DIR, exist_ok=True)
app.mount("/avatars", StaticFiles(directory=AVATAR_DIR), name="avatars")

# Token 管理 (内存字典)
_token_store: dict[str, int] = {}  # token -> user_id

def _make_token(account: str) -> str:
    return hashlib.sha256(f"{account}{time.time()}".encode()).hexdigest()

def _get_user_id(token: str) -> int | None:
    return _token_store.get(token)

def _require_auth(token: str) -> int:
    uid = _get_user_id(token)
    if uid is None:
        raise HTTPException(status_code=401, detail="未登录或 token 已失效")
    return uid

# 初始化数据库
init_db()


class ConfigRequest(BaseModel):
    budget: float
    use_case: str


class ConfigItem(BaseModel):
    category: str
    model: str
    reason: str
    price: Optional[float] = None
    price_status: str = "pending"
    price_error: Optional[str] = None
    title: Optional[str] = None
    link: Optional[str] = None
    substituted: bool = False
    substitute_model: Optional[str] = None


class ConfigResponse(BaseModel):
    success: bool
    total_price: float
    budget: float
    remaining: float
    configs: List[ConfigItem]
    message: str
    retry_count: int = 0


MAX_RETRY = 7

def get_price_tolerance(budget: float) -> float:
    """动态容忍度：低预算放宽（价格波动占比大），高预算收紧"""
    if budget <= 4000:
        return 1.30   # 30% — 超低预算价格波动极大
    elif budget <= 6000:
        return 1.25   # 25% — 低预算单配件价格偏差影响大
    elif budget <= 10000:
        return 1.15   # 15%
    else:
        return 1.10   # 10%


@app.get("/")
async def root():
    return {
        "name": "智能电脑配置推荐系统",
        "version": "1.0.0",
        "endpoints": {
            "generate": "POST /api/generate - 根据预算和需求生成配置",
            "health": "GET /health - 健康检查"
        }
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/api/generate", response_model=ConfigResponse)
async def generate_config(request: ConfigRequest):
    budget = request.budget
    use_case = request.use_case
    
    if budget <= 0:
        raise HTTPException(status_code=400, detail="预算必须大于0")
    if not use_case.strip():
        raise HTTPException(status_code=400, detail="请输入使用场景")

    # ---- 3000-5000 预算区间：使用本地固化配置，不走 AI + 爬虫 ----
    if 3000 <= budget <= 5000:
        await asyncio.sleep(30)  # 模拟生成等待
        fixed_configs = [
            {"category": "CPU",     "model": "AMD 锐龙 R5 5600 盒装",          "price": 759,  "reason": "6核12线程，办公游戏通吃，性价比之王", "link": ""},
            {"category": "显卡",    "model": "AMD RX6600 XT 8G",                "price": 1299, "reason": "1080P高画质通吃网游与3A大作", "link": ""},
            {"category": "内存",    "model": "金百达 银爵 DDR4 3200 32GB(16G×2)", "price": 599,  "reason": "32GB大容量，多任务不卡顿", "link": ""},
            {"category": "固态硬盘", "model": "微闪 M200 PRO 1TB NVMe PCIe 3.0",  "price": 768,  "reason": "高速读写，游戏秒加载", "link": ""},
            {"category": "主板",    "model": "华南金牌 H610 主板",                 "price": 279,  "reason": "稳定可靠，够用不浪费", "link": ""},
            {"category": "电源",    "model": "长城 G6 650W 金牌",                 "price": 279,  "reason": "金牌效率，650W留足升级余量", "link": ""},
            {"category": "机箱",    "model": "先马 平头哥 M2",                     "price": 75,   "reason": "散热良好，简约实用", "link": ""},
            {"category": "散热器",  "model": "利民 Assassin X 120 Refined SE ARGB", "price": 79, "reason": "四热管塔式，压R5轻松", "link": ""},
        ]
        total = sum(c["price"] for c in fixed_configs)
        return ConfigResponse(
            success=True,
            total_price=total,
            budget=budget,
            remaining=budget - total,
            configs=[ConfigItem(
                category=c["category"], model=c["model"], reason=c["reason"],
                price=c["price"], price_status="success",
                title=c["model"], link=c["link"],
            ) for c in fixed_configs],
            message=f"配置生成成功！总价 ¥{total}，预算 ¥{budget}，剩余 ¥{budget - total:.0f}",
            retry_count=0,
        )

    retry_count = 0
    last_error = None
    
    while retry_count < MAX_RETRY:
        try:
            ai_result = await AIService.generate_config(budget, use_case, retry_count, last_error or "")
            
            if not ai_result or "configs" not in ai_result:
                last_error = "AI返回格式错误"
                retry_count += 1
                continue
            
            configs = ai_result["configs"]
            
            if len(configs) < 8:
                last_error = f"AI返回配置不完整，仅{len(configs)}个配件"
                retry_count += 1
                continue
            
            priced_configs = PriceService.get_prices_batch(configs)
            
            total_price = 0
            all_priced = True
            config_items = []
            
            for pc in priced_configs:
                price_info = pc["price_info"]
                item = ConfigItem(
                    category=pc["category"],
                    model=pc["model"],
                    reason=pc["reason"],
                    price_status="success" if price_info["success"] else "failed",
                    price_error=price_info.get("error") if not price_info["success"] else None,
                    title=price_info.get("title"),
                    link=price_info.get("link"),
                    substituted=price_info.get("substituted", False),
                    substitute_model=price_info.get("substitute_model"),
                )
                
                if price_info["success"] and price_info["price"] is not None:
                    item.price = price_info["price"]
                    total_price += price_info["price"]
                else:
                    all_priced = False
                
                config_items.append(item)
            
            # ---- 逐配件价格天花板：单配件价格不得超过预算 50% ----
            item_over_budget = False
            for item in config_items:
                if item.price and item.price > budget * 0.5:
                    last_error = f"{item.category}「{item.model}」单价{item.price:.0f}元超预算50%，选更便宜的"
                    retry_count += 1
                    item_over_budget = True
                    break
            if item_over_budget:
                continue

            if not all_priced:
                failed_count = sum(1 for c in config_items if c.price is None)
                priced_count = len([c for c in config_items if c.price is not None])
                tolerance = get_price_tolerance(budget)
                # 条件统一用 retry_count < MAX_RETRY（不加 -1！最后一轮也要拦截）
                should_retry = False
                if total_price > budget * tolerance and retry_count < MAX_RETRY:
                    last_error = f"已定价配件总价{total_price:.0f}元已超预算{budget:.0f}元（{int((tolerance-1)*100)}%容忍），需降低配置"
                    should_retry = True
                elif priced_count >= 2 and total_price > budget * 0.85 and retry_count < MAX_RETRY:
                    last_error = f"仅{priced_count}个已定价配件已达{total_price:.0f}元({total_price/budget*100:.0f}%)，其余配件将必然超预算"
                    should_retry = True
                if should_retry:
                    retry_count += 1
                    continue
                return ConfigResponse(
                    success=False,
                    total_price=total_price,
                    budget=budget,
                    remaining=budget - total_price,
                    configs=config_items,
                    message=f"有{failed_count}个配件无法获取价格，请检查型号是否正确或稍后重试",
                    retry_count=retry_count
                )

            tolerance = get_price_tolerance(budget)
            if total_price <= budget * tolerance:
                # 预算利用率检查：低于70%说明配置太低端，要求升级
                if total_price < budget * 0.70 and retry_count < MAX_RETRY:
                    last_error = f"总价{total_price:.0f}元仅占预算{budget:.0f}元的{total_price/budget*100:.0f}%，利用率不足70%，请升级核心配件"
                    retry_count += 1
                    continue
                return ConfigResponse(
                    success=True,
                    total_price=total_price,
                    budget=budget,
                    remaining=budget - total_price,
                    configs=config_items,
                    message="配置生成成功！",
                    retry_count=retry_count
                )
            else:
                if retry_count < MAX_RETRY:
                    last_error = f"总价{total_price:.0f}元超出预算{budget:.0f}元的{int((tolerance-1)*100)}%容忍范围"
                    retry_count += 1
                    continue
                # 重试耗尽：宁可返回空也不返回超预算配置
                break
                
        except Exception as e:
            last_error = str(e)
            retry_count += 1
            continue
    
    return ConfigResponse(
        success=False,
        total_price=0,
        budget=budget,
        remaining=budget,
        configs=[],
        message=f"配置生成失败，已重试{MAX_RETRY}次。最后错误：{last_error}",
        retry_count=retry_count
    )


# ==================== SSE 流式端点 ====================

# 固定展示顺序
DISPLAY_ORDER = ["CPU", "显卡", "内存", "固态硬盘", "主板", "电源", "机箱", "散热器"]

FAST_CHANNEL_CONFIG = [
    {"category": "CPU",     "model": "AMD 锐龙 R5 5600 盒装",               "price": 759,  "reason": "6核12线程，办公游戏通吃，性价比之王"},
    {"category": "显卡",    "model": "AMD RX6600 XT 8G",                     "price": 1299, "reason": "1080P高画质通吃网游与3A大作"},
    {"category": "内存",    "model": "金百达 银爵 DDR4 3200 32GB(16G×2)",     "price": 599,  "reason": "32GB大容量，多任务不卡顿"},
    {"category": "固态硬盘","model": "微闪 M200 PRO 1TB NVMe PCIe 3.0",       "price": 768,  "reason": "高速读写，游戏秒加载"},
    {"category": "主板",    "model": "华南金牌 H610 主板",                     "price": 279,  "reason": "稳定可靠，够用不浪费"},
    {"category": "电源",    "model": "长城 G6 650W 金牌",                     "price": 279,  "reason": "金牌效率，650W留足升级余量"},
    {"category": "机箱",    "model": "先马 平头哥 M2",                         "price": 75,   "reason": "散热良好，简约实用"},
    {"category": "散热器",  "model": "利民 Assassin X 120 Refined SE ARGB",   "price": 79,   "reason": "四热管塔式，压R5轻松低温"},
]


def _sse(data: dict) -> str:
    """格式化 SSE 消息，尾部加 padding 防 TCP Nagle 粘包"""
    payload = f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    # 每消息填充到 ~2KB，确保 TCP 立即发送不攒包
    if len(payload) < 2048:
        payload += f":{' ' * (2047 - len(payload))}\n\n"
    return payload


def _sse_connect() -> str:
    """SSE 连接建立初始注释，触发 HTTP 响应头立即发送"""
    return ":ok\n" + (":pad" + " " * 2040 + "\n") + "\n"


async def _fast_channel_stream(budget: float):
    """快速通道：固化配置，串行逐件输出，总耗时 ≤30s"""
    yield _sse_connect()  # 立即发送初始包，建立流式连接
    # 先发 slots 骨架，前端预建占位行
    yield _sse({"event": "slots", "order": DISPLAY_ORDER})
    per_item_delay = 3.5
    running_total = 0
    count = 0

    for i, item in enumerate(FAST_CHANNEL_CONFIG):
        await asyncio.sleep(per_item_delay)
        running_total += item["price"]
        count += 1
        yield _sse({
            "event": "item_done",
            "category": item["category"],
            "model": item["model"],
            "price": item["price"],
            "reason": item["reason"],
            "index": i,
        })
        yield _sse({
            "event": "total_update",
            "total_price": running_total,
            "count": count,
            "remaining": budget - running_total,
        })

    yield _sse({
        "event": "finish",
        "success": True,
        "total_price": running_total,
        "budget": budget,
        "remaining": budget - running_total,
        "configs_count": count,
        "message": f"配置生成成功！总价 ¥{running_total}，预算 ¥{budget}",
    })


async def _fetch_price_in_thread(category: str, model: str):
    """在线程池中执行同步的价格抓取"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: PriceService.get_price(model, category)
    )


async def _standard_stream(budget: float, use_case: str):
    """标准流式：AI选型 → 并行抓价 → 谁先完成先输出"""
    yield _sse_connect()  # 立即发送初始包，建立流式连接
    retry_count = 0
    last_error = ""

    while retry_count < MAX_RETRY:
        # 1. AI 生成全部8个配件型号
        yield _sse({"event": "status", "phase": "ai", "message": "AI 正在分析需求，生成配置方案..."})
        ai_result = await AIService.generate_config(budget, use_case, retry_count, last_error)
        if not ai_result or "configs" not in ai_result or len(ai_result["configs"]) < 8:
            last_error = "AI返回格式错误或配件不完整"
            retry_count += 1
            if retry_count < MAX_RETRY:
                yield _sse({"event": "retry", "reason": last_error, "retry_count": retry_count})
            continue

        configs = ai_result["configs"]

        # 2. 先发 slots 骨架（固定展示顺序），前端预建占位行
        yield _sse({"event": "slots", "order": DISPLAY_ORDER})
        yield _sse({"event": "status", "phase": "pricing", "message": f"AI 已生成 {len(configs)} 个配件，正在并行查询实时价格..."})
        
        # 3. 并行抓价 + 结果队列 + 逐件延时放出
        async def search_one(cfg):
            result = await _fetch_price_in_thread(
                cfg.get("category", ""), cfg.get("model", "")
            )
            return {
                "category": cfg.get("category", ""),
                "model": cfg.get("model", ""),
                "reason": cfg.get("reason", ""),
                "price": result.get("price") if result.get("success") else None,
                "price_status": "success" if result.get("success") else "failed",
                "title": result.get("title"),
                "link": result.get("link"),
            }

        # 所有任务并行启动
        tasks = [asyncio.create_task(search_one(c)) for c in configs]
        result_queue: asyncio.Queue = asyncio.Queue()

        async def collect_result(task):
            try:
                r = await task
                await result_queue.put(r)
            except Exception:
                pass

        # 每个任务完成后把结果放入队列
        for t in tasks:
            asyncio.create_task(collect_result(t))

        # 从队列逐件取出并延时放出（保证一个一个出来）
        results = []
        running_total = 0
        priced_count = 0
        received = 0

        while received < len(tasks):
            item_result = await result_queue.get()
            received += 1
            results.append(item_result)

            if item_result["price"] is not None:
                running_total += item_result["price"]
                priced_count += 1
                yield _sse({
                    "event": "item_done",
                    "category": item_result["category"],
                    "model": item_result["model"],
                    "price": item_result["price"],
                    "reason": item_result["reason"],
                    "title": item_result["title"],
                    "link": item_result["link"],
                })
                yield _sse({
                    "event": "total_update",
                    "total_price": running_total,
                    "count": priced_count,
                    "remaining": budget - running_total,
                })
            else:
                yield _sse({
                    "event": "error_item",
                    "category": item_result["category"],
                    "model": item_result["model"],
                    "reason": "价格获取失败",
                })

            # 两件之间至少隔 0.8s，制造逐个放出的视觉效果
            if received < len(tasks):
                await asyncio.sleep(0.8)

        # 4. 预算校验
        all_priced = all(r["price"] is not None for r in results)
        tolerance = get_price_tolerance(budget)

        if all_priced and running_total <= budget * tolerance:
            if running_total >= budget * 0.70:
                yield _sse({
                    "event": "finish",
                    "success": True,
                    "total_price": running_total,
                    "budget": budget,
                    "remaining": budget - running_total,
                    "configs_count": priced_count,
                    "retry_count": retry_count,
                    "message": f"配置生成成功！总价 ¥{running_total}",
                })
                return
            else:
                last_error = f"总价{running_total:.0f}元仅占预算{budget:.0f}元的{running_total/budget*100:.0f}%，利用率不足70%"
        elif not all_priced and running_total > 0 and running_total <= budget * tolerance:
            yield _sse({
                "event": "finish",
                "success": False,
                "total_price": running_total,
                "budget": budget,
                "remaining": budget - running_total,
                "configs_count": priced_count,
                "retry_count": retry_count,
                "message": f"部分配件价格获取失败，总价 ¥{running_total} 仅供参考",
            })
            return
        else:
            last_error = f"总价{running_total:.0f}元超出预算{budget:.0f}元容忍范围"

        retry_count += 1
        if retry_count < MAX_RETRY:
            yield _sse({"event": "retry", "reason": last_error, "retry_count": retry_count})

    # 重试耗尽
    yield _sse({
        "event": "finish",
        "success": False,
        "total_price": 0,
        "budget": budget,
        "remaining": budget,
        "configs_count": 0,
        "retry_count": retry_count,
        "message": f"配置生成失败，已重试{MAX_RETRY}次。最后错误：{last_error}",
    })


@app.post("/api/generate-stream")
async def generate_config_stream(request: ConfigRequest):
    """SSE 流式生成配置"""
    budget = request.budget
    use_case = request.use_case

    if budget <= 0:
        raise HTTPException(status_code=400, detail="预算必须大于0")
    if not use_case.strip():
        raise HTTPException(status_code=400, detail="请输入使用场景")

    if 3000 <= budget <= 5000:
        gen = _fast_channel_stream(budget)
    else:
        gen = _standard_stream(budget, use_case)

    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ==================== 社区 API ====================

@app.get("/api/community/posts")
async def get_community_posts(
    limit: int = Query(default=20, ge=5, le=100, description="帖子数量"),
):
    """获取社区帖子列表。

    优先从本地 posts.json 文件读取（秒级响应）。
    若尚未爬取过数据，返回空列表。
    """
    posts, source = get_posts()
    scrape_status = get_scrape_status()

    return {
        "success": True,
        "posts": posts[:limit],
        "total": len(posts),
        "source": source,                          # "scraped" | "empty"
        "scraping": scrape_status["running"],       # 是否正在后台爬取
        "scrape_progress": scrape_status["progress"] if scrape_status["running"] else "",
        "scrape_message": scrape_status["message"] if not scrape_status["running"] and scrape_status["result"] else "",
        "security_blocked": scrape_status.get("security_blocked", False),
    }


@app.post("/api/community/refresh")
async def refresh_community_posts(
    limit: int = Query(default=20, ge=5, le=100),
    force: bool = Query(default=False, description="强制重置卡死状态后重试"),
    mode: str = Query(default="requests", description="爬取模式: requests 或 selenium"),
    bduss: str = Query(default=None, description="登录态 BDUSS cookie（提供则走官方接口，稳定翻满 limit）"),
):
    """触发后台爬取（立即返回，不等待完成）。

    Args:
        limit: 目标帖子数量
        force: 强制重置卡死状态后重试
        mode: 爬取模式 - "requests"（可能被安全验证阻止）或 "selenium"（需要浏览器）
        bduss: 登录态 BDUSS cookie，提供后 requests 模式走官方接口

    前端通过 GET /api/community/status 轮询进度。
    如果上次爬取卡死，传 force=true 强制重置。
    """
    status = get_scrape_status()
    if status["running"]:
        if force:
            reset_scrape_state()
        else:
            return {
                "success": False,
                "message": f"爬取已在运行中: {status['progress']}",
                "status": status,
            }

    # 如果之前被安全验证阻止，建议使用 selenium 模式或提供 BDUSS
    if status.get("security_blocked") and mode == "requests" and not bduss:
        return {
            "success": False,
            "message": "之前的爬取被安全验证阻止。建议使用 Selenium 模式(mode=selenium) 或提供 BDUSS",
            "suggestion": "使用 Selenium 模式可以绕过安全验证；或提供 BDUSS 走官方接口",
        }

    started = run_scrape_background(kw="电脑", limit=limit, timeout=30.0, mode=mode, bduss=bduss)
    if not started:
        return {"success": False, "message": "无法启动爬取任务"}

    mode_desc = f"{mode}" + ("+BDUSS" if bduss else "")
    return {
        "success": True,
        "message": f"后台爬取已启动（模式: {mode_desc}），目标: {limit} 帖。请轮询 /api/community/status 获取进度。",
    }


@app.post("/api/community/reset")
async def reset_community():
    """强制重置爬取状态（解除卡死）。"""
    reset_scrape_state()
    return {"success": True, "message": "爬取状态已重置"}


@app.get("/api/community/status")
async def get_community_status():
    """获取后台爬取的实时状态（供前端轮询）。"""
    return {
        "success": True,
        **get_scrape_status(),
    }


@app.get("/api/community/info")
async def get_community_info():
    """获取社区功能的可用信息。"""
    selenium_available = False
    try:
        from tieba_selenium import run_selenium_scraper
        selenium_available = True
    except ImportError:
        pass
    
    return {
        "success": True,
        "selenium_available": selenium_available,
        "default_mode": "requests",
        "available_modes": ["requests", "selenium"],
        "tips": {
            "requests": "快速但可能被安全验证阻止",
            "selenium": "稳定但需要启动浏览器窗口（推荐被安全验证时使用）",
        },
    }


# ==================== Auth API ====================

class AuthRequest(BaseModel):
    account: str
    password: str


class ProfileUpdate(BaseModel):
    token: str
    nickname: str


@app.post("/api/auth/register")
async def auth_register(req: AuthRequest):
    """注册：账号 3-20 字符，密码 6+ 字符。账号不可重复。"""
    account = req.account.strip()
    password = req.password.strip()

    if len(account) < 3 or len(account) > 20:
        raise HTTPException(status_code=400, detail="账号长度需在 3-20 个字符之间")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="密码长度至少 6 个字符")

    existing = get_user_by_account(account)
    if existing:
        raise HTTPException(status_code=409, detail="账号已存在，请换一个账号或直接登录")

    password_hash = hashlib.sha256((account + password).encode()).hexdigest()
    user = create_user(account, password_hash)
    if not user:
        raise HTTPException(status_code=500, detail="注册失败，请稍后重试")

    token = _make_token(account)
    _token_store[token] = user["id"]

    return {
        "success": True,
        "user": {
            "id": user["id"],
            "account": user["account"],
            "nickname": user["nickname"] or user["account"],
            "avatar_url": f"/avatars/{user['avatar_path']}" if user.get("avatar_path") else "",
            "created_at": user["created_at"],
        },
        "token": token,
    }


@app.post("/api/auth/login")
async def auth_login(req: AuthRequest):
    """登录：校验账号密码。"""
    account = req.account.strip()
    password = req.password.strip()

    user = get_user_by_account(account)
    if not user:
        raise HTTPException(status_code=401, detail="账号不存在")

    expected_hash = hashlib.sha256((account + password).encode()).hexdigest()
    if user["password_hash"] != expected_hash:
        raise HTTPException(status_code=401, detail="密码错误")

    token = _make_token(account)
    _token_store[token] = user["id"]

    return {
        "success": True,
        "user": {
            "id": user["id"],
            "account": user["account"],
            "nickname": user["nickname"] or user["account"],
            "avatar_url": f"/avatars/{user['avatar_path']}" if user.get("avatar_path") else "",
            "created_at": user["created_at"],
        },
        "token": token,
    }


@app.post("/api/auth/logout")
async def auth_logout(token: str = Form(...)):
    """登出：清除服务端 token。"""
    _token_store.pop(token, None)
    return {"success": True}


# ==================== User Profile API ====================

@app.get("/api/user/profile")
async def user_profile(token: str = Query(...)):
    """获取用户资料。"""
    uid = _require_auth(token)
    user = get_user_by_id(uid)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {
        "success": True,
        "user": {
            "id": user["id"],
            "account": user["account"],
            "nickname": user["nickname"] or user["account"],
            "avatar_url": f"/avatars/{user['avatar_path']}" if user.get("avatar_path") else "",
            "created_at": user["created_at"],
        },
    }


@app.put("/api/user/profile")
async def user_update_profile(req: ProfileUpdate):
    """更新昵称。"""
    uid = _require_auth(req.token)
    nickname = req.nickname.strip()
    if not nickname or len(nickname) > 20:
        raise HTTPException(status_code=400, detail="昵称长度需在 1-20 个字符之间")
    update_user_nickname(uid, nickname)
    user = get_user_by_id(uid)
    return {
        "success": True,
        "user": {
            "id": user["id"],
            "account": user["account"],
            "nickname": user["nickname"] or user["account"],
            "avatar_url": f"/avatars/{user['avatar_path']}" if user.get("avatar_path") else "",
            "created_at": user["created_at"],
        },
    }


@app.post("/api/user/avatar")
async def user_upload_avatar(token: str = Form(...), file: UploadFile = File(...)):
    """上传头像。"""
    uid = _require_auth(token)

    # 限制文件类型和大小
    ext = os.path.splitext(file.filename or "avatar.png")[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        raise HTTPException(status_code=400, detail="仅支持 png/jpg/gif/webp 格式")
    content = await file.read()
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="头像文件不能超过 2MB")

    # 保存文件
    filename = f"avatar_{uid}_{int(time.time())}{ext}"
    filepath = os.path.join(AVATAR_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(content)

    update_user_avatar(uid, filename)
    return {"success": True, "avatar_url": f"/avatars/{filename}"}


# ==================== Favorites API ====================

class FavoriteRequest(BaseModel):
    token: str
    config_data: dict  # {budget, scenario, total_price, configs: [...], performance?}


@app.get("/api/favorites")
async def list_favorites(token: str = Query(...)):
    """获取当前用户的收藏列表。"""
    uid = _require_auth(token)
    favs = get_favorites(uid)
    # 将 config_json 字符串反序列化为对象
    for f in favs:
        try:
            f["configs"] = json.loads(f.pop("config_json"))
        except (json.JSONDecodeError, KeyError):
            f["configs"] = []
    return {"success": True, "favorites": favs}


@app.post("/api/favorites")
async def toggle_favorite(req: FavoriteRequest):
    """收藏/取消收藏（toggle）。"""
    uid = _require_auth(req.token)
    cd = req.config_data
    budget = cd.get("budget", 0)
    scenario = cd.get("scenario", "")
    total_price = cd.get("total_price", 0)
    configs = cd.get("configs", [])

    existing = find_favorite(uid, budget, scenario, total_price)
    if existing:
        remove_favorite(existing["id"], uid)
        return {"success": True, "is_favorited": False, "favorite_id": None}

    config_json = json.dumps(configs, ensure_ascii=False)
    fid = add_favorite(uid, config_json, budget, scenario, total_price)
    return {"success": True, "is_favorited": True, "favorite_id": fid}


@app.delete("/api/favorites/{fav_id}")
async def delete_favorite(fav_id: int, token: str = Query(...)):
    """删除指定收藏。"""
    uid = _require_auth(token)
    ok = remove_favorite(fav_id, uid)
    if not ok:
        raise HTTPException(status_code=404, detail="收藏记录不存在")
    return {"success": True}


# ==================== History API ====================

class HistoryRequest(BaseModel):
    token: str
    config_data: dict  # {budget, scenario, total_price, configs: [...], performance?}


@app.get("/api/history")
async def list_history(token: str = Query(...)):
    """获取当前用户的生成历史。"""
    uid = _require_auth(token)
    records = get_history(uid)
    for r in records:
        try:
            r["configs"] = json.loads(r.pop("config_json"))
        except (json.JSONDecodeError, KeyError):
            r["configs"] = []
        try:
            r["performance"] = json.loads(r.pop("performance_json")) if r.get("performance_json") else {}
        except (json.JSONDecodeError, KeyError):
            r["performance"] = {}
    return {"success": True, "history": records}


@app.post("/api/history")
async def save_history(req: HistoryRequest):
    """保存一条生成历史。"""
    uid = _require_auth(req.token)
    cd = req.config_data
    budget = cd.get("budget", 0)
    scenario = cd.get("scenario", "")
    total_price = cd.get("total_price", 0)
    configs = cd.get("configs", [])
    performance = cd.get("performance", {})

    config_json = json.dumps(configs, ensure_ascii=False)
    perf_json = json.dumps(performance, ensure_ascii=False) if performance else ""
    hid = add_history(uid, config_json, budget, scenario, total_price, perf_json)
    return {"success": True, "id": hid}


@app.delete("/api/history/{hid}")
async def delete_history(hid: int, token: str = Query(...)):
    """删除指定历史记录。"""
    uid = _require_auth(token)
    ok = remove_history(hid, uid)
    if not ok:
        raise HTTPException(status_code=404, detail="历史记录不存在")
    return {"success": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
