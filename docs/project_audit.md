# TradePilot AI 项目功能审计

审计日期：2026-10-19
仓库：`jiafeiw477-eng/TradePilot-AI`
本地分支：`master`
审计提交基线：`a136672`（审计后修改需重新提交）

## 审计口径

- **已验证可用**：通过代码检查和隔离数据库/模拟 API 自动化测试；不代表真实 DeepSeek 账户或线上环境已验证。
- **存在问题**：已发现需要修复的行为或安全风险。
- **尚未实现**：代码中没有对应功能。
- **需要人工验证**：需要真实浏览器、第三方服务凭证、人工操作或实际部署才能确认。

## 功能检查表

| 模块 | 状态 | 代码与测试依据 | 边界 / 后续检查 |
|---|---|---|---|
| 业务概览与统计 | 已验证可用 | `services/inquiry_history.py` 从 SQLite 计算日期趋势、状态统计、业务维度、洞察和 CSV；`tests/test_app.py`、`tests/test_inquiry_history.py` 使用临时数据库覆盖数据与空状态。 | 未以真实业务数据评估洞察是否符合实际业务决策。 |
| 询盘翻译、需求提取与回复草稿 | 已验证可用（模拟响应） | `services/inquiry_ai.py` 校验结构化返回；`tests/test_inquiry_ai.py` 覆盖正常响应、空响应、无效响应和错误。 | 模型准确性和真实客户内容效果需人工评估。 |
| DeepSeek API 客户端与错误处理 | 已验证可用（模拟客户端） | `services/deepseek_client.py` 创建 OpenAI 兼容客户端；错误层区分 HTTP、连接、超时、空回复及限流，并隐藏日志中的 API Key；测试不发真实请求。 | 真实 API Key、账户额度、模型可用性及网络连通需用户主动验证。 |
| 历史记录与跟进字段 | 已验证可用 | SQLite 初始化/兼容迁移、保存、查询、删除、状态/优先级/备注/日期、筛选和统计由 `tests/test_inquiry_history.py` 覆盖。 | 生产级备份与数据加密尚无。 |
| 产品目录 CRUD | 已验证可用 | `services/product_catalog.py` 与 `tests/test_product_catalog.py` 覆盖新增、读取、编辑、删除、校验、SKU 冲突及检索。 | CSV/Excel 批量导入尚未实现。 |
| 客户管理与询盘关联 | 已验证可用 | `services/customer_manager.py` 与 `tests/test_customer_manager.py` 覆盖校验、去重、归档/恢复、关联询盘和关联数据保护。 | 登录、角色权限和多用户数据隔离尚未实现。 |
| AI 助手使用业务上下文 | 已验证可用（模拟客户端） | `services/ai_assistant.py` 按当前问题筛选有限的产品、客户和询盘上下文；测试验证对话、错误和上下文。 | 真实模型输出、延迟、费用和产品/客户问答质量需人工验证。 |
| 系统设置与密钥读取 | 已验证可用（自动化测试） | 非敏感偏好持久化至 SQLite；`services/deepseek_client.py` 从环境变量、`.env` 或 Streamlit Secrets 读配置；测试验证缺失配置、优先级和密钥不进入对象 repr。 | Community Cloud Secrets 需在已登录账户中人工配置。 |
| Streamlit 页面与导航 | 已验证可用（Streamlit AppTest） | `tests/test_app.py` 覆盖导航、页面渲染、空数据、确认和错误状态。 | 当前浏览器视觉与多尺寸布局需要人工验收；本轮未对浏览器页面执行操作。 |
| 英文回复复制 | 已实现，需人工验证 | 英文草稿由 `st.code` 展示，提供原生复制控件并有文本下载；页面测试验证草稿展示。 | 浏览器剪贴板权限、按钮实际复制和粘贴结果未做端到端验证。 |
| SQLite 持久化 | 已验证本地文件路径与测试库行为 | `services/database.py` 统一连接；服务使用参数化 SQL；测试数据库由 pytest 临时目录提供。 | Community Cloud 本地文件系统不保证永久保存；生产环境需持久化数据库及备份。 |
| Streamlit Community Cloud 部署 | 需要人工验证 | 仓库包含 `app.py`、`requirements.txt`、Streamlit 主题和 Secrets 模板，入口可本地启动。 | 未完成真实应用创建、Secrets 配置或公网访问验证；未部署成功。 |
| 登录与访问控制 | 尚未实现 | 项目没有用户认证或数据隔离模块。 | 不应将未受控的公开部署用于真实客户资料；公开启用 DeepSeek Key 有额度风险。 |

## 本次验证

2026-10-19 在 Python 3.12.10 隔离环境运行：

```text
python -m pytest -q
113 passed（含新增 429 限流错误测试）

python -m compileall -q app.py inquiry_parser.py services tests scripts
通过
```

本次没有调用真实 DeepSeek API，没有写入项目 SQLite 数据库，也没有完成云端部署。

## 发现与优先级

1. 公开演示环境缺少认证与数据隔离：不要在未评估费用与数据风险前设置真实 API Key 或处理真实客户信息。
2. Community Cloud SQLite 不能作为可靠的永久存储；原型演示可接受重启后数据丢失的风险，正式使用前需规划外部数据库和备份。
3. 真实 DeepSeek 连通、浏览器复制、线上部署都需要人工授权或手动验收。
4. 本次复测发现旧文档中测试数量 `119 passed` 与当前环境收集的数量不一致；新增限流测试后，本轮完整复测为 `113 passed`。不要将旧计数当成本轮验证结果。
