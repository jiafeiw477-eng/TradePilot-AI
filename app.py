from __future__ import annotations

import logging
from datetime import date

import streamlit as st

import services.deepseek_client as deepseek_client
import services.inquiry_history as inquiry_history
from services.inquiry_ai import (
    InquiryAIAnalysis,
    InquiryAIError,
    analyze_inquiry_with_ai,
)


logger = logging.getLogger(__name__)

PAGE_DESCRIPTIONS: dict[str, str] = {
    "业务概览": "业务概览入口。后续可在这里查看询盘和跟进进度。",
    "询盘助手": "使用 DeepSeek AI 分析英文询盘、提取需求并生成回复草稿。",
    "产品目录": "产品目录入口。后续将逐步支持 CSV/Excel 导入和产品匹配。",
    "客户管理": "客户管理入口。后续将逐步支持 SQLite 客户资料和跟进记录。",
    "设置": "设置入口。后续配置将通过环境变量读取，不会把 API 密钥写入代码。",
    "历史记录": "查看、复制或删除本地保存的历史询盘分析。",
    "询盘管理": "管理询盘跟进状态、优先级、备注和下次跟进日期。",
}

EXAMPLE_INQUIRY = (
    "Hello, we are a furniture importer based in Germany. We are interested in "
    "200 wooden dining tables, preferably made from FSC-certified oak. Our target "
    "price is USD 180 per table. Please send your product catalog and quotation. "
    "We need delivery to Hamburg within 45 days under FOB terms. Could you also "
    "confirm your payment terms and whether samples are available?"
)


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


st.set_page_config(page_title="TradePilot AI", layout="wide")

st.title("TradePilot AI")
st.caption("外贸 AI 询盘助手 · DeepSeek AI 询盘分析")

st.sidebar.header("导航")
selected_page = st.sidebar.radio(
    "导航模块",
    options=list(PAGE_DESCRIPTIONS),
    label_visibility="collapsed",
)
if selected_page == "询盘助手":
    st.sidebar.info("AI 模式：点击分析后，邮件内容将发送至 DeepSeek API。")
elif selected_page == "历史记录":
    st.sidebar.info("本地记录：分析结果保存在本机 SQLite 数据库中。")
elif selected_page == "询盘管理":
    st.sidebar.info("本地管理：所有跟进信息保存在本机 SQLite 数据库中。")
else:
    st.sidebar.info("占位模块：此处暂未接入相关业务功能。")

st.header(selected_page)

if selected_page == "询盘助手":
    st.write(
        "输入客户的英文询盘，使用 DeepSeek AI 获取翻译、需求分析、意向判断、"
        "风险提示和英文回复草稿。"
    )
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
                st.subheader("询盘中文翻译")
                st.write(analysis.translation_zh)

                st.subheader("客户需求分析")
                for label, field in (
                    ("产品", "product"),
                    ("规格", "specifications"),
                    ("数量", "quantity"),
                    ("目标价格", "target_price"),
                    ("交货时间", "delivery_time"),
                    ("贸易条款", "trade_terms"),
                    ("其他要求", "other_requirements"),
                ):
                    st.markdown(f"**{label}：** {analysis.customer_needs[field]}")

                st.subheader("客户意向判断")
                st.markdown(f"**辅助判断：{analysis.intent_level}意向**")
                st.write(analysis.intent_rationale)
                st.caption("意向判断仅供参考，不是确定结论。")

                st.subheader("风险与待确认事项")
                if analysis.risks_and_questions:
                    for item in analysis.risks_and_questions:
                        st.markdown(f"- {item}")
                else:
                    st.write("AI 未列出待确认事项；请结合实际业务人工复核。")

                st.subheader("英文回复草稿")
                st.caption("点击下方代码区域右上角的复制图标，即可复制英文草稿。")
                st.code(
                    analysis.reply_draft_en,
                    language=None,
                )
                st.markdown("**回复草稿中文翻译**")
                st.write(analysis.reply_draft_zh)

                st.subheader("后续跟进建议")
                for suggestion in analysis.follow_up_suggestions:
                    st.markdown(f"- {suggestion}")
elif selected_page == "历史记录":
    st.write("这里保存每次成功的真实 AI 分析，数据仅存储在本机 SQLite 文件中。")
    try:
        records = inquiry_history.list_inquiry_records()
    except inquiry_history.InquiryHistoryError as exc:
        st.error(str(exc))
    else:
        if not records:
            st.info("暂无历史询盘记录。成功完成一次 AI 分析后，记录会自动保存在这里。")
        for record in records:
            preview = record.original_inquiry.replace("\n", " ").strip()
            if len(preview) > 70:
                preview = f"{preview[:70]}…"
            with st.expander(
                f"{record.created_at} · {record.intent_level}意向 · {preview}"
            ):
                st.markdown("**原始英文询盘**")
                st.write(record.original_inquiry)
                st.subheader("询盘中文翻译")
                st.write(record.translation_zh)
                st.subheader("客户需求分析")
                for label, field in (
                    ("产品", "product"),
                    ("规格", "specifications"),
                    ("数量", "quantity"),
                    ("目标价格", "target_price"),
                    ("交货时间", "delivery_time"),
                    ("贸易条款", "trade_terms"),
                    ("其他要求", "other_requirements"),
                ):
                    st.markdown(f"**{label}：** {record.customer_needs[field]}")
                st.subheader("客户意向判断")
                st.markdown(f"**辅助判断：{record.intent_level}意向**")
                st.write(record.intent_rationale)
                st.subheader("风险与待确认事项")
                if record.risks_and_questions:
                    for item in record.risks_and_questions:
                        st.markdown(f"- {item}")
                else:
                    st.write("未列出待确认事项。")
                st.subheader("英文回复草稿")
                st.caption("点击代码区域右上角的复制图标复制英文草稿。")
                st.code(record.reply_draft_en, language=None)
                st.markdown("**回复草稿中文翻译**")
                st.write(record.reply_draft_zh)
                st.subheader("后续跟进建议")
                for suggestion in record.follow_up_suggestions:
                    st.markdown(f"- {suggestion}")

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
                            st.warning("这条记录可能已被删除，请刷新历史记录列表。")
elif selected_page == "询盘管理":
    st.write(
        "集中查看历史询盘并维护业务跟进信息。业务优先级与 AI 客户意向判断相互独立。"
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

            for record in records:
                product = record.customer_needs.get("product", "").strip()
                if not product or product == "未提供":
                    product = "未提供"
                follow_up_date = (
                    record.next_follow_up_date or "未提供"
                )
                title = (
                    f"{record.created_at} · {product} · "
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

                    st.markdown("**原始英文询盘**")
                    st.write(record.original_inquiry)
                    st.subheader("询盘中文翻译")
                    st.write(record.translation_zh)
                    st.subheader("客户需求分析")
                    for label, field in (
                        ("产品", "product"),
                        ("规格", "specifications"),
                        ("数量", "quantity"),
                        ("目标价格", "target_price"),
                        ("交货时间", "delivery_time"),
                        ("贸易条款", "trade_terms"),
                        ("其他要求", "other_requirements"),
                    ):
                        st.markdown(
                            f"**{label}：** "
                            f"{record.customer_needs.get(field, '未提供')}"
                        )
                    st.subheader("客户意向判断")
                    st.markdown(f"**AI 辅助意向：{record.intent_level}**")
                    st.write(record.intent_rationale)
                    st.subheader("风险与待确认事项")
                    if record.risks_and_questions:
                        for item in record.risks_and_questions:
                            st.markdown(f"- {item}")
                    else:
                        st.write("未列出待确认事项。")
                    st.subheader("英文回复草稿")
                    st.caption("点击代码区域右上角的复制图标复制英文回复。")
                    st.code(record.reply_draft_en, language=None)
                    st.markdown("**回复草稿中文翻译**")
                    st.write(record.reply_draft_zh)
                    st.subheader("后续跟进建议")
                    for suggestion in record.follow_up_suggestions:
                        st.markdown(f"- {suggestion}")
else:
    st.write(PAGE_DESCRIPTIONS[selected_page])
    st.info("占位页面：此模块将在后续阶段逐步实现。")
