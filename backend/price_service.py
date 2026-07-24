import sys
import os
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from price_search import search_price, detect_category


# ========== 性能等效替代映射 ==========
# 当某个型号搜不到价格时，按优先级依次尝试性能相近的替代型号

GPU_ALTERNATIVES = {
    # AMD → NVIDIA / 同性能级
    "rx 7600": [
        "七彩虹 RTX 4060 8G", "华硕 RTX 4060 8G", "微星 RTX 4060 8G",
        "蓝宝石 RX 6650 XT 8G", "瀚铠 RX 6650 XT 8G",
    ],
    "rx 7700 xt": [
        "七彩虹 RTX 4060 Ti 8G", "华硕 RTX 4060 Ti 8G", "微星 RTX 4060 Ti 8G",
        "蓝宝石 RX 6800 16G", "撼讯 RX 6800 16G",
    ],
    "rx 7800 xt": [
        "七彩虹 RTX 4070 12G", "华硕 RTX 4070 12G", "微星 RTX 4070 12G",
        "蓝宝石 RX 7900 GRE 16G", "撼讯 RX 7900 GRE 16G",
    ],
    "rx 7900 gre": [
        "七彩虹 RTX 4070 Super 12G", "华硕 RTX 4070 Super 12G",
        "蓝宝石 RX 7800 XT 16G",
    ],
    "rx 7900 xt": [
        "七彩虹 RTX 4070 Ti Super 16G", "华硕 RTX 4070 Ti Super 16G",
        "蓝宝石 RX 7900 XTX 24G",
    ],
    "rx 6650 xt": [
        "七彩虹 RTX 4060 8G", "华硕 RTX 4060 8G",
        "蓝宝石 RX 7600 8G", "瀚铠 RX 7600 8G",
    ],
    "rx 6750 xt": [
        "七彩虹 RTX 4060 Ti 8G", "微星 RTX 4060 Ti 8G",
        "蓝宝石 RX 7700 XT 12G",
    ],
    # NVIDIA → AMD / 同性能级
    "rtx 4060": [
        "蓝宝石 RX 7600 8G", "瀚铠 RX 7600 8G",
        "蓝宝石 RX 6650 XT 8G", "瀚铠 RX 6650 XT 8G",
    ],
    "rtx 4060 ti": [
        "蓝宝石 RX 7700 XT 12G", "撼讯 RX 7700 XT 12G",
        "蓝宝石 RX 6750 XT 12G",
    ],
    "rtx 4070": [
        "蓝宝石 RX 7800 XT 16G", "撼讯 RX 7800 XT 16G",
        "蓝宝石 RX 7900 GRE 16G",
    ],
    "rtx 4070 super": [
        "蓝宝石 RX 7900 GRE 16G", "撼讯 RX 7900 GRE 16G",
        "蓝宝石 RX 7800 XT 16G",
    ],
    "rtx 4070 ti": [
        "蓝宝石 RX 7900 XT 20G", "撼讯 RX 7900 XT 20G",
        "蓝宝石 RX 7900 XTX 24G",
    ],
    "rtx 4080 super": [
        "蓝宝石 RX 7900 XTX 24G", "撼讯 RX 7900 XTX 24G",
    ],
    "rtx 3060": [
        "蓝宝石 RX 7600 8G", "瀚铠 RX 7600 8G",
        "七彩虹 RTX 4060 8G",
    ],
}

CPU_ALTERNATIVES = {
    "i5-13600kf": ["Intel 酷睿 i5-14600KF 盒装", "AMD 锐龙 R5 7600X 盒装", "Intel 酷睿 i5-13400F 盒装"],
    "i5-14600kf": ["Intel 酷睿 i5-13600KF 盒装", "AMD 锐龙 R5 7600X 盒装", "AMD 锐龙 R7 7700 盒装"],
    "i5-12400f": ["Intel 酷睿 i5-13400F 盒装", "AMD 锐龙 R5 5600 盒装", "AMD 锐龙 R5 7500F 盒装"],
    "i5-13400f": ["Intel 酷睿 i5-12400F 盒装", "AMD 锐龙 R5 7500F 盒装", "Intel 酷睿 i5-14400F 盒装"],
    "i5-14400f": ["Intel 酷睿 i5-13400F 盒装", "Intel 酷睿 i5-12400F 盒装"],
    "i7-13700kf": ["Intel 酷睿 i7-14700KF 盒装", "AMD 锐龙 R7 7800X3D 盒装"],
    "i7-14700kf": ["Intel 酷睿 i7-13700KF 盒装", "AMD 锐龙 R7 7800X3D 盒装"],
    "r5 5600": ["Intel 酷睿 i5-12400F 盒装", "AMD 锐龙 R5 7500F 盒装"],
    "r5 7500f": ["AMD 锐龙 R5 7600 盒装", "Intel 酷睿 i5-13400F 盒装", "Intel 酷睿 i5-12400F 盒装"],
    "r5 7600": ["AMD 锐龙 R5 7500F 盒装", "Intel 酷睿 i5-13600KF 盒装"],
    "r5 7600x": ["Intel 酷睿 i5-13600KF 盒装", "AMD 锐龙 R5 9600X 盒装"],
    "r7 7700": ["Intel 酷睿 i5-14600KF 盒装", "AMD 锐龙 R7 7700X 盒装"],
    "r7 7800x3d": ["Intel 酷睿 i7-14700KF 盒装", "AMD 锐龙 R7 9800X3D 盒装"],
}

MOTHERBOARD_ALTERNATIVES = {
    "b760m": ["华硕 PRIME B760M-K D4", "技嘉 B760M GAMING WIFI D4", "华擎 B760M Pro RS"],
    "b650m": ["技嘉 B650M GAMING WIFI", "华硕 TUF GAMING B650M-PLUS WIFI", "微星 MAG B650M MORTAR WIFI"],
    "h610m": ["华硕 PRIME H610M-K D4", "微星 PRO H610M-G DDR4", "技嘉 H610M S2 D4"],
    "a620m": ["技嘉 A620M GAMING X", "华硕 TUF GAMING A620M-PLUS WIFI"],
    "z790": ["微星 MAG Z790 TOMAHAWK WIFI", "华硕 TUF GAMING Z790-PLUS WIFI"],
}

MEMORY_DDR4_ALTERNATIVES = [
    "威刚 XPG 威龙 DDR4 3200 16GB(8G×2)",
    "海盗船 复仇者LPX DDR4 3200 16GB(8G×2)",
    "芝奇 幻光戟 DDR4 3600 16GB(8G×2)",
    "雷克沙 雷神之锤 DDR4 3200 16GB(8G×2)",
]

MEMORY_DDR5_ALTERNATIVES = [
    "威刚 XPG 威龙 DDR5 6000 32GB(16G×2)",
    "海盗船 复仇者 DDR5 6000 32GB(16G×2)",
    "芝奇 幻锋戟 DDR5 6000 32GB(16G×2)",
    "雷克沙 雷神之锤 DDR5 6000 32GB(16G×2)",
]

SSD_ALTERNATIVES = [
    "西部数据 SN770 1TB NVMe",
    "铠侠 EXCERIA G2 RC20 1TB",
    "三星 980 PRO 1TB NVMe",
    "致态 TiPlus7100 1TB",
    "英睿达 P3 Plus 1TB",
]

PSU_ALTERNATIVES = {
    "550w": ["长城 V5 550W 金牌", "酷冷至尊 MWE 550W 铜牌", "微星 MAG A550BN"],
    "650w": ["长城 G6 650W 金牌", "酷冷至尊 MWE 650W 铜牌", "振华 铜皇 650W"],
    "750w": ["长城 G7 750W 金牌", "酷冷至尊 MWE 750W 金牌", "振华 LEADEX HG 750W"],
    "850w": ["长城 GX850 金牌", "酷冷至尊 V850 金牌", "振华 LEADEX HG 850W"],
}

CASE_ALTERNATIVES = [
    "先马 平头哥 M2", "安钛克 DP502 FLUX", "九州风神 玄冰40", "追风者 P300A",
]

COOLER_ALTERNATIVES = [
    "利民 AX120R SE", "九州风神 玄冰400", "雅浚 B3",
    "利民 PA120 SE", "酷冷至尊 T620", "猫头鹰 NH-D15",
]


class PriceService:
    @staticmethod
    def _find_alternatives(category: str, model: str) -> list[str]:
        """根据分类和原始型号，返回性能相近的替代型号列表。"""
        model_l = model.lower()

        if category == "显卡":
            for key, alts in GPU_ALTERNATIVES.items():
                if key in model_l:
                    return alts
            return []

        elif category == "CPU":
            for key, alts in CPU_ALTERNATIVES.items():
                if key in model_l:
                    return alts
            return []

        elif category == "主板":
            for key, alts in MOTHERBOARD_ALTERNATIVES.items():
                if key in model_l:
                    return alts
            return []

        elif category == "内存":
            if "ddr5" in model_l:
                return MEMORY_DDR5_ALTERNATIVES
            else:
                return MEMORY_DDR4_ALTERNATIVES

        elif category == "固态硬盘":
            return SSD_ALTERNATIVES

        elif category == "电源":
            for watt_key, alts in PSU_ALTERNATIVES.items():
                if watt_key in model_l:
                    return alts
            return PSU_ALTERNATIVES.get("650w", [])

        elif category == "机箱":
            return CASE_ALTERNATIVES

        elif category == "散热器":
            return COOLER_ALTERNATIVES

        return []

    @staticmethod
    def get_price(model: str, category_hint: str = None) -> dict:
        """查询价格，搜不到时自动尝试性能等效替代型号。"""
        try:
            query = f"{category_hint} {model}" if category_hint else model
            result = search_price(query, category=category_hint)

            if not result.get("error") and result.get("price") is not None:
                return {
                    "success": True,
                    "error": None,
                    "price": result["price"],
                    "source": "zol",
                    "title": result.get("title"),
                    "link": result.get("link"),
                    "category": result.get("category"),
                    "substituted": False,
                }

            # === 搜不到 → 尝试替代型号 ===
            alternatives = PriceService._find_alternatives(category_hint, model)
            for alt in alternatives:
                alt_query = f"{category_hint} {alt}" if category_hint else alt
                alt_result = search_price(alt_query, category=category_hint)

                if not alt_result.get("error") and alt_result.get("price") is not None:
                    return {
                        "success": True,
                        "error": None,
                        "price": alt_result["price"],
                        "source": "zol",
                        "title": alt_result.get("title"),
                        "link": alt_result.get("link"),
                        "category": alt_result.get("category"),
                        "substituted": True,
                        "original_model": model,
                        "substitute_model": alt,
                    }

            # 替代也失败
            error_msg = result.get("error") or "未获取到有效价格（含替代型号）"
            return {
                "success": False,
                "error": error_msg,
                "price": None,
                "source": "zol",
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "price": None,
                "source": "zol",
            }

    @staticmethod
    def get_prices_batch(configs: list) -> list:
        category_mapping = {
            "CPU": "CPU",
            "显卡": "显卡",
            "内存": "内存",
            "固态硬盘": "固态硬盘",
            "主板": "主板",
            "电源": "电源",
            "机箱": "机箱",
            "散热器": "散热器"
        }

        results = []
        for config in configs:
            category = config.get("category", "")
            model = config.get("model", "")
            category_hint = category_mapping.get(category)

            price_result = PriceService.get_price(model, category_hint)

            results.append({
                "category": category,
                "model": model,
                "reason": config.get("reason", ""),
                "price_info": price_result
            })

        return results
