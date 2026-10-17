# TradePilot AI｜外贸询盘助手

**TradePilot AI 是一个面向外贸业务员的 Streamlit Web 原型，可调用 DeepSeek 分析英文询盘、生成回复草稿，并使用 SQLite 保存分析与跟进信息。**

> English summary: TradePilot AI is a local Streamlit prototype for analyzing English export inquiries with DeepSeek, saving successful analyses to SQLite, and managing follow-up activity.

本项目用于展示 AI 产品原型、用户流程和基础工程实践，不代表已验证商业效果。项目没有用户量、准确率、节省时间或收益等实测数据。

## 项目要解决的问题

外贸业务员收到英文询盘后，通常需要人工阅读邮件、整理产品与数量等需求、识别尚未明确的信息、拟写英文回复，并持续记录后续跟进。零散的邮件内容与跟进状态容易增加整理成本。

本项目尝试把“理解询盘—形成回复草稿—保存记录—维护跟进状态”放入一个简易网页流程中。AI 输出仅作为辅助，业务员仍需核对内容；对于原文没有说明的信息，系统提示模型标记为“未提供”，不能把模型输出当作报价、认证、库存或交期承诺。

## 目标用户与典型场景

- **目标用户**：处理中小批量出口询盘的外贸业务员或业务助理。
- **典型场景**：用户粘贴一封英文询盘，点击“AI 分析”，检查中文翻译、采购需求、风险和回复草稿；成功结果进入本地历史列表，之后在“询盘管理”中记录业务跟进状态、优先级、备注和下次联系日期。
- **原型验证方式**：使用示例询盘或经脱敏的测试文本验证页面流程。真实客户邮件会被发送到已配置的 DeepSeek 服务，提交前需确认数据授权和隐私要求。

## 当前版本实际功能

### 已实现

- 中文 Streamlit 网页，提供“业务概览、询盘助手、产品目录、客户管理、设置、历史记录、询盘管理”导航。
- 询盘助手通过 OpenAI Python SDK 兼容客户端调用 DeepSeek Chat Completions API，解析结构化 JSON，展示中文翻译、需求字段、辅助意向判断、风险、英文回复草稿和跟进建议。
- 明确标注真实 API 调用；只有用户提交分析时才发送邮件内容。未配置、HTTP 错误、超时、连接错误、空响应及格式错误会显示错误提示，不用演示数据伪装成功。
- 只有获取到有效分析后才尝试将询盘及分析结果保存到本地 SQLite。
- 历史记录页面可查看分析详情和英文回复；支持勾选确认后删除记录。
- 英文回复用 Streamlit `st.code` 代码区呈现，可使用组件提供的复制控件。自动化测试检查内容是否呈现，**没有在真实浏览器中验证剪贴板权限或实际粘贴结果**。
- 询盘管理页提供总数、状态数和高优先级数统计，关键词搜索、状态/优先级筛选，以及状态、业务优先级、备注、下次跟进日期自动写入 SQLite。
- 旧的 `inquiry_history` 表使用 `ALTER TABLE` 增量扩展；旧记录默认为“待跟进”、优先级“中”、空备注、未设置日期。
- 独立的 `inquiry_parser.py` 规则解析模块可解析一部分常见英文表达，并有单元测试；**当前 Streamlit 询盘助手实际走 DeepSeek 分析流程，而非该规则解析器**。

### 当前仍是占位或尚未验证的内容

- “业务概览、产品目录、客户管理、设置”仍为占位页面；尚未实现产品 CSV/Excel 导入、产品匹配或客户 CRM 工作流。
- AI 真实 API 调用没有纳入自动化测试。本次文档阶段不发送真实请求，因此不能据此声称当前 API Key、账户余额、模型服务状态或真实响应可用。
- Pandas 已列在依赖清单中，但当前应用源代码没有直接导入使用。
- SQLite 数据存储在本机，未加密；当前不包含用户登录、角色权限、备份与恢复功能。
- SQLite 数据库在本地或云端应用文件系统中；Streamlit Community Cloud 的本地文件系统不应视为持久化存储，重启或重新部署后记录可能丢失。

## 技术栈

| 技术 | 当前用途 |
|---|---|
| Python 3.11 / 3.12 | 应用与测试运行环境 |
| Streamlit | 页面、导航、表单及本地交互 |
| SQLite / `sqlite3` | 本地历史询盘与跟进字段持久化 |
| OpenAI Python SDK | 使用兼容接口发送 DeepSeek Chat Completions 请求 |
| `python-dotenv` | 从项目根目录 `.env` 读取 API 配置 |
| pytest | 规则解析、API 响应处理、数据库及 Streamlit 页面测试 |
| Pandas | 已声明依赖；目前核心页面尚未直接使用 |

## 项目结构

```text
trade_pilot_ai/
├── app.py                         # Streamlit 入口、导航和页面呈现
├── inquiry_parser.py              # 独立的规则式英文询盘解析
├── services/
│   ├── deepseek_client.py         # 环境变量配置与 API 客户端
│   ├── inquiry_ai.py              # DeepSeek 请求、错误分类和响应校验
│   └── inquiry_history.py         # SQLite 初始化、迁移、查询、更新、删除和统计
├── scripts/
│   └── test_deepseek_connection.py # 手动真实 API 连通性测试（会发请求）
├── tests/
│   ├── test_app.py
│   ├── test_deepseek_client.py
│   ├── test_inquiry_ai.py
│   ├── test_inquiry_history.py
│   └── test_inquiry_parser.py
├── docs/
│   └── portfolio.md               # 产品与项目作品集说明
├── data/
│   └── inquiry_history.sqlite3    # 自动创建的本地数据库，已加入忽略规则
├── .env.example                   # 无密钥配置模板
├── .gitignore                     # 忽略密钥、数据库和 Python 缓存
├── .streamlit/
│   └── secrets.toml.example       # Streamlit Cloud Secrets 占位模板
├── requirements.txt               # Python 依赖
└── README.md
```

## Windows 安装与启动

在 VS Code 打开项目后，从项目根目录的 PowerShell 终端运行以下命令。若已有 `.venv`，不必重复创建。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

如果使用 Python 3.11，将 `py -3.12` 改为 `py -3.11`。如果 PowerShell 不允许激活脚本，可直接调用虚拟环境中的解释器：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Streamlit 启动后，根据终端提示打开本地网页。

## DeepSeek 配置与安全

项目模板 [.env.example](./.env.example) 只包含空 API Key 和非秘密的配置示例。第一次配置时，仅在确认本地 `.env` 不存在后复制模板：

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

只在本机 `.env` 中填写自己从 DeepSeek 官方渠道获得的密钥。不要把密钥粘贴到聊天、截图、代码、README 或作品集文档中。`.gitignore` 已忽略 `.env`、`.env.*`（保留 `.env.example`）、`.streamlit/secrets.toml` 及 `data/`。

可在不打印密钥的前提下检查是否读取到 API Key：

```powershell
.\.venv\Scripts\python.exe -c "from dotenv import load_dotenv; import os; load_dotenv('.env'); print('DEEPSEEK_API_KEY 已读取' if os.getenv('DEEPSEEK_API_KEY') else 'DEEPSEEK_API_KEY 未设置')"
```

真实连通性脚本只应由用户主动执行：

```powershell
.\.venv\Scripts\python.exe -m scripts.test_deepseek_connection
```

该命令会发起真实 API 请求，可能产生费用。本项目自动化测试不应运行此脚本；本次测试没有发送 API 请求。

## Streamlit Community Cloud 部署

### 部署前的重要限制

- 本项目使用本地 SQLite 文件保存询盘。Streamlit Community Cloud 的本地文件系统不是持久化数据库；应用重启、重新部署或迁移后，历史记录和跟进资料可能丢失。请勿将真实客户邮件用于此云端原型。生产使用前应改用有访问控制和备份的托管数据库。
- 当前应用没有用户登录或访问权限控制。若将应用公开，任何能访问页面的人都可能提交询盘并触发 DeepSeek API 费用；也可能查看同一应用实例中的历史数据。不要在未限制访问时配置有费用的 API Key 或处理真实客户资料。

### 发布步骤

1. 将 GitHub 仓库推送到包含 `app.py` 和 `requirements.txt` 的分支。
2. 登录 [Streamlit Community Cloud](https://share.streamlit.io/)，选择 **Create app**，指定仓库、分支（当前为 `master`）和主文件路径 `app.py`。
3. 在应用的 **Settings → Secrets** 中按 [`.streamlit/secrets.toml.example`](./.streamlit/secrets.toml.example) 的格式配置。将占位符替换为自己的值；不要提交 `secrets.toml`。
4. 保存配置并等待部署日志显示应用启动成功。未配置密钥时，应用仍可打开，但询盘助手会提示缺少配置。
5. 首次部署建议先不配置真实 DeepSeek Key，验证页面能够启动后，再根据访问范围和费用风险决定是否启用 API。

云端使用 Streamlit Secrets；本地开发仍可使用根目录 `.env`。环境变量优先于 Secrets。

## 测试与检查

运行所有自动化测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

运行 Python 语法编译检查：

```powershell
.\.venv\Scripts\python.exe -m compileall -q app.py inquiry_parser.py services tests scripts
```

最近一次项目测试结果（2026-10-17）：45 passed。该结果不等同于云端部署验证。测试覆盖解析器、模拟 API 响应处理、SQLite 操作和 Streamlit 页面流程；不覆盖真实 API 账户状态、云端持久化和浏览器剪贴板行为。

## 截图建议

目前没有随项目提交的功能截图。后续可将脱敏后的截图保存到 `docs/screenshots/`，建议截图：

1. `inquiry-assistant.png`：示例询盘、分析分区和英文草稿，确认截图中没有真实客户资料。
2. `inquiry-history.png`：历史记录展开详情及复制控件。
3. `inquiry-management.png`：统计卡片、搜索筛选和跟进表单。
4. `placeholder-pages.png`：展示仍未实现的页面确为占位，避免让读者误以为功能已交付。

截图前应检查浏览器、终端和页面中没有 API Key、真实客户邮箱或其他敏感信息。

## 已知限制与未来规划

### 已知限制

- AI 结果可能不准确，仍须人工核对；模型提示词不能替代业务审核。
- 真实 AI 请求可能花费费用；超时或网络中断后的状态应由用户根据错误提示判断。
- 数据库为未加密的本地文件，适合原型演示，不代表生产级客户数据管理方案。
- 搜索为 SQLite 文本匹配，不是全文检索；没有联系人、公司名称等独立 CRM 字段。
- 当前没有产品导入、目录匹配、CRM、账号权限、云同步、备份恢复或真实浏览器端到端剪贴板测试。

### 未来规划（尚未实现）

1. 由业务用户评审 AI 结果，建立脱敏测试集和人工标注的质量评估方式。
2. 增加产品 CSV/Excel 导入、字段校验和可解释的匹配流程。
3. 增加客户联系人及跟进活动模型，并评估数据导出、备份和访问控制。
4. 补充可重复的浏览器端测试与剪贴板手动验收清单。
5. 在有明确业务样本和测量方法后，再评估准确率、处理耗时等指标；目前没有这些实测数据。

## 项目作品集说明

产品背景、用户流程、设计理由、当前测试结果与未来方向见 [docs/portfolio.md](./docs/portfolio.md)。
