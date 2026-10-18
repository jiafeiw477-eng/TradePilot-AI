from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

import services.ai_assistant as ai_assistant
import services.app_settings as app_settings
import services.customer_manager as customer_manager
import services.database as database
import services.deepseek_client as deepseek_client
import services.inquiry_history as inquiry_history
import services.product_catalog as product_catalog
from services.inquiry_ai import (
    InquiryAIAnalysis,
    InquiryAIError,
    analyze_inquiry_with_ai,
)


logger = logging.getLogger(__name__)

# 应用自身版本号：仅用于设置页的系统信息展示，不涉及任何业务数据。
APP_VERSION = "0.4.0"

PAGE_DESCRIPTIONS: dict[str, str] = {
    "业务概览": "基于本机保存的真实询盘记录，查看核心指标、趋势、状态分布和业务洞察。",
    "询盘助手": "粘贴客户英文询盘，获取中文翻译、需求要点、风险提示和英文回复草稿。",
    "AI 助手": "与 AI 助手连续对话：分析询盘、起草英文回复、查询产品资料、整理客户跟进建议。",
    "产品目录": "维护产品资料（名称、SKU、分类、价格、MOQ 等），AI 助手回答产品问题时会优先使用这里的真实记录。",
    "客户管理": "维护客户资料与等级，查看客户关联的询盘记录并安排下一步跟进。",
    "历史记录": "查看、复制或删除保存在本机的历史询盘分析。",
    "询盘管理": "维护询盘跟进状态、业务优先级、备注和下次跟进日期。",
    "设置": "配置 AI 回复偏好与通用选项，查看模型与数据库运行状态；API 密钥仍由 .env 管理。",
}

# 侧边栏底部说明：按页面提示当前数据流向。
SIDEBAR_NOTES: dict[str, str] = {
    "业务概览": "本地数据：统计口径全部来自本机 SQLite 中的真实询盘记录。",
    "询盘助手": "AI 模式：点击分析后，邮件内容将发送至 DeepSeek API。",
    "AI 助手": "AI 模式：发送消息后，对话内容与匹配到的业务记录将发送至 DeepSeek API。",
    "产品目录": "本地数据：产品资料保存在本机 SQLite 数据库中。",
    "客户管理": "本地数据：客户资料保存在本机 SQLite；有关联询盘的客户删除时会自动改为归档。",
    "历史记录": "本地记录：分析结果保存在本机 SQLite 数据库中。",
    "询盘管理": "本地管理：所有跟进信息保存在本机 SQLite 数据库中。",
    "设置": "本地配置：偏好设置保存在本机 SQLite；API 密钥只从 .env 或 Secrets 读取。",
}

# 客户需求字段的固定展示顺序，保证三个页面呈现一致。
NEED_FIELD_LABELS: tuple[tuple[str, str], ...] = (
    ("product", "产品"),
    ("specifications", "规格"),
    ("quantity", "数量"),
    ("target_price", "目标价格"),
    ("delivery_time", "交货时间"),
    ("trade_terms", "贸易条款"),
    ("other_requirements", "其他要求"),
)

EXAMPLE_INQUIRY = (
    "Hello, we are a furniture importer based in Germany. We are interested in "
    "200 wooden dining tables, preferably made from FSC-certified oak. Our target "
    "price is USD 180 per table. Please send your product catalog and quotation. "
    "We need delivery to Hamburg within 45 days under FOB terms. Could you also "
    "confirm your payment terms and whether samples are available?"
)

# 仅供类型标注：分析结果与历史记录具有相同的展示字段。
AnalysisLike = InquiryAIAnalysis | inquiry_history.InquiryHistoryRecord

THEME_CSS = """
<style>
/* 统一视觉规范：白底 + 浅灰分区 + 深海军蓝文字 + 克制的蓝色强调 */
:root {
  --tp-border: #E3E7ED;
  --tp-surface: #F5F7FA;
  --tp-surface-raised: #FFFFFF;
  --tp-text: #1B2A3D;
  --tp-muted: #5A6775;
  --tp-accent: #1F4E79;
}
.block-container {
  padding: 1.8rem clamp(1rem, 2.5vw, 2.25rem) 3rem;
  max-width: 1440px;
}
h1, h2, h3, h4 { color: var(--tp-text); letter-spacing: .005em; }
h1 { font-size: clamp(1.8rem, 2.4vw, 2.2rem); font-weight: 650; margin-bottom: .35rem; }
h2 { font-size: 1.22rem; padding-bottom: .45rem; border-bottom: 1px solid var(--tp-border); margin-top: 1.8rem; }
h3 { font-size: 1.0rem; margin-top: 1.25rem; }
[data-testid="stCaptionContainer"] p { color: var(--tp-muted); font-size: .82rem; }
[data-testid="stSidebar"] {
  background: var(--tp-surface);
  border-right: 1px solid var(--tp-border);
  min-width: 16rem;
  max-width: 16rem;
}
.tp-brand { font-size: 1.06rem; font-weight: 700; color: var(--tp-text); letter-spacing: .02em; }
.tp-brand-sub { margin-top: .2rem; font-size: .78rem; line-height: 1.55; color: var(--tp-muted); }
.tp-nav-label { margin: .1rem 0 .45rem; font-size: .74rem; font-weight: 600; letter-spacing: .06em; color: var(--tp-muted); }
.stButton > button, .stDownloadButton > button { border-radius: 6px; min-height: 2.4rem; font-weight: 550; }
.stTextArea textarea { border-radius: 6px; }
[data-testid="stTextInput"] input, [data-testid="stSelectbox"] [role="combobox"] { border-radius: 6px; }
[data-testid="stMetric"] {
  min-height: 6rem;
  padding: .8rem .9rem;
  background: var(--tp-surface-raised);
  border: 1px solid var(--tp-border);
  border-radius: 8px;
}
[data-testid="stMetricLabel"] { white-space: normal; line-height: 1.35; }
[data-testid="stMetricLabel"] p {
  white-space: normal;
  overflow: visible;
  text-overflow: clip;
}
[data-testid="stMetricValue"] { color: var(--tp-text); font-size: 1.8rem; line-height: 1.2; }
[data-testid="stCode"], pre { border-radius: 6px; }
[data-testid="stDivider"], hr { border-color: var(--tp-border); }
table { font-size: .88rem; }
@media (max-width: 760px) {
  .block-container { padding: 1.25rem 1rem 2.25rem; }
  h1 { font-size: 1.7rem; }
  [data-testid="stMetric"] { min-height: 5.25rem; padding: .65rem; }
  [data-testid="stMetricValue"] { font-size: 1.55rem; }
}
</style>
"""


def format_timestamp(value: str) -> str:
    """把 ISO 时间转换为紧凑的本地展示格式，解析失败时回退为原值。"""
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return value


def render_customer_needs(needs: dict[str, str]) -> None:
    """以两列表格展示客户需求与关键业务信息，避免堆叠卡片。"""
    lines = ["| 需求项 | 内容 |", "| --- | --- |"]
    for field, label in NEED_FIELD_LABELS:
        value = str(needs.get(field, "")).strip() or "未提供"
        value = value.replace("\n", " ").replace("|", "\\|")
        lines.append(f"| **{label}** | {value} |")
    st.markdown("\n".join(lines))


def render_analysis_details(analysis: AnalysisLike, *, key_prefix: str) -> None:
    """询盘助手、历史记录、询盘管理共用同一套结果分区，保证展示一致。"""
    st.subheader("询盘中文翻译")
    st.write(analysis.translation_zh)

    st.subheader("客户需求分析")
    render_customer_needs(analysis.customer_needs)

    st.subheader("客户意向判断")
    st.markdown(f"**辅助判断：{analysis.intent_level}意向**")
    st.write(analysis.intent_rationale)
    st.caption("AI 意向判断仅供参考，不等同于你已经确认的业务优先级。")

    st.subheader("风险与待确认事项")
    if analysis.risks_and_questions:
        for item in analysis.risks_and_questions:
            st.markdown(f"- {item}")
    else:
        st.write("AI 未列出待确认事项；请结合业务经验人工复核。")

    st.subheader("英文回复草稿")
    st.caption("点击代码块右上角的复制图标即可复制英文草稿，也可以下载为 txt 文件。")
    st.code(analysis.reply_draft_en, language=None)
    st.download_button(
        "下载英文草稿 (.txt)",
        data=analysis.reply_draft_en,
        file_name="reply_draft_en.txt",
        mime="text/plain",
        key=f"download_reply_{key_prefix}",
    )
    st.markdown("**回复草稿中文翻译**")
    st.write(analysis.reply_draft_zh)

    st.subheader("后续跟进建议")
    if analysis.follow_up_suggestions:
        for suggestion in analysis.follow_up_suggestions:
            st.markdown(f"- {suggestion}")
    else:
        st.write("AI 未给出跟进建议；请按业务节奏安排下一步。")


def save_management_field_changes(record_id: str) -> None:
    """字段变更后立即写入 SQLite，并为页面显示安全的保存反馈。"""
    date_value = st.session_state[f"next_follow_up_date_{record_id}"]
    try:
        updated = inquiry_history.update_inquiry_follow_up(
            record_id,
            status=st.session_state[f"follow_up_status_{record_id}"],
            priority=st.session_state[f"follow_up_priority_{record_id}"],
            notes=st.session_state[f"follow_up_notes_{record_id}"],
            next_follow_up_date=(
                date_value.isoformat() if date_value else None
            ),
        )
    except inquiry_history.InquiryHistoryError as exc:
        st.session_state["inquiry_management_save_error"] = str(exc)
        return
    if updated:
        st.session_state["inquiry_management_saved"] = (
            "跟进信息已自动保存到本地数据库。"
        )
        st.session_state.pop("inquiry_management_save_error", None)
    else:
        st.session_state["inquiry_management_save_error"] = (
            "这条询盘记录不存在，可能已被删除。"
        )


# ---------------------------------------------------------------------------
# 业务数据分析：统计逻辑集中在 services/inquiry_history.py，页面只负责呈现。
# ---------------------------------------------------------------------------
ANALYTICS_QUICK_RANGES = ("最近 7 天", "最近 30 天", "最近 90 天", "全部时间", "自定义")
# 选定区间内会补齐没有询盘的日期；跨度太大时改为只显示有记录的时间单位。
TREND_FILL_MAX_DAYS = 92
# 明细表最多渲染的行数，导出文件不受此限制。
RECORDS_TABLE_MAX_ROWS = 500


def format_status_label(status: str) -> str:
    """把数据库中的状态值转换为展示文案，未识别状态如实标注。"""
    if not status:
        return "未设置"
    if status in inquiry_history.FOLLOW_UP_STATUSES:
        return status
    return f"{status}（未识别状态）"


def format_country_label(country: str) -> str:
    """展示识别结果，“未知”明确标注为未识别，避免留下空白造成误解。"""
    return "未识别" if country == "未知" else country


def summarize_inquiry_text(text: str, limit: int = 60) -> str:
    """把邮件正文压缩为单行摘要，便于在明细表中快速浏览。"""
    compact = " ".join((text or "").split())
    return compact if len(compact) <= limit else f"{compact[:limit]}…"


def render_analytics_filters(
) -> tuple[date | None, date | None, str, str | None, str] | None:
    """渲染日期与状态筛选控件；日期非法时提示并返回 None。"""
    today = date.today()
    try:
        earliest_raw, latest_raw = inquiry_history.get_inquiry_date_bounds()
    except inquiry_history.InquiryHistoryError:
        earliest_raw = latest_raw = None
    earliest = date.fromisoformat(earliest_raw) if earliest_raw else None
    latest = date.fromisoformat(latest_raw) if latest_raw else None

    filter_columns = st.columns([1, 1, 2])
    # 默认统计范围来自“设置”页保存的通用偏好；读取失败时回退到最近 30 天。
    try:
        default_range = app_settings.load_settings().default_time_range
    except app_settings.SettingsError:
        default_range = "最近 30 天"
    default_index = (
        ANALYTICS_QUICK_RANGES.index(default_range)
        if default_range in ANALYTICS_QUICK_RANGES
        else 1
    )
    quick_range = filter_columns[0].selectbox(
        "统计范围",
        ANALYTICS_QUICK_RANGES,
        index=default_index,
        key="analytics_quick_range",
        help="快捷范围会同步影响指标、图表、明细表和导出内容。",
    )
    status_label = filter_columns[1].selectbox(
        "跟进状态",
        ("全部", *inquiry_history.FOLLOW_UP_STATUSES),
        index=0,
        key="analytics_status_filter",
        help="与统计范围叠加生效，同样影响全部统计与导出内容。",
    )
    selected_status = None if status_label == "全部" else status_label

    if quick_range == "全部时间":
        label = (
            f"全部时间（{earliest.isoformat()} 至 {latest.isoformat()}）"
            if earliest and latest
            else "全部时间（暂无可统计的记录）"
        )
        return None, None, label, selected_status, status_label

    if quick_range == "最近 7 天":
        start: date = today - timedelta(days=6)
        end: date = today
    elif quick_range == "最近 30 天":
        start = today - timedelta(days=29)
        end = today
    elif quick_range == "最近 90 天":
        start = today - timedelta(days=89)
        end = today
    else:
        default_start = earliest or (today - timedelta(days=29))
        default_end = min(latest or today, today)
        if default_start > default_end:
            default_start = default_end
        start = filter_columns[2].date_input(
            "开始日期",
            value=default_start,
            max_value=today,
            key="analytics_start_date",
        )
        end = filter_columns[2].date_input(
            "结束日期",
            value=default_end,
            max_value=today,
            key="analytics_end_date",
        )
        if start > end:
            st.warning("开始日期不能晚于结束日期，请重新选择日期范围。")
            return None
    return start, end, f"{start.isoformat()} 至 {end.isoformat()}", selected_status, status_label


def build_trend_frame(
    trend: list[tuple[str, int]],
    granularity: str,
    start_date: date | None,
    end_date: date | None,
) -> pd.DataFrame:
    """把趋势数据转换为图表用 DataFrame，并在选定区间内补齐空日期。"""
    frame = pd.DataFrame(
        {"询盘数量（条）": [count for _, count in trend]},
        index=pd.Index([period for period, _ in trend], name=None),
    )
    if not trend or start_date is None or end_date is None:
        return frame
    if (end_date - start_date).days > TREND_FILL_MAX_DAYS:
        return frame
    if granularity == "week":
        first = pd.Timestamp(start_date)
        last = pd.Timestamp(end_date)
        first -= timedelta(days=int(first.weekday()))
        last -= timedelta(days=int(last.weekday()))
        full_index = pd.date_range(first, last, freq="7D")
    else:
        full_index = pd.date_range(start_date, end_date, freq="D")
    filled = frame.reindex(full_index.strftime("%Y-%m-%d"), fill_value=0)
    filled.index.name = None
    return filled


def build_records_frame(
    records: list[inquiry_history.InquiryHistoryRecord],
) -> pd.DataFrame:
    """把询盘记录转换为明细表所需的业务字段，不包含任何密钥或内部配置。"""
    return pd.DataFrame(
        [
            {
                "记录时间": inquiry_history.format_local_timestamp(record.created_at),
                "跟进状态": format_status_label(record.follow_up_status),
                "业务优先级": record.priority,
                "客户国家": format_country_label(record.country),
                "产品": record.customer_needs.get("product", "") or "未提供",
                "AI意向": record.intent_level,
                "下次跟进": record.next_follow_up_date or "未安排",
                "询盘摘要": summarize_inquiry_text(record.original_inquiry),
            }
            for record in records
        ]
    )


def render_business_analytics() -> None:
    """业务数据分析工作台：核心指标、趋势、状态分布、业务维度与洞察。"""
    st.caption(
        "以下指标、图表和洞察全部由本机 SQLite 中保存的真实询盘记录计算得出，"
        "不包含任何模拟或填充数据。"
    )

    resolved_filters = render_analytics_filters()
    if resolved_filters is None:
        return
    start_date, end_date, range_label, selected_status, status_label = resolved_filters
    st.caption(f"当前筛选条件：统计范围 {range_label}｜跟进状态 {status_label}")

    try:
        summary = inquiry_history.get_inquiry_analysis_summary(
            start_date, end_date, status=selected_status
        )
    except inquiry_history.InquiryHistoryError as exc:
        st.error(f"读取业务数据失败：{exc}")
        return

    # 第一行：询盘体量与跟进进度；第二行：产出效率与近期增量。
    volume_row = st.columns(4)
    volume_row[0].metric(
        "询盘总数", summary.total, help="当前筛选范围内的询盘记录总数。"
    )
    volume_row[1].metric(
        "待处理", summary.pending, help="跟进状态为“待跟进”的询盘数量。"
    )
    volume_row[2].metric(
        "处理中", summary.in_progress, help="跟进状态为“跟进中”的询盘数量。"
    )
    volume_row[3].metric(
        "已完成", summary.completed, help="跟进状态为“已完成”的询盘数量。"
    )

    efficiency_row = st.columns(3)
    efficiency_row[0].metric(
        "已生成回复草稿",
        summary.draft_generated,
        help=(
            "仅统计已生成英文回复草稿的询盘数量。数据库没有记录邮件是否已实际发送，"
            "因此该数值不能等同于“已回复客户”。"
        ),
    )
    efficiency_row[1].metric(
        "处理率",
        (
            "暂无数据"
            if summary.completion_rate is None
            else f"{summary.processed / summary.total:.0%}"
        ),
        help="（处理中 + 已完成）÷ 询盘总数。",
    )
    efficiency_row[2].metric(
        f"近 {summary.recent_window_days} 天新增",
        summary.recent_new,
        help=(
            f"筛选范围最后 {summary.recent_window_days} 个自然日内新增的询盘数量。"
        ),
    )
    st.caption(
        "指标口径：待处理 = 状态“待跟进”；处理中 = 状态“跟进中”；已完成 = 状态“已完成”；"
        f"处理率 =（处理中 + 已完成）÷ 询盘总数；近 {summary.recent_window_days} 天新增 = "
        f"统计范围最后 {summary.recent_window_days} 个自然日内的新增记录。"
        "“回复率 / 已回复”暂无数据来源：数据库未记录邮件是否已实际发送，"
        "本页不会用“已生成草稿”冒充“已回复客户”。"
    )

    if summary.total == 0:
        st.info(
            f"当前筛选条件下没有询盘记录（统计范围 {range_label}，跟进状态 {status_label}），"
            "暂无可用于分析的指标。请调整筛选条件，或先在“询盘助手”中完成一次询盘分析。"
        )
        return

    # ---- 询盘数量趋势 ----
    st.subheader("询盘数量趋势")
    granularity_label = st.radio(
        "统计粒度",
        options=("按日", "按周"),
        index=0,
        horizontal=True,
        key="analytics_trend_granularity",
        label_visibility="collapsed",
    )
    granularity = "day" if granularity_label == "按日" else "week"
    try:
        trend = inquiry_history.get_inquiry_trend(
            granularity, start_date, end_date, status=selected_status
        )
    except inquiry_history.InquiryHistoryError as exc:
        st.error(f"读取询盘趋势失败：{exc}")
        return

    if not trend:
        st.info("当前筛选条件下没有询盘记录，无法生成趋势图。")
    else:
        st.bar_chart(
            build_trend_frame(trend, granularity, start_date, end_date),
            x_label="日期" if granularity == "day" else "周（以周一为起始日）",
            y_label="询盘数量（条）",
            height=280,
        )
        st.caption(
            f"当前筛选条件下共 {summary.total} 条询盘记录；"
            + ("按日统计。" if granularity == "day" else "按周统计，以周一为每周第一天。")
            + "没有询盘的日期已补 0，避免趋势断裂。"
        )

    # ---- 跟进状态分布 ----
    st.subheader("跟进状态分布")
    try:
        status_distribution = inquiry_history.get_inquiry_status_distribution(
            start_date, end_date, status=selected_status
        )
    except inquiry_history.InquiryHistoryError as exc:
        st.error(f"读取状态分布失败：{exc}")
        status_distribution = []

    if status_distribution:
        st.bar_chart(
            pd.DataFrame(
                {
                    "询盘数量（条）": [count for _, count in status_distribution],
                },
                index=pd.Index(
                    [
                        format_status_label(status)
                        for status, _ in status_distribution
                    ],
                    name="跟进状态",
                ),
            ),
            x_label="跟进状态",
            y_label="询盘数量（条）",
            height=260,
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "跟进状态": format_status_label(status),
                        "询盘数量": count,
                        "占比": f"{count / summary.total:.0%}",
                    }
                    for status, count in status_distribution
                ]
            ),
            hide_index=True,
        )
        st.caption(
            "占比分母为上方“询盘总数”，与指标卡片口径一致；"
            "不在“待跟进/跟进中/已完成”内的状态会标注为未识别状态并原样保留。"
        )
    else:
        st.info("当前筛选条件下没有可用于统计的状态数据。")

    # ---- 产品需求与客户国家 ----
    st.subheader("产品需求与客户国家")
    try:
        product_distribution, identified_products = (
            inquiry_history.get_inquiry_product_distribution(
                start_date, end_date, status=selected_status
            )
        )
        country_distribution, identified_countries, scoped_total = (
            inquiry_history.get_inquiry_country_distribution(
                start_date, end_date, status=selected_status
            )
        )
        missing_field_stats = inquiry_history.get_inquiry_missing_field_stats(
            start_date, end_date, status=selected_status
        )
    except inquiry_history.InquiryHistoryError as exc:
        st.error(f"读取产品与国家分布失败：{exc}")
        product_distribution, identified_products = [], 0
        country_distribution, identified_countries = [], 0
        scoped_total = summary.total
        missing_field_stats = []

    detail_columns = st.columns(2)
    with detail_columns[0]:
        st.markdown("**产品需求分布**")
        if product_distribution:
            st.bar_chart(
                pd.DataFrame(
                    {
                        "询盘数量（条）": [
                            count for _, count in product_distribution
                        ],
                    },
                    index=pd.Index(
                        [name for name, _ in product_distribution], name="产品"
                    ),
                ),
                x_label="产品",
                y_label="询盘数量（条）",
                height=250,
            )
            st.caption(
                f"产品信息来自分析结果中的需求字段，共 {identified_products} 条记录可识别，"
                "“未提供”不计入分布。"
            )
        else:
            st.info("当前筛选条件下没有可识别的产品需求信息。")

    with detail_columns[1]:
        st.markdown("**客户国家分布**")
        if country_distribution:
            st.bar_chart(
                pd.DataFrame(
                    {
                        "询盘数量（条）": [
                            count for _, count in country_distribution
                        ],
                    },
                    index=pd.Index(
                        [name for name, _ in country_distribution],
                        name="国家/地区",
                    ),
                ),
                x_label="国家/地区",
                y_label="询盘数量（条）",
                height=250,
            )
        st.caption(
            "数据库未单独保存客户国家字段：此处只识别邮件中“based in 某国”"
            "这类明确表述，不依据客户姓名或邮箱后缀推断；"
            f"{scoped_total} 条记录中有 {identified_countries} 条可识别，"
            "其余记为未识别且不参与统计。若需稳定统计，"
            "可在数据库中新增国家字段并在分析时写入。"
        )

    st.markdown("**询盘信息完整度**")
    if missing_field_stats:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "需求项": label,
                        "未提供记录数": count,
                        "占当前范围比例": f"{count / summary.total:.0%}",
                    }
                    for label, count in missing_field_stats
                ]
            ),
            hide_index=True,
        )
        st.caption("“未提供 / 未提及”等占位内容统一按缺失统计。")

    st.caption(
        "数据维度说明：数据库当前保存了记录时间、跟进状态、业务优先级、跟进备注、"
        "下次跟进日期、原始邮件与分析结果。客户名称、询盘来源、客户国家没有对应字段，"
        "因此页面不提供这三项统计，也不会用虚构数据填充图表。"
        "后续如需要，可在保存询盘时新增这些字段并写入数据库。"
    )

    # ---- 业务洞察 ----
    st.subheader("业务洞察")
    try:
        overdue_follow_ups = inquiry_history.count_overdue_follow_ups()
    except inquiry_history.InquiryHistoryError:
        overdue_follow_ups = 0
    for insight in inquiry_history.build_business_insights(
        summary,
        status_distribution=status_distribution,
        product_distribution=product_distribution,
        missing_field_stats=missing_field_stats,
        trend=trend,
        overdue_follow_ups=overdue_follow_ups,
    ):
        st.markdown(f"- {insight}")
    st.caption(
        "以上提示全部由真实记录按固定统计规则生成，可用页面上方的指标逐条核对，"
        "不包含预测、转化率或营销结论。"
    )

    # ---- 询盘数据明细与导出 ----
    st.subheader("询盘数据明细与导出")
    try:
        records = inquiry_history.list_inquiry_records(
            status=selected_status, start_date=start_date, end_date=end_date
        )
    except inquiry_history.InquiryHistoryError as exc:
        st.error(f"读取询盘明细失败：{exc}")
        return

    # 明细表最多展示行数来自“设置”页的通用偏好。
    try:
        table_max_rows = app_settings.load_settings().table_max_rows
    except app_settings.SettingsError:
        table_max_rows = RECORDS_TABLE_MAX_ROWS
    st.dataframe(
        build_records_frame(records[:table_max_rows]), hide_index=True
    )
    if len(records) > table_max_rows:
        st.caption(
            f"表格最多展示 {table_max_rows} 条，"
            f"导出文件包含当前筛选条件下的全部 {len(records)} 条记录。"
        )
    else:
        st.caption(f"共 {len(records)} 条记录，与上方指标口径一致。")
    st.caption(
        "导出内容为当前日期范围与跟进状态筛选下的询盘记录，"
        "包含客户询盘与分析结果等业务字段，不包含任何 API 密钥或配置信息；"
        "生成导出不会写入数据库，也不会调用 AI 接口。"
    )

    file_suffix = (
        f"{start_date.isoformat()}_{end_date.isoformat()}"
        if start_date and end_date
        else "all"
    )
    st.download_button(
        "下载询盘记录 (.csv)",
        data=inquiry_history.build_inquiry_records_csv(records),
        file_name=f"tradepilot_inquiries_{file_suffix}.csv",
        mime="text/csv",
        key="analytics_export_csv",
    )


# ---------------------------------------------------------------------------
# 产品目录 / 客户管理 / 设置 / AI 助手：页面只负责呈现，数据逻辑在 services/ 中。
# ---------------------------------------------------------------------------
# 列表分页大小：按实际数据量提供分页，避免一次渲染过多行。
LIST_PAGE_SIZE = 20
PRODUCT_FORM_KEY_PREFIX = "product_form_"
CUSTOMER_FORM_KEY_PREFIX = "customer_form_"


def index_or_default(options: tuple[str, ...], value: str, default: int = 0) -> int:
    """安全获取下拉框默认下标；数据库中的历史值不在选项内时回退。"""
    return options.index(value) if value in options else default


def clear_widget_state(prefix: str) -> None:
    """清理以 prefix 开头的控件状态，确保新增/编辑表单每次都以真实值初始化。"""
    for key in [item for item in st.session_state if item.startswith(prefix)]:
        del st.session_state[key]


def render_flash_messages(prefix: str) -> None:
    """展示并清除一次性操作反馈，避免重跑后重复提示。"""
    success = st.session_state.pop(f"{prefix}_success", None)
    if success:
        st.success(success)
    warning = st.session_state.pop(f"{prefix}_warning", None)
    if warning:
        st.warning(warning)
    error = st.session_state.pop(f"{prefix}_error", None)
    if error:
        st.error(error)


def render_pagination(total: int, key: str) -> int:
    """渲染简单分页控件，返回当前页码（从 1 开始）。"""
    total_pages = max(1, (total + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = int(st.session_state.get(f"{key}_page", 1))
    page = min(max(page, 1), total_pages)
    if total_pages > 1:
        columns = st.columns([1, 1, 4])
        if columns[0].button("上一页", key=f"{key}_prev", disabled=page <= 1):
            st.session_state[f"{key}_page"] = page - 1
            st.rerun()
        if columns[1].button("下一页", key=f"{key}_next", disabled=page >= total_pages):
            st.session_state[f"{key}_page"] = page + 1
            st.rerun()
        columns[2].caption(f"共 {total} 条，第 {page}/{total_pages} 页。")
    return page


def page_slice(items: list, page: int) -> list:
    start = (page - 1) * LIST_PAGE_SIZE
    return items[start : start + LIST_PAGE_SIZE]


# ---------------------------------------------------------------------------
# 产品目录
# ---------------------------------------------------------------------------


def build_products_frame(products: list[product_catalog.Product]) -> pd.DataFrame:
    """产品列表表格字段，全部来自数据库真实记录。"""
    return pd.DataFrame(
        [
            {
                "产品名称": item.name,
                "SKU": item.sku or "—",
                "分类": item.category or "—",
                "价格": item.price_display,
                "MOQ": item.moq_display,
                "状态": item.status,
                "最近更新": format_timestamp(item.updated_at),
            }
            for item in products
        ]
    )


def render_product_detail(product: product_catalog.Product) -> None:
    """展示单个产品的完整信息；图片仅展示链接本身，不加载外部图片。"""
    rows = [
        ("产品名称", product.name),
        ("SKU / 产品编号", product.sku or "未填写"),
        ("产品分类", product.category or "未填写"),
        ("价格", product.price_display),
        ("货币单位", product.currency),
        ("最小起订量 MOQ", product.moq_display),
        ("产品状态", product.status),
        ("材质", product.material or "未填写"),
        ("尺寸", product.dimensions or "未填写"),
        ("包装", product.packaging or "未填写"),
        ("产品图片链接", product.image_url or "未填写"),
        ("创建时间", format_timestamp(product.created_at)),
        ("最近更新时间", format_timestamp(product.updated_at)),
    ]
    lines = ["| 字段 | 内容 |", "| --- | --- |"]
    for label, value in rows:
        safe_value = str(value).replace("\n", " ").replace("|", "\\|")
        lines.append(f"| **{label}** | {safe_value} |")
    st.markdown("\n".join(lines))
    st.markdown("**产品描述**")
    st.write(product.description or "未填写。")
    st.markdown("**产品规格**")
    st.write(product.specifications or "未填写。")


def render_product_form() -> None:
    """新增或编辑产品表单；校验与写库由 services/product_catalog.py 负责。"""
    edit_id = st.session_state.get("product_edit_id")
    product: product_catalog.Product | None = None
    if edit_id:
        try:
            product = product_catalog.get_product(edit_id)
        except product_catalog.ProductError as exc:
            st.error(f"读取产品失败：{exc}")
            return
        if product is None:
            st.session_state["product_mode"] = "list"
            st.session_state.pop("product_edit_id", None)
            st.session_state["product_warning"] = "要编辑的产品不存在，可能已被删除。"
            st.rerun()

    is_edit = product is not None
    st.subheader("编辑产品" if is_edit else "新增产品")
    st.caption("仅产品名称为必填项，其它字段可留空；SKU 一旦填写就必须唯一。")

    with st.form("product_form"):
        name = st.text_input(
            "产品名称 *",
            value=product.name if product else "",
            key=f"{PRODUCT_FORM_KEY_PREFIX}name",
        )
        column_pair = st.columns(2)
        sku = column_pair[0].text_input(
            "SKU / 产品编号（可选，需唯一）",
            value=product.sku if product else "",
            key=f"{PRODUCT_FORM_KEY_PREFIX}sku",
        )
        category = column_pair[1].text_input(
            "产品分类",
            value=product.category if product else "",
            key=f"{PRODUCT_FORM_KEY_PREFIX}category",
        )
        price_column = st.columns(3)
        price = price_column[0].text_input(
            "价格",
            value=(
                ""
                if product is None or product.price is None
                else f"{product.price:g}"
            ),
            key=f"{PRODUCT_FORM_KEY_PREFIX}price",
            help="可留空；只能填写非负数字，例如 180 或 180.50。",
        )
        currency = price_column[1].selectbox(
            "货币单位",
            product_catalog.CURRENCIES,
            index=index_or_default(
                product_catalog.CURRENCIES,
                product.currency if product else product_catalog.DEFAULT_CURRENCY,
            ),
            key=f"{PRODUCT_FORM_KEY_PREFIX}currency",
        )
        moq = price_column[2].text_input(
            "最小起订量 MOQ",
            value=(
                "" if product is None or product.moq is None else str(product.moq)
            ),
            key=f"{PRODUCT_FORM_KEY_PREFIX}moq",
            help="可留空；只能填写非负整数，例如 50。",
        )
        status = st.selectbox(
            "产品状态",
            product_catalog.PRODUCT_STATUSES,
            index=index_or_default(
                product_catalog.PRODUCT_STATUSES,
                product.status if product else product_catalog.DEFAULT_STATUS,
            ),
            key=f"{PRODUCT_FORM_KEY_PREFIX}status",
        )
        description = st.text_area(
            "产品描述",
            value=product.description if product else "",
            height=100,
            key=f"{PRODUCT_FORM_KEY_PREFIX}description",
        )
        specifications = st.text_area(
            "产品规格",
            value=product.specifications if product else "",
            height=80,
            key=f"{PRODUCT_FORM_KEY_PREFIX}specifications",
        )
        detail_columns = st.columns(2)
        material = detail_columns[0].text_input(
            "材质",
            value=product.material if product else "",
            key=f"{PRODUCT_FORM_KEY_PREFIX}material",
        )
        dimensions = detail_columns[1].text_input(
            "尺寸",
            value=product.dimensions if product else "",
            key=f"{PRODUCT_FORM_KEY_PREFIX}dimensions",
        )
        packaging = st.text_input(
            "包装",
            value=product.packaging if product else "",
            key=f"{PRODUCT_FORM_KEY_PREFIX}packaging",
        )
        image_url = st.text_input(
            "产品图片链接（可选）",
            value=product.image_url if product else "",
            key=f"{PRODUCT_FORM_KEY_PREFIX}image_url",
            help="本项目不含图片存储功能，此处只保存链接文本，不会加载外部图片。",
        )
        submitted = st.form_submit_button("保存产品", type="primary")

    if submitted:
        payload = {
            "name": name,
            "sku": sku,
            "category": category,
            "description": description,
            "price": price,
            "currency": currency,
            "moq": moq,
            "status": status,
            "specifications": specifications,
            "material": material,
            "dimensions": dimensions,
            "packaging": packaging,
            "image_url": image_url,
        }
        try:
            if is_edit and product is not None:
                updated = product_catalog.update_product(product.product_id, **payload)
                if not updated:
                    raise product_catalog.ProductError("该产品不存在，可能已被删除。")
            else:
                product_catalog.create_product(**payload)
        except product_catalog.ProductError as exc:
            st.error(str(exc))
        else:
            st.session_state["product_success"] = (
                "产品已更新。" if is_edit else "产品已创建。"
            )
            st.session_state["product_mode"] = "list"
            st.session_state.pop("product_edit_id", None)
            st.rerun()

    if st.button("返回产品列表", key=f"{PRODUCT_FORM_KEY_PREFIX}cancel"):
        st.session_state["product_mode"] = "list"
        st.session_state.pop("product_edit_id", None)
        st.rerun()


def render_product_catalog() -> None:
    """产品目录页面：列表、搜索筛选、新增、编辑、详情与删除。"""
    st.caption(
        "产品资料保存在本机 SQLite 数据库。AI 助手回答价格、规格和 MOQ 等问题时，"
        "会优先引用这里的真实记录，数据库中没有的产品不会被编造。"
    )
    render_flash_messages("product")

    try:
        stats = product_catalog.get_product_stats()
    except product_catalog.ProductError as exc:
        st.error(f"读取产品统计失败：{exc}")
        stats = {"total": 0, "active": 0, "discontinued": 0, "draft": 0}

    metric_columns = st.columns(4)
    metric_columns[0].metric("产品总数", stats["total"])
    metric_columns[1].metric("在售", stats["active"])
    metric_columns[2].metric("停售", stats["discontinued"])
    metric_columns[3].metric("草稿", stats["draft"])

    if st.session_state.get("product_mode") == "form":
        st.divider()
        render_product_form()
        return

    st.divider()
    filter_columns = st.columns([2, 1, 1, 1])
    keyword = filter_columns[0].text_input(
        "关键词搜索",
        key="product_keyword",
        help="搜索产品名称、SKU、分类和产品描述。",
    )
    try:
        categories = product_catalog.list_categories()
    except product_catalog.ProductError as exc:
        st.error(f"读取产品分类失败：{exc}")
        categories = []
    category_label = filter_columns[1].selectbox(
        "产品分类",
        ("全部", *categories),
        key="product_category_filter",
    )
    status_label = filter_columns[2].selectbox(
        "产品状态",
        ("全部", *product_catalog.PRODUCT_STATUSES),
        key="product_status_filter",
    )
    with filter_columns[3]:
        st.write("")
        if st.button("＋ 新增产品", key="product_new_button"):
            clear_widget_state(PRODUCT_FORM_KEY_PREFIX)
            st.session_state.pop("product_edit_id", None)
            st.session_state["product_mode"] = "form"
            st.rerun()

    try:
        products = product_catalog.list_products(
            keyword=keyword,
            category=None if category_label == "全部" else category_label,
            status=None if status_label == "全部" else status_label,
        )
    except product_catalog.ProductError as exc:
        st.error(f"读取产品目录失败：{exc}")
        return

    if stats["total"] == 0:
        st.info(
            "产品目录还是空的。点击上方“＋ 新增产品”建立第一条产品记录，"
            "AI 助手之后就能引用真实产品资料。"
        )
        return
    if not products:
        st.info("没有符合当前关键词和筛选条件的产品，请调整筛选条件。")
        return

    page = render_pagination(len(products), "product_list")
    page_products = page_slice(products, page)
    st.dataframe(build_products_frame(page_products), hide_index=True)

    st.divider()
    st.subheader("产品详情")
    labels = [item.display_label for item in page_products]
    selected_label = st.selectbox(
        "选择产品查看详情", labels, key="product_detail_select"
    )
    selected = page_products[labels.index(selected_label)]
    render_product_detail(selected)

    action_columns = st.columns([1, 1, 3])
    if action_columns[0].button("编辑此产品", key="product_edit_button"):
        clear_widget_state(PRODUCT_FORM_KEY_PREFIX)
        st.session_state["product_edit_id"] = selected.product_id
        st.session_state["product_mode"] = "form"
        st.rerun()

    confirmed = st.checkbox(
        "我确认删除该产品（删除后无法恢复）",
        key=f"product_confirm_delete_{selected.product_id}",
    )
    if action_columns[1].button(
        "删除此产品",
        key="product_delete_button",
        disabled=not confirmed,
        type="secondary",
    ):
        try:
            deleted = product_catalog.delete_product(selected.product_id)
        except product_catalog.ProductError as exc:
            st.session_state["product_error"] = f"删除产品失败：{exc}"
        else:
            if deleted:
                st.session_state["product_success"] = "产品已删除。"
            else:
                st.session_state["product_warning"] = (
                    "该产品可能已被删除，请刷新产品列表。"
                )
        st.rerun()


# ---------------------------------------------------------------------------
# 客户管理
# ---------------------------------------------------------------------------


def build_customers_frame(customers: list[customer_manager.Customer]) -> pd.DataFrame:
    """客户列表表格字段，全部来自数据库真实记录。"""
    return pd.DataFrame(
        [
            {
                "公司名称": item.company_name,
                "联系人": item.contact_name or "—",
                "国家/地区": item.country or "—",
                "邮箱": item.email or "—",
                "电话": item.phone or "—",
                "来源": item.source or "—",
                "等级": item.level,
                "状态": item.status,
                "最近联系": item.last_contact_date or "未记录",
                "创建时间": format_timestamp(item.created_at),
            }
            for item in customers
        ]
    )


def build_inquiries_frame(records: list[inquiry_history.InquiryHistoryRecord]) -> pd.DataFrame:
    """客户关联询盘的简要表格。"""
    return pd.DataFrame(
        [
            {
                "记录时间": format_timestamp(record.created_at),
                "跟进状态": format_status_label(record.follow_up_status),
                "业务优先级": record.priority,
                "产品": record.customer_needs.get("product", "") or "未提供",
                "询盘摘要": summarize_inquiry_text(record.original_inquiry),
            }
            for record in records
        ]
    )


def render_customer_detail(customer: customer_manager.Customer) -> None:
    """客户详情：基本资料、备注、状态等级与关联询盘。"""
    rows = [
        ("公司名称", customer.company_name),
        ("联系人", customer.contact_name or "未填写"),
        ("邮箱", customer.email or "未填写"),
        ("电话", customer.phone or "未填写"),
        ("国家/地区", customer.country or "未填写"),
        ("公司网站", customer.website or "未填写"),
        ("客户来源", customer.source or "未填写"),
        ("客户等级", customer.level),
        ("客户状态", customer.status),
        ("最近联系", customer.last_contact_date or "未记录"),
        ("创建时间", format_timestamp(customer.created_at)),
        ("最近更新", format_timestamp(customer.updated_at)),
        ("客户记录状态", "已归档" if customer.archived else "正常"),
    ]
    lines = ["| 字段 | 内容 |", "| --- | --- |"]
    for label, value in rows:
        safe_value = str(value).replace("\n", " ").replace("|", "\\|")
        lines.append(f"| **{label}** | {safe_value} |")
    st.markdown("\n".join(lines))
    st.markdown("**客户备注**")
    st.write(customer.notes or "未填写。")

    st.subheader("关联询盘")
    try:
        linked = inquiry_history.list_inquiry_records(
            customer_id=customer.customer_id
        )
    except inquiry_history.InquiryHistoryError as exc:
        st.error(f"读取关联询盘失败：{exc}")
        linked = []

    st.caption(f"该客户当前关联 {len(linked)} 条询盘记录。")
    if linked:
        st.dataframe(build_inquiries_frame(linked), hide_index=True)
        for record in linked:
            title = (
                f"{format_timestamp(record.created_at)} · "
                f"{record.customer_needs.get('product', '') or '未提供'} · "
                f"{record.follow_up_status}"
            )
            with st.expander(title):
                st.markdown("**原始英文询盘**")
                st.write(record.original_inquiry)
                render_analysis_details(
                    record, key_prefix=f"customer_{customer.customer_id}_{record.record_id}"
                )
                if st.button(
                    "解除与该客户的关联",
                    key=f"unlink_{customer.customer_id}_{record.record_id}",
                ):
                    try:
                        inquiry_history.link_inquiry_to_customer(
                            record.record_id, None
                        )
                    except inquiry_history.InquiryHistoryError as exc:
                        st.session_state["customer_error"] = f"解除关联失败：{exc}"
                    else:
                        st.session_state["customer_success"] = "已解除询盘与客户的关联。"
                    st.rerun()
    else:
        st.info("该客户暂无关联询盘。可在下方把已有询盘关联到该客户。")

    st.markdown("**关联已有询盘**")
    try:
        all_records = inquiry_history.list_inquiry_records()
    except inquiry_history.InquiryHistoryError as exc:
        st.error(f"读取询盘列表失败：{exc}")
        return
    candidates = [
        record
        for record in all_records
        if record.customer_id != customer.customer_id
    ]
    if not candidates:
        st.caption("当前没有可关联的询盘记录。")
        return
    option_ids = [record.record_id for record in candidates]
    label_by_id = {
        record.record_id: (
            f"{format_timestamp(record.created_at)} · "
            f"{summarize_inquiry_text(record.original_inquiry, 30)}"
            f"（{'未关联' if not record.customer_id else '已关联其他客户'}）"
        )
        for record in candidates
    }
    chosen_id = st.selectbox(
        "选择要关联的询盘",
        option_ids,
        format_func=lambda item: label_by_id[item],
        key=f"customer_link_select_{customer.customer_id}",
    )
    if st.button("关联到该客户", key=f"customer_link_button_{customer.customer_id}"):
        try:
            inquiry_history.link_inquiry_to_customer(chosen_id, customer.customer_id)
        except inquiry_history.InquiryHistoryError as exc:
            st.session_state["customer_error"] = f"关联询盘失败：{exc}"
        else:
            st.session_state["customer_success"] = "已把询盘关联到该客户。"
        st.rerun()


def render_customer_form() -> None:
    """新增或编辑客户表单。"""
    edit_id = st.session_state.get("customer_edit_id")
    customer: customer_manager.Customer | None = None
    if edit_id:
        try:
            customer = customer_manager.get_customer(edit_id)
        except customer_manager.CustomerError as exc:
            st.error(f"读取客户失败：{exc}")
            return
        if customer is None:
            st.session_state["customer_mode"] = "list"
            st.session_state.pop("customer_edit_id", None)
            st.session_state["customer_warning"] = "要编辑的客户不存在，可能已被删除。"
            st.rerun()

    is_edit = customer is not None
    st.subheader("编辑客户" if is_edit else "新增客户")
    st.caption("公司名称为必填项；邮箱可以留空，但填写时必须符合基本邮箱格式。")

    with st.form("customer_form"):
        company_name = st.text_input(
            "公司名称 *",
            value=customer.company_name if customer else "",
            key=f"{CUSTOMER_FORM_KEY_PREFIX}company_name",
        )
        contact_columns = st.columns(2)
        contact_name = contact_columns[0].text_input(
            "联系人姓名",
            value=customer.contact_name if customer else "",
            key=f"{CUSTOMER_FORM_KEY_PREFIX}contact_name",
        )
        country = contact_columns[1].text_input(
            "国家或地区",
            value=customer.country if customer else "",
            key=f"{CUSTOMER_FORM_KEY_PREFIX}country",
        )
        email_columns = st.columns(2)
        email = email_columns[0].text_input(
            "联系邮箱",
            value=customer.email if customer else "",
            key=f"{CUSTOMER_FORM_KEY_PREFIX}email",
        )
        phone = email_columns[1].text_input(
            "电话",
            value=customer.phone if customer else "",
            key=f"{CUSTOMER_FORM_KEY_PREFIX}phone",
        )
        website = st.text_input(
            "公司网站",
            value=customer.website if customer else "",
            key=f"{CUSTOMER_FORM_KEY_PREFIX}website",
        )
        selection_columns = st.columns(3)
        source = selection_columns[0].selectbox(
            "客户来源",
            ("", *customer_manager.CUSTOMER_SOURCES),
            index=index_or_default(
                ("", *customer_manager.CUSTOMER_SOURCES),
                customer.source if customer else "",
            ),
            format_func=lambda item: item or "未填写",
            key=f"{CUSTOMER_FORM_KEY_PREFIX}source",
        )
        level = selection_columns[1].selectbox(
            "客户等级",
            customer_manager.CUSTOMER_LEVELS,
            index=index_or_default(
                customer_manager.CUSTOMER_LEVELS,
                customer.level if customer else customer_manager.DEFAULT_LEVEL,
            ),
            key=f"{CUSTOMER_FORM_KEY_PREFIX}level",
        )
        status = selection_columns[2].selectbox(
            "客户状态",
            customer_manager.CUSTOMER_STATUSES,
            index=index_or_default(
                customer_manager.CUSTOMER_STATUSES,
                customer.status if customer else customer_manager.DEFAULT_STATUS,
            ),
            key=f"{CUSTOMER_FORM_KEY_PREFIX}status",
        )
        last_contact = st.date_input(
            "最近联系时间（没有联系记录可留空）",
            value=(
                date.fromisoformat(customer.last_contact_date)
                if customer and customer.last_contact_date
                else None
            ),
            key=f"{CUSTOMER_FORM_KEY_PREFIX}last_contact",
        )
        notes = st.text_area(
            "客户备注",
            value=customer.notes if customer else "",
            height=100,
            key=f"{CUSTOMER_FORM_KEY_PREFIX}notes",
        )
        submitted = st.form_submit_button("保存客户", type="primary")

    if submitted:
        payload = {
            "company_name": company_name,
            "contact_name": contact_name,
            "email": email,
            "phone": phone,
            "country": country,
            "website": website,
            "source": source,
            "level": level,
            "status": status,
            "notes": notes,
            "last_contact_date": last_contact,
        }
        try:
            if is_edit and customer is not None:
                updated = customer_manager.update_customer(customer.customer_id, **payload)
                if not updated:
                    raise customer_manager.CustomerError("该客户不存在，可能已被删除。")
            else:
                customer_manager.create_customer(**payload)
        except customer_manager.CustomerError as exc:
            st.error(str(exc))
        else:
            st.session_state["customer_success"] = (
                "客户资料已更新。" if is_edit else "客户已创建。"
            )
            st.session_state["customer_mode"] = "list"
            st.session_state.pop("customer_edit_id", None)
            st.rerun()

    if st.button("返回客户列表", key=f"{CUSTOMER_FORM_KEY_PREFIX}cancel"):
        st.session_state["customer_mode"] = "list"
        st.session_state.pop("customer_edit_id", None)
        st.rerun()


def render_customer_management() -> None:
    """客户管理页面：列表、搜索筛选、新增、编辑、详情、关联询盘与删除。"""
    st.caption(
        "客户资料保存在本机 SQLite 数据库，可把历史询盘关联到客户，"
        "避免重复录入客户信息。删除有关联询盘的客户时会自动改为归档，历史询盘不受影响。"
    )
    render_flash_messages("customer")

    try:
        stats = customer_manager.get_customer_stats()
    except customer_manager.CustomerError as exc:
        st.error(f"读取客户统计失败：{exc}")
        stats = {"total": 0, "archived": 0, "key_accounts": 0, "won": 0}

    metric_columns = st.columns(4)
    metric_columns[0].metric("客户总数", stats["total"])
    metric_columns[1].metric("重点客户", stats["key_accounts"])
    metric_columns[2].metric("已成交", stats["won"])
    metric_columns[3].metric("已归档", stats["archived"])

    if st.session_state.get("customer_mode") == "form":
        st.divider()
        render_customer_form()
        return

    st.divider()
    filter_columns = st.columns([2, 1, 1, 1])
    keyword = filter_columns[0].text_input(
        "关键词搜索",
        key="customer_keyword",
        help="搜索公司名称、联系人、邮箱和电话。",
    )
    try:
        countries = customer_manager.list_countries()
    except customer_manager.CustomerError as exc:
        st.error(f"读取客户国家或地区失败：{exc}")
        countries = []
    country_label = filter_columns[1].selectbox(
        "国家/地区", ("全部", *countries), key="customer_country_filter"
    )
    level_label = filter_columns[2].selectbox(
        "客户等级", ("全部", *customer_manager.CUSTOMER_LEVELS), key="customer_level_filter"
    )
    status_label = filter_columns[3].selectbox(
        "客户状态", ("全部", *customer_manager.CUSTOMER_STATUSES), key="customer_status_filter"
    )

    option_columns = st.columns([1, 4])
    include_archived = option_columns[0].checkbox(
        "显示已归档客户", key="customer_include_archived"
    )
    with option_columns[1]:
        st.write("")
        if st.button("＋ 新增客户", key="customer_new_button"):
            clear_widget_state(CUSTOMER_FORM_KEY_PREFIX)
            st.session_state.pop("customer_edit_id", None)
            st.session_state["customer_mode"] = "form"
            st.rerun()

    try:
        customers = customer_manager.list_customers(
            keyword=keyword,
            country=None if country_label == "全部" else country_label,
            level=None if level_label == "全部" else level_label,
            status=None if status_label == "全部" else status_label,
            include_archived=include_archived,
        )
    except customer_manager.CustomerError as exc:
        st.error(f"读取客户列表失败：{exc}")
        return

    if stats["total"] == 0:
        st.info(
            "还没有客户记录。点击上方“＋ 新增客户”建立第一条客户资料，"
            "之后可以在客户详情中关联历史询盘。"
        )
        return
    if not customers:
        st.info("没有符合当前关键词和筛选条件的客户，请调整筛选条件。")
        return

    page = render_pagination(len(customers), "customer_list")
    page_customers = page_slice(customers, page)
    st.dataframe(build_customers_frame(page_customers), hide_index=True)

    st.divider()
    st.subheader("客户详情")
    labels = [item.display_label for item in page_customers]
    selected_label = st.selectbox(
        "选择客户查看详情", labels, key="customer_detail_select"
    )
    selected = page_customers[labels.index(selected_label)]
    render_customer_detail(selected)

    action_columns = st.columns([1, 1, 1, 2])
    if action_columns[0].button("编辑此客户", key="customer_edit_button"):
        clear_widget_state(CUSTOMER_FORM_KEY_PREFIX)
        st.session_state["customer_edit_id"] = selected.customer_id
        st.session_state["customer_mode"] = "form"
        st.rerun()

    if selected.archived:
        if action_columns[1].button("恢复此客户", key="customer_restore_button"):
            try:
                customer_manager.restore_customer(selected.customer_id)
            except customer_manager.CustomerError as exc:
                st.session_state["customer_error"] = f"恢复客户失败：{exc}"
            else:
                st.session_state["customer_success"] = "客户已恢复到正常列表。"
            st.rerun()
    else:
        if action_columns[1].button("归档此客户", key="customer_archive_button"):
            try:
                customer_manager.archive_customer(selected.customer_id)
            except customer_manager.CustomerError as exc:
                st.session_state["customer_error"] = f"归档客户失败：{exc}"
            else:
                st.session_state["customer_success"] = (
                    "客户已归档，可在“显示已归档客户”中查看。"
                )
            st.rerun()

    confirmed = st.checkbox(
        "我确认删除该客户（如有关联询盘将改为归档，不会删除历史询盘）",
        key=f"customer_confirm_delete_{selected.customer_id}",
    )
    if action_columns[2].button(
        "删除此客户",
        key="customer_delete_button",
        disabled=not confirmed,
        type="secondary",
    ):
        try:
            result = customer_manager.delete_customer(selected.customer_id)
        except customer_manager.CustomerError as exc:
            st.session_state["customer_error"] = f"删除客户失败：{exc}"
        else:
            if result == "archived":
                st.session_state["customer_warning"] = (
                    "该客户存在关联询盘，已改为归档；历史询盘记录保持完整。"
                )
            elif result == "deleted":
                st.session_state["customer_success"] = "客户已删除。"
            else:
                st.session_state["customer_warning"] = (
                    "该客户可能已被删除，请刷新客户列表。"
                )
        st.rerun()


# ---------------------------------------------------------------------------
# 系统设置
# ---------------------------------------------------------------------------


def render_settings() -> None:
    """系统设置：AI 模型信息、连接测试、回复偏好、通用偏好与系统信息。"""
    render_flash_messages("settings")

    st.subheader("AI 模型设置")
    try:
        connection_settings = deepseek_client.load_settings()
    except deepseek_client.DeepSeekConfigurationError as exc:
        connection_settings = None
        st.warning(f"{exc}")
    else:
        st.dataframe(
            pd.DataFrame(
                [
                    {"配置项": "模型名称", "当前值": connection_settings.model},
                    {"配置项": "API 服务地址", "当前值": connection_settings.base_url},
                    {"配置项": "API Key 状态", "当前值": "已配置（不显示内容）"},
                ]
            ),
            hide_index=True,
        )
    st.caption(
        "API Key 只从项目根目录的 .env 或 Streamlit Secrets 读取，本页面不回显、"
        "不能修改，也不会写入数据库、日志或导出文件。如需更换密钥，请编辑 .env 后重启应用。"
    )
    if st.button("测试 API 连接", key="settings_test_connection"):
        with st.spinner("正在向 DeepSeek 发送一次最小测试请求……"):
            result = deepseek_client.test_connection()
        st.session_state["settings_connection_result"] = (
            result.connected,
            result.message,
        )
    connection_result = st.session_state.get("settings_connection_result")
    if connection_result:
        connected, message = connection_result
        if connected:
            st.success(message)
        else:
            st.error(f"连接测试未通过：{message}")
        st.caption("连接测试只在点击按钮时执行，应用启动时不会自动请求 API。")

    st.divider()
    st.subheader("AI 回复偏好")
    st.caption("以下偏好会作为提示词约束，实际影响 AI 助手的回复生成结果。")
    try:
        current = app_settings.load_settings()
    except app_settings.SettingsError as exc:
        st.error(f"读取设置失败：{exc}")
        return

    with st.form("reply_preferences_form"):
        reply_columns = st.columns(3)
        reply_language = reply_columns[0].selectbox(
            app_settings.SETTING_LABELS["reply_language"],
            app_settings.REPLY_LANGUAGES,
            index=index_or_default(
                app_settings.REPLY_LANGUAGES, current.reply_language
            ),
            key="settings_reply_language",
        )
        reply_tone = reply_columns[1].selectbox(
            app_settings.SETTING_LABELS["reply_tone"],
            app_settings.REPLY_TONES,
            index=index_or_default(app_settings.REPLY_TONES, current.reply_tone),
            key="settings_reply_tone",
        )
        reply_length = reply_columns[2].selectbox(
            app_settings.SETTING_LABELS["reply_length"],
            app_settings.REPLY_LENGTHS,
            index=index_or_default(app_settings.REPLY_LENGTHS, current.reply_length),
            key="settings_reply_length",
        )
        include_chinese = st.checkbox(
            app_settings.SETTING_LABELS["include_chinese_explanation"],
            value=current.include_chinese_explanation,
            key="settings_include_chinese",
        )
        show_notes = st.checkbox(
            app_settings.SETTING_LABELS["show_usage_notes"],
            value=current.show_usage_notes,
            key="settings_show_usage_notes",
        )
        save_reply = st.form_submit_button("保存回复偏好", type="primary")

    if save_reply:
        try:
            app_settings.save_settings(
                {
                    "reply_language": reply_language,
                    "reply_tone": reply_tone,
                    "reply_length": reply_length,
                    "include_chinese_explanation": include_chinese,
                    "show_usage_notes": show_notes,
                }
            )
        except app_settings.SettingsError as exc:
            st.error(f"保存回复偏好失败：{exc}")
        else:
            st.session_state["settings_success"] = (
                "回复偏好已保存，将在 AI 助手下一次生成回复时生效。"
            )
            st.rerun()

    st.divider()
    st.subheader("通用偏好")
    st.caption(
        "界面语言：当前版本仅提供中文界面，因此不提供语言切换选项。"
    )
    with st.form("general_preferences_form"):
        general_columns = st.columns(2)
        default_time_range = general_columns[0].selectbox(
            app_settings.SETTING_LABELS["default_time_range"],
            app_settings.DEFAULT_TIME_RANGES,
            index=index_or_default(
                app_settings.DEFAULT_TIME_RANGES, current.default_time_range
            ),
            key="settings_default_time_range",
            help="业务概览页首次打开时使用的默认统计范围。",
        )
        table_max_rows = general_columns[1].selectbox(
            app_settings.SETTING_LABELS["table_max_rows"],
            app_settings.TABLE_MAX_ROWS_OPTIONS,
            index=index_or_default(
                app_settings.TABLE_MAX_ROWS_OPTIONS, str(current.table_max_rows)
            ),
            key="settings_table_max_rows",
            help="业务概览页明细表最多显示的行数，导出文件不受此限制。",
        )
        save_general = st.form_submit_button("保存通用偏好", type="primary")

    if save_general:
        try:
            app_settings.save_settings(
                {
                    "default_time_range": default_time_range,
                    "table_max_rows": table_max_rows,
                }
            )
        except app_settings.SettingsError as exc:
            st.error(f"保存通用偏好失败：{exc}")
        else:
            st.session_state["settings_success"] = (
                "通用偏好已保存，业务概览页将按新的默认范围显示。"
            )
            st.rerun()

    reset_confirmed = st.checkbox(
        "我确认恢复默认设置（会清空已保存的偏好，不影响业务数据）",
        key="settings_confirm_reset",
    )
    if st.button(
        "恢复默认设置",
        key="settings_reset_button",
        disabled=not reset_confirmed,
        type="secondary",
    ):
        try:
            app_settings.reset_settings()
        except app_settings.SettingsError as exc:
            st.error(f"恢复默认设置失败：{exc}")
        else:
            st.session_state["settings_success"] = "已恢复默认设置。"
            st.rerun()

    st.divider()
    st.subheader("系统信息")
    environment_columns = st.columns(2)
    with environment_columns[0]:
        st.markdown(
            "\n".join(
                [
                    f"- **应用名称**：TradePilot AI",
                    f"- **应用版本**：{APP_VERSION}",
                    f"- **运行环境 Python**：{sys.version.split()[0]}",
                    f"- **Streamlit 版本**：{st.__version__}",
                    (
                        f"- **当前模型**：{connection_settings.model}"
                        if connection_settings
                        else "- **当前模型**：未配置"
                    ),
                    (
                        f"- **API 服务地址**：{connection_settings.base_url}"
                        if connection_settings
                        else "- **API 服务地址**：未配置"
                    ),
                ]
            )
        )
    with environment_columns[1]:
        try:
            database.table_exists("inquiry_history")
        except database.DatabaseError as exc:
            st.error(f"数据库连接检查失败：{exc}")
        else:
            st.success("数据库连接正常：可以读写本机 SQLite 数据库。")
        table_rows = []
        for table in database.KNOWN_TABLES:
            try:
                exists = database.table_exists(table)
            except database.DatabaseError:
                exists = False
            if not exists:
                table_rows.append({"数据表": table, "状态": "尚未创建", "记录数": "—"})
                continue
            try:
                count = database.count_table_rows(table)
            except database.DatabaseError:
                count = None
            table_rows.append(
                {
                    "数据表": table,
                    "状态": "正常",
                    "记录数": "—" if count is None else count,
                }
            )
        st.dataframe(pd.DataFrame(table_rows), hide_index=True)
    st.caption(
        "当前版本为单机原型，没有用户登录与权限系统，所有数据保存在本机 SQLite，"
        "不涉及多用户隔离。本页面不展示任何密钥、密码或系统敏感路径。"
    )


# ---------------------------------------------------------------------------
# AI 助手
# ---------------------------------------------------------------------------
AI_ASSISTANT_QUICK_ACTIONS: tuple[tuple[str, str], ...] = (
    (
        "分析客户询盘",
        "请分析下面这条客户询盘：提取客户主要需求、产品、规格、数量、目的地和关注点，"
        "列出仍需确认的信息，并给出下一步跟进建议。原文中没有依据的信息请标记为“待确认”，不要编造。\n\n",
    ),
    (
        "生成英文商务回复",
        "请根据下面这条客户询盘起草一封英文商务回复，只生成草稿，不要声称已经发送给客户。\n\n",
    ),
    (
        "介绍某个产品",
        "请根据系统中的真实产品资料介绍这个产品，说明价格、MOQ 和规格。"
        "如果数据库中没有该产品记录，请直接说明未找到匹配记录，不要编造价格或规格。产品名称：",
    ),
    (
        "整理客户跟进建议",
        "请根据系统中该客户的真实资料和关联询盘，整理下一步跟进建议；"
        "资料缺失的部分请明确说明，不要编造客户历史或购买意向。客户：",
    ),
    (
        "优化英文商务邮件",
        "请帮我优化下面这封英文商务邮件，使其更符合外贸商务习惯，并说明主要修改点。\n\n",
    ),
)


def apply_pending_assistant_input() -> None:
    """在渲染输入框之前应用待写入内容，避免直接修改已实例化的控件状态。"""
    pending = st.session_state.pop("ai_assistant_pending_input", None)
    if pending is not None:
        st.session_state["ai_assistant_input"] = pending


def render_ai_assistant() -> None:
    """AI 助手页面：多轮对话，并按需注入真实产品、客户与询盘数据。"""
    apply_pending_assistant_input()
    if st.session_state.pop("ai_assistant_clear_requested", False):
        st.session_state["ai_assistant_messages"] = []
        st.session_state.pop("ai_assistant_context_note", None)
        st.session_state.pop("ai_assistant_error", None)
        st.session_state["ai_assistant_pending_input"] = ""
        st.session_state["ai_assistant_clear_confirm"] = False
    st.caption(
        "AI 助手通过现有 DeepSeek 配置工作。发送消息时，只有与你问题相关的"
        "产品、客户与询盘记录会作为上下文发送给模型；数据库中查不到的内容不会被编造。"
    )

    try:
        deepseek_client.load_settings()
    except deepseek_client.DeepSeekConfigurationError as exc:
        st.warning(f"{exc} 配置完成后才能使用 AI 助手。")

    st.markdown("**常见任务（点击后填入输入框，可直接补充内容再发送）**")
    action_columns = st.columns(len(AI_ASSISTANT_QUICK_ACTIONS))
    for column, (label, template) in zip(action_columns, AI_ASSISTANT_QUICK_ACTIONS):
        if column.button(label, key=f"ai_assistant_action_{label}"):
            st.session_state["ai_assistant_pending_input"] = template
            st.rerun()

    st.divider()
    st.markdown("**引用真实业务数据（可选）**")
    context_columns = st.columns(2)
    try:
        customers = customer_manager.list_customers()
    except customer_manager.CustomerError as exc:
        st.error(f"读取 AI 助手客户选择列表失败：{exc}")
        customers = []
    customer_options = [""] + [item.customer_id for item in customers]
    customer_labels = {item.customer_id: item.display_label for item in customers}
    selected_customer_id = context_columns[0].selectbox(
        "关联客户",
        customer_options,
        format_func=lambda item: "不关联客户" if not item else customer_labels.get(item, item),
        key="ai_assistant_customer",
        help="关联后，该客户的真实资料会作为上下文提供给模型。",
    )
    try:
        all_records = inquiry_history.list_inquiry_records()
    except inquiry_history.InquiryHistoryError as exc:
        st.error(f"读取 AI 助手询盘选择列表失败：{exc}")
        all_records = []
    record_options = [""] + [record.record_id for record in all_records]
    record_labels = {
        record.record_id: (
            f"{format_timestamp(record.created_at)} · "
            f"{summarize_inquiry_text(record.original_inquiry, 30)}"
        )
        for record in all_records
    }
    selected_record_id = context_columns[1].selectbox(
        "关联询盘",
        record_options,
        format_func=lambda item: "不关联询盘" if not item else record_labels.get(item, item),
        key="ai_assistant_inquiry",
        help="关联后，该询盘的原始邮件会作为上下文提供给模型。",
    )

    st.divider()
    messages: list[dict[str, str]] = st.session_state.setdefault(
        "ai_assistant_messages", []
    )
    if not messages:
        st.info(
            "这是可连续对话的外贸工作助手。你可以粘贴客户询盘、询问产品价格与 MOQ，"
            "或让助手整理客户跟进建议。所有答复仅供参考，请人工复核后再对客户使用。"
        )
    for message in messages:
        role = "user" if message.get("role") == "user" else "assistant"
        with st.chat_message(role):
            st.markdown(message.get("content", ""))
            if role == "assistant":
                with st.expander("复制此回复"):
                    st.code(message.get("content", ""), language=None)

    context_note = st.session_state.get("ai_assistant_context_note")
    if context_note:
        st.caption(f"最近一次回复引用的真实业务数据：{context_note}")

    error_message = st.session_state.pop("ai_assistant_error", None)
    if error_message:
        st.error(f"AI 助手生成失败：{error_message}")
        st.caption("你的输入内容已保留，可以直接再次点击“发送”重试。")

    prompt = st.text_area(
        "输入消息",
        key="ai_assistant_input",
        height=120,
        placeholder="例如：请分析这条询盘，并起草一封英文回复……",
    )
    action_row = st.columns([1, 1, 4])
    send = action_row[0].button("发送", type="primary", key="ai_assistant_send")
    clear_confirmed = st.checkbox(
        "我确认清空当前对话（无法恢复）",
        key="ai_assistant_clear_confirm",
    )
    if action_row[1].button(
        "清空当前对话",
        key="ai_assistant_clear",
        disabled=not clear_confirmed,
    ):
        st.session_state["ai_assistant_clear_requested"] = True
        st.rerun()

    if send:
        text = prompt.strip()
        if not text:
            st.warning("请先输入要发送的内容。")
        else:
            history = [
                {"role": item.get("role", ""), "content": item.get("content", "")}
                for item in messages
                if item.get("role") in ("user", "assistant")
            ]
            with st.spinner("AI 助手正在生成回复……"):
                try:
                    reply, context = ai_assistant.generate_reply(
                        ai_assistant.trim_history(history),
                        text,
                        customer_id=selected_customer_id or None,
                        inquiry_record_id=selected_record_id or None,
                    )
                except ai_assistant.AIAssistantError as exc:
                    st.session_state["ai_assistant_error"] = str(exc)
                except Exception as exc:  # noqa: BLE001 - 兜底避免页面崩溃
                    logger.error(
                        "Unexpected AI assistant failure: error_type=%s",
                        type(exc).__name__,
                    )
                    st.session_state["ai_assistant_error"] = (
                        f"生成过程中发生未预期错误（{type(exc).__name__}），"
                        "请查看应用终端日志后重试。"
                    )
                else:
                    messages.append({"role": "user", "content": text})
                    messages.append({"role": "assistant", "content": reply})
                    st.session_state["ai_assistant_messages"] = (
                        ai_assistant.trim_history(messages)
                    )
                    st.session_state["ai_assistant_context_note"] = (
                        context.summary() or "本次未引用任何业务记录"
                    )
                    st.session_state["ai_assistant_pending_input"] = ""
                    st.rerun()


st.set_page_config(page_title="TradePilot AI", layout="wide")
st.markdown(THEME_CSS, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# 侧边栏：品牌 + 现有功能入口。此处只列出已实现的模块，不新增占位入口。
# ---------------------------------------------------------------------------
st.sidebar.markdown(
    """
    <div class="tp-brand">TradePilot AI</div>
    <div class="tp-brand-sub">外贸 AI 询盘助手<br/>询盘分析 · 回复草稿 · 跟进管理</div>
    """,
    unsafe_allow_html=True,
)
st.sidebar.divider()
st.sidebar.markdown('<div class="tp-nav-label">工作台</div>', unsafe_allow_html=True)
selected_page = st.sidebar.radio(
    "导航模块",
    options=list(PAGE_DESCRIPTIONS),
    label_visibility="collapsed",
)
st.sidebar.divider()
st.sidebar.caption(
    SIDEBAR_NOTES.get(selected_page, "占位模块：此模块尚未接入业务功能。")
)

# ---------------------------------------------------------------------------
# 页面顶部：标题 + 一句业务说明。
# ---------------------------------------------------------------------------
st.header(selected_page)
st.caption(PAGE_DESCRIPTIONS[selected_page])

if selected_page == "询盘助手":
    try:
        deepseek_client.load_settings()
    except deepseek_client.DeepSeekConfigurationError as exc:
        st.warning(
            f"{exc} 应用仍可浏览；配置完成后才能使用 AI 分析。"
            "Streamlit Community Cloud 用户请在应用设置的 Secrets 中配置。"
        )
    st.warning(
        "真实 AI 模式：点击“AI 分析”会将输入邮件发送给 DeepSeek API，"
        "可能产生费用。未提供的信息应标记为“未提供”，请人工核实分析结果。"
    )

    with st.form("inquiry_analysis_form"):
        inquiry_text = st.text_area(
            "英文询盘",
            value=EXAMPLE_INQUIRY,
            key="inquiry_email",
            height=180,
            help="可以直接使用示例，也可以粘贴客户邮件。内容仅在提交分析时发送。",
        )
        submitted = st.form_submit_button("AI 分析", type="primary")

    if submitted:
        if not inquiry_text.strip():
            st.warning("请先输入英文询盘。")
        else:
            st.session_state.pop("inquiry_ai_analysis", None)
            st.session_state.pop("inquiry_ai_error", None)
            st.session_state.pop("inquiry_history_save_error", None)
            with st.spinner("正在请求 DeepSeek AI 分析，请稍候……"):
                try:
                    result = analyze_inquiry_with_ai(inquiry_text)
                except InquiryAIError as exc:
                    st.session_state["inquiry_ai_error"] = str(exc)
                except Exception as exc:
                    logger.error(
                        "Unexpected inquiry analysis failure: error_type=%s",
                        type(exc).__name__,
                    )
                    st.session_state["inquiry_ai_error"] = (
                        "分析过程中发生未预期错误"
                        f"（{type(exc).__name__}），未生成或保存历史记录。"
                        "请查看应用终端日志后重试。"
                    )
                else:
                    st.session_state["inquiry_reply_draft_en"] = result.reply_draft_en
                    st.session_state["inquiry_ai_analysis"] = {
                        "source": inquiry_text,
                        "result": result,
                    }
                    try:
                        inquiry_history.save_inquiry_record(inquiry_text, result)
                    except inquiry_history.InquiryHistoryError as exc:
                        st.session_state["inquiry_history_save_error"] = str(exc)
                    else:
                        st.session_state["inquiry_history_saved"] = True

    error_message = st.session_state.get("inquiry_ai_error")
    if error_message:
        st.error(f"AI 分析失败：{error_message}")

    if st.session_state.get("inquiry_history_saved"):
        st.session_state.pop("inquiry_history_saved", None)
        st.success("分析成功，已保存到历史记录。")
    history_save_error = st.session_state.get("inquiry_history_save_error")
    if history_save_error:
        st.warning(f"分析已完成，但保存历史记录失败：{history_save_error}")

    stored_analysis = st.session_state.get("inquiry_ai_analysis")
    if stored_analysis:
        if stored_analysis["source"] != inquiry_text:
            st.info("输入内容已修改，请重新点击“AI 分析”更新结果。")
        else:
            analysis = stored_analysis["result"]
            if isinstance(analysis, InquiryAIAnalysis):
                st.divider()
                st.subheader("分析结果")
                st.caption(
                    "以下内容由 DeepSeek AI 生成，请人工复核后再对客户使用。"
                )
                render_analysis_details(analysis, key_prefix="current")
elif selected_page == "历史记录":
    try:
        records = inquiry_history.list_inquiry_records()
    except inquiry_history.InquiryHistoryError as exc:
        st.error(str(exc))
    else:
        if not records:
            st.info("暂无历史询盘记录。成功完成一次 AI 分析后，记录会自动保存在这里。")
        else:
            st.caption(
                f"共 {len(records)} 条本地记录，按时间从新到旧排列。"
                "展开某条记录可查看完整分析并删除。"
            )
            for record in records:
                product = record.customer_needs.get("product", "").strip() or "未提供"
                summary = (
                    f"{format_timestamp(record.created_at)} · "
                    f"{record.intent_level}意向 · {product}"
                )
                with st.expander(summary):
                    st.markdown("**原始英文询盘**")
                    st.write(record.original_inquiry)

                    render_analysis_details(
                        record,
                        key_prefix=record.record_id,
                    )

                    st.divider()
                    confirm_key = f"confirm_delete_{record.record_id}"
                    confirmed = st.checkbox(
                        "我确认删除这条历史记录",
                        key=confirm_key,
                    )
                    if st.button(
                        "删除此记录",
                        key=f"delete_record_{record.record_id}",
                        disabled=not confirmed,
                        type="secondary",
                    ):
                        try:
                            deleted = inquiry_history.delete_inquiry_record(
                                record.record_id
                            )
                        except inquiry_history.InquiryHistoryError as exc:
                            st.error(str(exc))
                        else:
                            if deleted:
                                st.success("历史记录已删除。")
                                st.rerun()
                            else:
                                st.warning(
                                    "这条记录可能已被删除，请刷新历史记录列表。"
                                )
elif selected_page == "询盘管理":
    st.caption(
        "集中查看历史询盘并维护业务跟进信息。"
        "业务优先级由你维护，与 AI 客户意向判断相互独立。"
    )
    saved_message = st.session_state.pop("inquiry_management_saved", None)
    if saved_message:
        st.success(saved_message)
    save_error = st.session_state.pop("inquiry_management_save_error", None)
    if save_error:
        st.error(f"保存跟进信息失败：{save_error}")

    try:
        stats = inquiry_history.get_inquiry_history_stats()
        all_records = inquiry_history.list_inquiry_records()
    except inquiry_history.InquiryHistoryError as exc:
        st.error(f"读取询盘管理数据失败：{exc}")
    else:
        metric_columns = st.columns(5)
        metric_columns[0].metric("询盘总数", stats.total)
        metric_columns[1].metric("待跟进", stats.pending)
        metric_columns[2].metric("跟进中", stats.in_progress)
        metric_columns[3].metric("已完成", stats.completed)
        metric_columns[4].metric("高优先级", stats.high_priority)

        st.divider()
        filter_columns = st.columns([2, 1, 1])
        keyword = filter_columns[0].text_input(
            "关键词搜索",
            key="inquiry_management_keyword",
            help="搜索原始邮件、中文翻译、产品需求和跟进备注。",
        )
        selected_status = filter_columns[1].selectbox(
            "跟进状态筛选",
            options=("全部", *inquiry_history.FOLLOW_UP_STATUSES),
            key="inquiry_management_status_filter",
        )
        selected_priority = filter_columns[2].selectbox(
            "优先级筛选",
            options=("全部", *inquiry_history.PRIORITIES),
            key="inquiry_management_priority_filter",
        )

        try:
            records = inquiry_history.list_inquiry_records(
                keyword=keyword,
                status=None if selected_status == "全部" else selected_status,
                priority=(
                    None
                    if selected_priority == "全部"
                    else selected_priority
                ),
            )
        except inquiry_history.InquiryHistoryError as exc:
            st.error(f"搜索或筛选询盘失败：{exc}")
        else:
            if not all_records:
                st.info("目前没有历史询盘。成功完成一次 AI 分析后，询盘会自动进入管理列表。")
            elif not records:
                st.info("没有符合当前关键词和筛选条件的询盘。")
            else:
                st.caption(f"当前显示 {len(records)} 条询盘，展开后可编辑跟进信息。")

            for record in records:
                product = record.customer_needs.get("product", "").strip()
                if not product or product == "未提供":
                    product = "未提供"
                follow_up_date = record.next_follow_up_date or "未提供"
                title = (
                    f"{format_timestamp(record.created_at)} · {product} · "
                    f"{record.follow_up_status} · 优先级{record.priority} · "
                    f"下次跟进：{follow_up_date}"
                )
                with st.expander(title):
                    edit_columns = st.columns(2)
                    edit_columns[0].selectbox(
                        "跟进状态",
                        options=inquiry_history.FOLLOW_UP_STATUSES,
                        index=inquiry_history.FOLLOW_UP_STATUSES.index(
                            record.follow_up_status
                        ),
                        key=f"follow_up_status_{record.record_id}",
                        on_change=save_management_field_changes,
                        args=(record.record_id,),
                    )
                    edit_columns[1].selectbox(
                        "业务优先级",
                        options=inquiry_history.PRIORITIES,
                        index=inquiry_history.PRIORITIES.index(
                            record.priority
                        ),
                        key=f"follow_up_priority_{record.record_id}",
                        help="此业务优先级由你维护，不会根据 AI 意向判断自动推断。",
                        on_change=save_management_field_changes,
                        args=(record.record_id,),
                    )
                    st.date_input(
                        "下次跟进日期（没有计划可留空）",
                        value=(
                            date.fromisoformat(record.next_follow_up_date)
                            if record.next_follow_up_date
                            else None
                        ),
                        key=f"next_follow_up_date_{record.record_id}",
                        on_change=save_management_field_changes,
                        args=(record.record_id,),
                    )
                    st.text_area(
                        "跟进备注",
                        value=record.follow_up_notes,
                        key=f"follow_up_notes_{record.record_id}",
                        height=100,
                        on_change=save_management_field_changes,
                        args=(record.record_id,),
                    )

                    st.divider()
                    st.markdown("**原始英文询盘**")
                    st.write(record.original_inquiry)
                    render_analysis_details(
                        record,
                        key_prefix=f"management_{record.record_id}",
                    )
elif selected_page == "业务概览":
    render_business_analytics()
elif selected_page == "AI 助手":
    render_ai_assistant()
elif selected_page == "产品目录":
    render_product_catalog()
elif selected_page == "客户管理":
    render_customer_management()
elif selected_page == "设置":
    render_settings()
