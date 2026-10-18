from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class InquiryAnalysis:
    country: str
    product_name: str
    quantity: str
    destination_port: str
    expected_delivery: str
    certification_requirements: str
    customer_summary: str
    missing_information: tuple[str, ...]
    next_actions: tuple[str, ...]


EXAMPLE_INQUIRY = (
    "Hello, we are a furniture importer based in Germany. We are interested in "
    "200 wooden dining tables. Please send us your product catalog and quotation. "
    "We need delivery to Hamburg within 45 days. Do you have FSC certification?"
)

COUNTRIES = {
    "Germany": "德国",
    "France": "法国",
    "Italy": "意大利",
    "Spain": "西班牙",
    "Portugal": "葡萄牙",
    "Netherlands": "荷兰",
    "Belgium": "比利时",
    "Switzerland": "瑞士",
    "Austria": "奥地利",
    "Poland": "波兰",
    "Sweden": "瑞典",
    "Norway": "挪威",
    "Denmark": "丹麦",
    "Finland": "芬兰",
    "United Kingdom": "英国",
    "UK": "英国",
    "United States": "美国",
    "USA": "美国",
    "Canada": "加拿大",
    "Australia": "澳大利亚",
    "New Zealand": "新西兰",
    "Japan": "日本",
    "South Korea": "韩国",
    "China": "中国",
    "India": "印度",
    "Singapore": "新加坡",
    "United Arab Emirates": "阿联酋",
    "UAE": "阿联酋",
    "Brazil": "巴西",
    "Mexico": "墨西哥",
    "South Africa": "南非",
}

PRODUCT_PATTERNS = (
    r"wooden\s+dining\s+tables?",
    r"dining\s+tables?",
    r"office\s+chairs?",
    r"wooden\s+chairs?",
    r"chairs?",
    r"sofas?",
    r"beds?",
    r"desks?",
    r"cabinets?",
    r"tables?",
)
CERTIFICATION_PATTERNS = (
    r"\bFSC\b",
    r"\bBSCI\b",
    r"\bCE\b",
    r"\bISO\s*9001\b",
    r"\bREACH\b",
    r"\bRoHS\b",
)
QUANTITY_BRIDGE_WORDS = {
    "of",
    "piece",
    "pieces",
    "pc",
    "pcs",
    "unit",
    "units",
    "set",
    "sets",
    "wooden",
    "wood",
    "solid",
    "oak",
    "pine",
    "teak",
    "dining",
    "round",
    "rectangular",
    "folding",
    "extendable",
    "large",
    "small",
    "custom",
}


def _find_country(text: str) -> str:
    for country, localized_country in sorted(
        COUNTRIES.items(), key=lambda item: len(item[0]), reverse=True
    ):
        country_match = re.search(
            rf"\b(?:the\s+)?{re.escape(country)}\b", text, flags=re.IGNORECASE
        )
        if country_match is None:
            continue

        preceding_text = text[max(0, country_match.start() - 100) : country_match.start()]
        if re.search(
            r"\b(?:based|located|headquartered)\s+in\s+(?:the\s+)?$",
            preceding_text,
            flags=re.IGNORECASE,
        ):
            return localized_country
    return "未知"


def detect_country(text: str) -> str:
    """基于邮件正文的规则识别客户国家；未识别时返回“未知”。

    仅在整个邮件中出现“based in / located in / headquartered in + 国家”这类
    明确表述时才返回结果，不依赖客户姓名或邮箱后缀等不可靠线索。
    """
    return _find_country(text)


def _find_product(text: str) -> tuple[str, re.Match[str] | None]:
    for pattern in PRODUCT_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return re.sub(r"\s+", " ", match.group()).lower(), match
    return "未提及", None


def _find_quantity(text: str, product_match: re.Match[str] | None) -> str:
    if product_match is None:
        return "未提及"

    preceding_text = text[max(0, product_match.start() - 100) : product_match.start()]
    numbers = list(re.finditer(r"\b\d[\d,]*(?:\.\d+)?\b", preceding_text))
    if not numbers:
        return "未提及"

    number = numbers[-1]
    between_text = preceding_text[number.end() :]
    bridge_words = re.findall(r"[A-Za-z-]+", between_text.lower())
    if (
        len(between_text) > 80
        or re.search(r"[.!?;\n]", between_text)
        or any(word not in QUANTITY_BRIDGE_WORDS for word in bridge_words)
    ):
        return "未提及"
    return f"{number.group().replace(',', '')}（原文数量；单位未单独注明）"


def _find_destination(text: str) -> tuple[str, bool]:
    port_match = re.search(
        r"(?i:\b(?:port\s+of|destination\s+port(?:\s+is)?|port\s*:)\s*)"
        r"([A-Z][A-Za-z-]*(?:\s+[A-Z][A-Za-z-]*){0,2})",
        text,
    )
    if port_match:
        return f"{port_match.group(1)}（原文明示为目的港）", True

    delivery_match = re.search(
        r"(?i:\b(?:delivery|deliver|ship(?:ment)?)\s+to\s+)"
        r"([A-Z][A-Za-z-]*(?:\s+[A-Z][A-Za-z-]*){0,2})",
        text,
    )
    if delivery_match:
        return f"{delivery_match.group(1)}（原文提及交付地点，未明确为目的港）", False
    return "未提及", False


def _find_expected_delivery(text: str) -> str:
    duration_match = re.search(
        r"\bwithin\s+(\d+\s+(?:days?|weeks?|months?))\b",
        text,
        flags=re.IGNORECASE,
    )
    if duration_match:
        return f"{duration_match.group(1)}（原文未说明起算日）"

    deadline_match = re.search(
        r"\b(?:by|before)\s+"
        r"((?:\d{1,2}\s+)?(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|"
        r"Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
        r"Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"\s+\d{1,2}(?:,?\s+\d{4})?|\d{4}-\d{2}-\d{2})\b",
        text,
        flags=re.IGNORECASE,
    )
    if deadline_match:
        return f"{deadline_match.group(1)}（原文未说明是否为交付日期）"
    return "未提及"


def _find_certifications(text: str) -> str:
    found = []
    for pattern in CERTIFICATION_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            found.append(re.sub(r"\s+", " ", match.group()).upper())
    if not found:
        return "未提及"
    return "、".join(found) + "（客户询问或提及；我方是否具备未知）"


def analyze_inquiry(text: str) -> InquiryAnalysis:
    """从英文询盘中提取有明确文本依据的字段，不补猜未提及的信息。"""
    country = _find_country(text)
    product_name, product_match = _find_product(text)
    quantity = _find_quantity(text, product_match)
    destination_port, is_explicit_port = _find_destination(text)
    expected_delivery = _find_expected_delivery(text)
    certification_requirements = _find_certifications(text)

    missing_information = []
    if country == "未知":
        missing_information.append("客户所在国家未提及。")
    if product_name == "未提及":
        missing_information.append("具体产品名称或型号未提及。")
    else:
        missing_information.append("产品型号、尺寸和完整规格未提及。")
    if quantity == "未提及":
        missing_information.append("采购数量未提及。")
    if destination_port == "未提及":
        missing_information.append("目的港或交付地点未提及。")
    elif not is_explicit_port:
        missing_information.append("原文只提及交付地点，尚未明确目的港。")
    if expected_delivery == "未提及":
        missing_information.append("期望交期未提及。")
    elif "起算日" in expected_delivery or "是否为交付日期" in expected_delivery:
        missing_information.append("请确认交期的具体日期及起算日。")
    if certification_requirements != "未提及":
        missing_information.append("公司是否具备客户提及的认证需核实。")

    if "catalog" in text.lower() or "quotation" in text.lower() or "quote" in text.lower():
        next_actions = [
            "准备并发送产品目录；报价前先核实型号、规格和价格依据。",
        ]
    else:
        next_actions = [
            "确认客户需要的产品资料及是否需要正式报价。",
        ]

    if product_name != "未提及":
        next_actions.append("与客户确认产品型号、尺寸、材质及其他规格要求。")
    if not is_explicit_port:
        next_actions.append("确认汉堡等交付地点是否为目的港，并核实完整收货信息。")
    if expected_delivery != "未提及":
        next_actions.append("确认期望交期的具体日期及起算日。")
    if certification_requirements != "未提及":
        next_actions.append("核实公司认证资料后再回复，不要在核实前承诺已具备认证。")

    summary_parts = []
    if country != "未知":
        summary_parts.append(f"客户来自{country}")
    if product_name != "未提及":
        summary_parts.append(f"咨询{product_name}")
    if quantity != "未提及":
        summary_parts.append(f"采购数量为{quantity}")
    if expected_delivery != "未提及":
        summary_parts.append(f"期望交期为{expected_delivery}")
    if destination_port != "未提及":
        summary_parts.append(f"要求交付至{destination_port}")

    summary = "，".join(summary_parts) if summary_parts else "询盘中未提及客户、产品或采购安排的明确细节。"
    if certification_requirements != "未提及":
        summary += f"，并询问{certification_requirements.split('（')[0]}认证情况"
    if "catalog" in text.lower():
        summary += "，并索要产品目录"
    if "quotation" in text.lower() or "quote" in text.lower():
        summary += "，并索要报价"
    summary += "。"

    return InquiryAnalysis(
        country=country,
        product_name=product_name,
        quantity=quantity,
        destination_port=destination_port,
        expected_delivery=expected_delivery,
        certification_requirements=certification_requirements,
        customer_summary=summary,
        missing_information=tuple(missing_information),
        next_actions=tuple(next_actions),
    )
