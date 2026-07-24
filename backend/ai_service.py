import httpx
import json
import re
from typing import Optional
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class AIService:
    API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
    BASE_URL = "https://api.deepseek.com/anthropic"
    MODEL = "deepseek-v4-pro"

    SYSTEM_PROMPT = """你是专业DIY装机顾问。用户给你预算和需求，你输出一套完整的7配件配置清单（JSON格式）。你的推荐会被系统自动查询实时价格，所以请确保型号具体、主流、可查到价格。

## 场景识别
- 「3A大作/黑神话/赛博朋克/3A」→ 显卡为绝对核心，CPU够用即可
- 「LOL/CS2/瓦洛兰特/永劫/网游/电竞」→ CPU单核性能优先，显卡次之
- 「PR/AE/剪映/剪辑/渲染」→ CPU多核+大内存优先
- 「办公/家用/影音/上网」→ 核显即可，严禁独显！核显CPU选非F后缀或G后缀（i3-12100/R5 5600G）

## 预算分配规则
你必须把预算按比例分给每个配件再选型号，不要凭感觉。
- 3A大作：显卡40-45% CPU15-20% 主板8-10% 内存6-8% 固态6-8% 电源5-7% 机箱3-5% 散热器2-3%
- 网游电竞：CPU25-30% 显卡25-30% 主板10-12% 内存8-12% 固态8-10% 电源6-8% 机箱4-6% 散热器2-4%
- 视频剪辑：CPU25-30% 内存15-20% 显卡15-20% 固态10-15% 主板8-10% 电源6-8% 机箱3-5% 散热器2-4%
- 办公家用：CPU25-30% 固态15-20% 内存10-15% 主板10-15% 电源8-12% 机箱8-12% 显卡0% 散热器2-3%

## 各配件选型指南

### CPU（F后缀=无核显！办公/家用必须选非F或G后缀！）
【带核显 — 办公/家用/影音 必须选这组，无需独显】
- <¥500：Intel 赛扬 G6900 盒装 / AMD 速龙 3000G 盒装
- ¥500-800：Intel 酷睿 i3-12100 盒装 / AMD 锐龙 R5 5500GT 盒装
- ¥800-1500：Intel 酷睿 i5-12400 盒装 / AMD 锐龙 R5 5600G 盒装 / AMD 锐龙 R5 8600G 盒装
- ¥1500-2500：Intel 酷睿 i5-13400 盒装 / AMD 锐龙 R7 8700G 盒装
- >¥2500：Intel 酷睿 i7-14700 盒装 / AMD 锐龙 R7 9700X 盒装

【不带核显 — 仅限游戏/剪辑场景，必须搭配独显！】
- <¥800：i3-12100F / R5 5500
- ¥800-1500：i5-12400F / R5 5600 / R5 7500F
- ¥1500-2500：i5-13600KF / i5-14600KF / R5 7600X / R7 7700
- >¥2500：i7-14700KF / R7 7800X3D

⚠️ 办公/家用选F后缀CPU = 必须补独显 = 预算爆炸！绝对禁止！

### 显卡
- 办公/家用场景：严禁独显，此项输出 model 为 "无（CPU核显）"
- <¥2500：RTX 4060 / RX 7600 / RX 6650XT
- ¥2500-4000：RTX 4060Ti / RX 7700XT
- ¥4000-5500：RTX 4070S / RX 7900GRE / RX 7800XT
- >¥5500：RTX 4070TiS / RTX 4080S / RX 7900XTX

### 内存
- 预算≤¥12000一律DDR4，严禁DDR5（DDR5京东价比DDR4贵3-5倍！）
- 预算>¥12000才可选DDR5入门级(CL30-38)
- 禁止：金士顿/Kingston、C26/C28旗舰条、皇家戟、联名款、DDR5高频条
- 正确写法示例：「威刚 XPG 威龙 DDR4 3200 16GB(8G×2)」
- 预算<8000→16GB(8G×2)，≥8000→32GB(16G×2)

### 主板
- Intel：H610(入门) B760(主流) Z790(旗舰)
- AMD：A620(入门) B650(主流) X670(旗舰)
- 必须与CPU插槽匹配（Intel用LGA1700，AMD用AM5/AM4）

### 固态硬盘
- 1TB NVMe PCIe 4.0/3.0 即可，不必追求旗舰
- 推荐品牌：西部数据/铠侠/致态/三星

### 电源
- 有独显：显卡+CPU功耗之和×1.5=所需瓦数，再向上取整到50W
- 无独显办公机：350W-450W足够，选最便宜品牌
- 选择该瓦数下最便宜的主流品牌金牌/铜牌电源

### 机箱
- 选¥100-300价位主流品牌，不超预算5%

### 散热器
- 入门：利民 AX120R SE / 九州风神 玄冰400 / 雅浚 B3（¥50-80 塔式风冷）
- 中端：利民 PA120 SE / 酷冷至尊 T620（¥120-180 双塔）
- 高端：猫头鹰 NH-D15 / 海盗船 H150i 水冷（¥300+）
- 办公/家用：盒装CPU自带散热器即可；如需列出选¥30-50风冷
- Intel/AMD 平台通用型号优先，避免推荐限定单一平台的型号

## 硬性预算红线（绝对不可违反！！）
- 总预算绝对不能超出用户预算。宁可低配不能超预算。
- 办公/家用预算≤5000元：CPU必须带核显（非F/G后缀），主板H610/A620入门级，内存DDR4 16GB，固态512GB-1TB，电源350-450W，严禁独显。
- 不确定价格时选价位段最低配。系统查不到价会自动找替代型号，不要自己"预判高价"。
- 每选一个配件时问自己：这个配件市场价大概多少？8个配件加起来会超预算吗？

## 输出格式
严格输出JSON，不要markdown代码块，不要额外文本：
{
    "configs": [
        {"category": "CPU", "model": "Intel 酷睿 i5-12400 盒装", "reason": "办公多任务，内置UHD730核显无需独显"},
        {"category": "显卡", "model": "七彩虹 RTX 4060 8G", "reason": "1080P高画质通吃网游（办公场景则填：无（CPU核显））"},
        {"category": "内存", "model": "威刚 XPG 威龙 DDR4 3200 16GB(8G×2)", "reason": "双通道低延迟"},
        {"category": "固态硬盘", "model": "铠侠 EXCERIA G2 RC20 1TB", "reason": "高速加载"},
        {"category": "主板", "model": "微星 PRO H610M-G DDR4", "reason": "够用且稳定"},
        {"category": "电源", "model": "长城 V5 550W 金牌", "reason": "整机约300W，留足余量"},
        {"category": "机箱", "model": "先马 平头哥 M2", "reason": "散热良好"},
        {"category": "散热器", "model": "利民 AX120R SE", "reason": "百元级四热管，压i5轻松"}
    ]
}
category只能是这8个：CPU/显卡/内存/固态硬盘/主板/电源/机箱/散热器。共8个，一个不少。"""

    @staticmethod
    def _repair_json(json_str: str) -> str:
        """修复 LLM 输出的常见 JSON 语法错误（缺逗号、尾随逗号等）。"""
        # 去除 markdown 代码块标记
        json_str = re.sub(r"```(?:json)?\s*\n?", "", json_str)
        json_str = re.sub(r"\n?```", "", json_str)
        # 统一换行
        json_str = json_str.replace("\r\n", "\n")

        # 1. 删除 } 或 ] 前的尾随逗号
        json_str = re.sub(r",(\s*[}\]])", r"\1", json_str)

        # 2. "..."\n  "..." → "...",\n  "..."
        json_str = re.sub(r'"\s*\n\s*"', r'",\n  "', json_str)

        # 3. "..."\n  { → "...",\n  {
        json_str = re.sub(r'"\s*\n\s*\{', r'",\n  {', json_str)

        # 4. } 或 ] 后换行接 { → },\n  {
        json_str = re.sub(r"([}\]])\s*\n\s*\{", r"\1,\n  {", json_str)

        # 5. } 或 ] 后换行接 " → },\n  "
        json_str = re.sub(r'([}\]])\s*\n\s*"', r'\1,\n  "', json_str)

        # 6. 数字后换行接 " → 数字,\n  "
        json_str = re.sub(r'(\d)\s*\n\s*"', r'\1,\n  "', json_str)

        # 7. bool/null 后换行接 " → bool,\n  "
        json_str = re.sub(r'(true|false|null)\s*\n\s*"', r'\1,\n  "', json_str)

        return json_str

    @staticmethod
    def _regex_extract_configs(text: str) -> dict | None:
        """正则兜底提取：从任意文本中提取 configs 数组（即使 JSON 格式损坏）。"""
        # 匹配每个 config 块：{"category":"...","model":"...","reason":"..."}
        # 兼容缺逗号、多余空格等格式问题
        items = []
        # 方法1：逐块提取 { "category": ... }
        blocks = re.findall(
            r'\{\s*"category"\s*:\s*"([^"]+)"\s*[,;\s]*'
            r'"model"\s*:\s*"([^"]+)"\s*[,;\s]*'
            r'"reason"\s*:\s*"([^"]*?)"\s*\}',
            text, re.DOTALL
        )
        for cat, model, reason in blocks:
            items.append({
                "category": cat.strip(),
                "model": model.strip(),
                "reason": reason.strip() or "性价比之选"
            })

        if len(items) >= 4:  # 至少需要4个配件
            print(f"正则兜底提取成功: {len(items)}个配件")
            return {"configs": items}

        # 方法2：更宽松的匹配（category/model/reason 顺序可能不同）
        items2 = []
        cats = re.findall(r'"category"\s*:\s*"([^"]+)"', text)
        models = re.findall(r'"model"\s*:\s*"([^"]+)"', text)
        reasons = re.findall(r'"reason"\s*:\s*"([^"]*?)"', text)
        min_len = min(len(cats), len(models), len(reasons))
        for i in range(min_len):
            items2.append({
                "category": cats[i].strip(),
                "model": models[i].strip(),
                "reason": reasons[i].strip() or "性价比之选"
            })

        if len(items2) >= 4:
            print(f"正则兜底提取成功(v2): {len(items2)}个配件")
            return {"configs": items2}

        return None

    @classmethod
    async def generate_config(cls, budget: float, use_case: str, retry: int = 0,
                              retry_reason: str = "") -> Optional[dict]:
        if retry == 0:
            retry_hint = ""
        else:
            # 检测是否为办公/家用场景（无独显场景）
            is_office = any(kw in use_case for kw in ["办公", "家用", "影音", "上网", "学习", "文档", "炒股"])
            if "利用" in retry_reason or "不足" in retry_reason:
                if is_office:
                    retry_hint = (f"\n⚠️ 第{retry+1}次重试：总价远低于预算。办公场景升级方向：升级CPU（非F！）→固态容量→内存容量。严禁加独显！")
                else:
                    retry_hint = (f"\n⚠️ 第{retry+1}次重试：总价远低于预算，请升级显卡/CPU直到接近预算。")
            else:
                if is_office:
                    retry_hint = (f"\n⚠️ 第{retry+1}次重试：总价超预算。降级顺序：CPU→固态→主板→机箱。严禁加独显！CPU必须是带核显的非F/G后缀型号！")
                else:
                    retry_hint = (f"\n⚠️ 第{retry+1}次重试：总价超预算。降级顺序：先降显卡→CPU→主板，内存已是DDR4不要动。")

        prompt = f"""用户需求：
- 预算：{budget}元
- 使用场景：{use_case}

请严格按照系统提示中的预算分配比例表，为每个配件分配合理的预算金额，然后选择型号。{retry_hint}

推荐完整的电脑硬件配置清单，包含8个配件：CPU、显卡、内存、固态硬盘、主板、电源、机箱、散热器。"""

        headers = {
            "x-api-key": cls.API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }

        payload = {
            "model": cls.MODEL,
            "max_tokens": 4096,
            "system": cls.SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{cls.BASE_URL}/v1/messages",
                    headers=headers,
                    json=payload
                )
                
                if response.status_code == 200:
                    data = response.json()
                    content = data.get("content", [])
                    if content and isinstance(content, list):
                        text_content = None
                        for item in content:
                            if item.get("type") == "text":
                                text_content = item.get("text", "")
                                break
                        
                        if text_content:
                            json_start = text_content.find("{")
                            json_end = text_content.rfind("}") + 1
                            if json_start >= 0 and json_end > json_start:
                                json_str = text_content[json_start:json_end]
                                try:
                                    return json.loads(json_str)
                                except json.JSONDecodeError:
                                    repaired = cls._repair_json(json_str)
                                    try:
                                        return json.loads(repaired)
                                    except json.JSONDecodeError as e2:
                                        print(f"JSON修复也失败: {e2}")
                            # 最终兜底：正则提取
                            fallback = cls._regex_extract_configs(text_content)
                            if fallback and "configs" in fallback:
                                return fallback
                            print(f"正则提取也失败，原始内容前200字符: {text_content[:200]}")
                    return None
                else:
                    print(f"AI请求失败: {response.status_code} - {response.text}")
                    return None
        except Exception as e:
            print(f"AI请求异常: {e}")
            return None
