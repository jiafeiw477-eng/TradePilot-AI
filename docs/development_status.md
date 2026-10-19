# TradePilot AI 开发进度

更新日期：2026-10-19

## 阶段 0：项目检查与数据保护 — 已完成

- 实际项目目录：`C:\Users\wvemi\Desktop\github\trade_pilot_ai`。本次用户消息中的 `C:\Users\wemi\...` 路径不存在。
- 技术栈：Python 3.12、Streamlit、Pandas、OpenAI SDK（DeepSeek 兼容接口）、python-dotenv、SQLite、pytest。
- 入口为 `app.py`；Git 分支为 `master`，远程为 `origin`。本次审计基线为 GitHub 与本地同步的 `a136672`；本轮生成审计文件并补充限流测试后，工作区会有待审核修改。
- 检查过 `.gitignore`：忽略 `.env`、`.streamlit/secrets.toml`、`data/`、虚拟环境、缓存和日志。
- 本地 SQLite 文件存在。只核对了表结构和记录数量，没有读取询盘正文；`inquiry_history` 保留 1 条记录，`app_settings` 表存在且当前 0 条设置。未发现 `products`、`customers` 表；页面访问时由模块按需创建。
- 不重置数据库，不覆盖或删除工作区已有文件。进入本轮之前已存在大量未提交功能实现，本次在其上审查、补测和小范围修复；没有把所有既有代码都归因于本轮。

## 阶段 1：统一页面结构与导航 — 已实现，已测试

- `app.py` 统一导航到业务概览、询盘助手、AI 助手、产品目录、客户管理、历史记录、询盘管理和设置。
- `.streamlit/config.toml` 提供浅色、克制的工作台主题。
- Streamlit `AppTest` 覆盖导航和新模块页面渲染。

## 阶段 2：完善询盘处理 — 已实现，已测试

- 保留 DeepSeek 结构化分析、回复草稿、SQLite 历史、跟进字段和历史删除确认。
- `inquiry_history` 增量迁移支持可选 `customer_id`，旧记录仍可查看；新增客户关联与解除关联操作。
- CSV 导出按当前筛选结果生成，并增加电子表格公式注入防护。
- CSV 高风险字段、询盘保存、历史查询与页面行为有自动化测试。

## 阶段 3：业务数据分析 — 已实现，已测试

- 指标、趋势、状态/产品/国家分布、信息完整度、规则型洞察、筛选和 CSV 均使用 SQLite 真实记录。
- “已生成回复草稿”与实际发送邮件区分；总数为 0 时不除零。
- 临时 SQLite 测试覆盖日期范围、状态筛选、趋势、指标与导出。

## 阶段 4：产品目录 — 已实现，已测试

- `services/product_catalog.py` 提供产品表初始化/兼容迁移、增删改查、分类与状态筛选、唯一 SKU、价格和 MOQ 校验、关键词匹配。
- `app.py` 提供分页、详情、新增/编辑和二次确认删除。
- 修复精确 SKU 命中后继续关键词回退、误带入其他产品的问题；AI 使用产品上下文的测试通过。
- 尚未实现 CSV/Excel 批量导入；尚未在现有真实数据库中创建产品表或写入产品数据。

## 阶段 5：客户管理 — 已实现，已测试

- `services/customer_manager.py` 提供客户资料 CRUD、邮箱校验、重复检查、搜索/筛选、归档/恢复。
- `app.py` 提供客户详情、询盘关联和关联记录查看。
- 有关联询盘时删除操作改为归档，不级联删除历史询盘；测试使用隔离数据库。
- 尚无登录、角色权限或多用户数据隔离。

## 阶段 6：AI 助手 — 已实现，真实服务未验证

- `services/ai_assistant.py` 复用现有 DeepSeek 客户端，实现多轮对话、有限历史裁剪、任务快捷入口以及按需检索产品、客户和询盘上下文。
- 回复语言、语气、长度等偏好会进入模型提示词；回复是草稿，不表示邮件已发送。
- 使用模拟客户端测试正常响应、空响应、API 错误和业务数据引用，没有发起真实 DeepSeek 请求。
- 本次修复：相关业务表读取失败时显示明确错误并取消 API 请求，避免把数据库故障误报成“没有匹配记录”。
- 真实 API、实际额度和云端服务状态尚未验证。

## 阶段 7：系统设置 — 已实现，已测试

- `services/app_settings.py` 将非敏感偏好持久化至 `app_settings` 表，包含校验、默认值和确认后恢复默认值。
- API Key 仍只从 `.env`、环境变量或 Streamlit Secrets 读取，不回显或写入 SQLite。
- 设置页提供模型配置状态、明确点击才运行的 API 连接测试和数据库信息。
- API 连接测试按钮一旦点击会发送真实请求，可能产生费用；本轮未点击。

## 阶段 8：模块联动与数据安全 — 部分完成

- 共享 SQLite 连接层使用单一数据库路径；业务写入使用参数化 SQL；询盘到客户采用可选外键式 ID 关联，不级联删除。
- `.gitignore` 排除 `.env`、Streamlit 私密配置、本地数据、缓存和日志；CSV 导出已防护公式注入。
- 已向 AI 提供有限的匹配业务上下文；查库失败会取消模型请求。
- 未完成：登录/授权、记录级数据隔离、静态/传输加密、托管数据库、云端持久化、生产级备份恢复和全面隐私脱敏。

## 阶段 9：测试、文档与部署准备 — 本地检查完成，云端未部署

### 本轮实际修改文件

- `app.py`：统一调整页面留白、导航侧栏宽度、指标展示卡片和窄屏样式；修复业务概览第二排指标沿用四列布局导致长标题截断的问题。并补充数据库读取异常反馈、设置读取失败时停止渲染依赖配置的表单，以及 AI 对话清空二次确认。
- `services/ai_assistant.py`：记录业务数据读取异常；查库失败时取消 DeepSeek 请求。
- `services/product_catalog.py`：精确名称/SKU/分类命中后不再继续宽泛关键词匹配。
- `services/inquiry_history.py`：CSV 公式注入防护。
- `tests/test_ai_assistant.py`、`tests/test_app.py`、`tests/test_inquiry_history.py`：补充上述路径的自动化测试。
- `requirements.txt`、`requirements-dev.txt`：分离应用运行依赖和 pytest 开发依赖，避免云端安装测试工具。
- `README.md`、`docs/portfolio.md`、`docs/development_status.md`：修正功能清单、限制、测试结果并记录阶段状态。
- 工作区原先已有未提交的 `app.py`、`inquiry_parser.py`、`services/inquiry_history.py`、测试以及新服务文件。本次保留并继续在这些文件基础上修改；没有提交或推送。

### 测试命令与结果

在系统临时目录创建 Python 3.12.10 隔离环境，未复用或覆盖用户数据库；安装 `requirements-dev.txt` 后运行：

```text
python -m pytest -q
119 passed

python -m compileall -q app.py inquiry_parser.py services tests scripts
退出码 0

Streamlit 启动检查：app.py 成功启动；本地首页 HTTP 200，未调用 DeepSeek API。
视觉检查：浏览器确认首页已渲染，窄屏视口中三项次级指标标题完整显示。
```

测试不调用 DeepSeek API。使用真实数据库时只读取过表结构和行数，数据库仍被 Git 忽略。

### 尚未解决/下一步

1. 如需继续本地开发，在本机 `.venv` 中安装 `requirements-dev.txt`；本轮已用隔离环境启动并通过首页 HTTP 检查。
2. 以合成数据手动测试产品、客户、询盘关联和 CSV 下载；不要在未授权时使用真实客户邮件。
3. 如要验证真实 DeepSeek，先确认 API 费用与数据授权，再手动进行一次请求。
4. 如要云端部署，需配置 Streamlit Cloud Secrets；先解决无认证、SQLite 临时存储及共享数据的风险。当前没有可用的永久公网预览地址。
5. 检查并确认本工作区现有改动后，再由项目所有者决定是否提交；本轮没有执行 `git add`、`git commit` 或 `git push`。

## 2026-10-19 项目审计与交付复核

- 按模块重新核对入口、服务层、SQLite 访问、依赖、配置、测试和 README；详细状态见 `docs/project_audit.md`。
- 当前完整测试在相同隔离环境复跑为 112 项；新增 429 限流响应测试后，实测为 **113 passed**。此前开发记录中的 119 项未能在当前提交复现，README 与作品集已明确区分。
- `python -m compileall -q app.py inquiry_parser.py services tests scripts` 通过；`git diff --check` 通过。
- 自动化测试未调用真实 DeepSeek API。配置读取、429 错误说明、超时、空响应及模拟响应有测试；真实账号连通和模型质量仍需人工验证。
- `app.py` 可从项目根目录导入；此前本地启动检查返回 HTTP 200。当前未重新启动受用户正在运行的应用，也未操作用户浏览器。
- `.env`、`data/` 和 `.streamlit/secrets.toml` 均有忽略规则；无密钥被提交。当前仓库核心提交为 `a136672`。
- Community Cloud 未部署。需要用户登录/授权后才能创建应用及配置 Secrets；公开访问风险、无登录和云端 SQLite 非持久性需先由项目所有者接受。
