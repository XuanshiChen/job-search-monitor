# Job Search Monitor（求职监控助手）

[English README](README.md)

Job Search Monitor 是一个本地优先的个人求职管理系统，主要面向电力系统、保护与控制、变电站、电力公司及相邻电气工程岗位。它可以定期扫描配置好的公司招聘平台，将职位统一格式化并评分，自动去除重复职位，把历史记录保存在 SQLite 数据库中，生成每日求职报告，并通过 Streamlit 控制面板完成职位审核和申请跟踪。

当前策略只监控 20 家与保护控制、电力系统、变电站和电力设备方向最相关、且官网来源稳定的公司。Gmail、LinkedIn、Indeed、Glassdoor、Job Bank 和邮件聚合来源均已关闭；本地示例来源也默认关闭，不会导入虚假职位。

## 已实现的功能

- 可配置的 `critical` 和三级公司优先级体系
- Greenhouse、Lever、SmartRecruiters、Workday、SuccessFactors、Avature、Workable、SelectMinds、Jobsyn、Jobvite、Jibe、AEM Job List、Paradox、Cornerstone/CSOD、Eightfold、iCIMS、Taleo Business Edition 和通用 JSON-LD/HTML 适配器；旧邮件适配器仅保留代码，默认关闭
- 合理的请求超时、请求间隔、重试、指数退避以及来源独立失败机制
- 不同公司官网之间采用可配置的有限并发；数据库写入仍保持单线程
- 使用 `data/jobs.db` 持久保存的 SQLite 数据库
- 带来源记录的四级职位去重机制
- 透明的 0–100 分评分和可读的评分原因
- NEW、UPDATED、REOPENED、CLOSED 以及手动状态变更历史
- 对达到阈值的新职位发送桌面通知
- Markdown 每日报告和可配置的每日申请目标
- 带筛选、职位详情、备注、状态管理和基础分析的 Streamlit 控制面板
- 支持初始化、扫描、报告、状态更新和启动控制面板的命令行工具
- 使用模拟响应和本地数据的自动化测试；测试不会频繁访问真实招聘网站

## 系统架构

项目采用模块化单体架构：本地运行简单，同时把不同招聘平台的抓取逻辑与评分、存储和界面代码分开。

```text
公司 YAML 配置
      |
      v
招聘平台适配器 -> 标准化 JobCandidate
      |
      v
去重器 -> 评分器 -> 数据仓库 -> SQLite
                              |
                    +---------+---------+
                    |                   |
                 CLI/报告            控制面板
```

`JobOccurrence` 会保存一个职位在不同来源中出现过的外部 ID 和 URL。`JobEvent` 会保存职位首次发现、重要更新、重新开放、关闭以及手动状态变更历史。职位下架后不会从数据库中删除。

### 去重顺序

1. 标准公司名称 + 外部职位 ID，包括之前记录的其他来源身份
2. 移除跟踪参数和 URL 片段后的标准职位 URL
3. 公司 + 标准化职位名称 + 标准化地点指纹
4. 同一公司内保守的职位名称与地点相似度匹配

重复发现同一个职位时，只会更新 `date_last_seen`。如果职位内容发生实质变化，系统会建立 UPDATED 事件。职位连续超过 `close_missing_after_days` 天没有出现时会标记为 CLOSED，但前提是该公司的本次扫描成功。某个来源扫描失败时，系统不会因此批量关闭该公司的职位。

### 评分方式

评分器会根据公司等级、职位类别、应届生/初级岗位用语、技术关键词、首选地点和远程办公偏好加分，并根据经验要求和高级职位关键词扣分。最终分数限制在 0–100 之间，而且每一项加分、扣分和潜在问题都会保存到职位的匹配说明中。

职位描述中出现 `Senior` 只会产生较小扣分，不会自动淘汰职位。只有配置文件中明确设置的职位名称关键词才会触发硬排除。

评分权重和阈值位于 `config/settings.yaml`，关键词位于 `config/keywords.yaml`。基础版本不依赖付费模型，也没有隐藏在云端的评分逻辑。

## 项目结构

```text
job-search-monitor/
├── main.py
├── README.md
├── README.zh-CN.md
├── requirements.txt
├── .env.example
├── config/
│   ├── companies.yaml
│   ├── keywords.yaml
│   ├── settings.yaml
│   └── sample_jobs.yaml
├── data/                    # 本地持久化 SQLite 数据库
├── logs/                    # 轮转保存的结构化 JSON 日志
├── reports/                 # 自动生成的 Markdown 每日报告
├── scripts/
│   └── run_daily.ps1
├── src/
│   ├── config/
│   ├── models/
│   ├── database/
│   ├── sources/
│   │   ├── base.py
│   │   ├── greenhouse.py
│   │   ├── lever.py
│   │   ├── linkedin_email.py
│   │   ├── successfactors.py
│   │   ├── avature.py
│   │   ├── workable.py
│   │   ├── selectminds.py
│   │   ├── jobsyn.py
│   │   ├── jobvite.py
│   │   ├── jibe.py
│   │   ├── aem_joblist.py
│   │   ├── cornerstone.py
│   │   ├── eightfold.py
│   │   ├── icims.py
│   │   ├── taleo_business.py
│   │   ├── paradox.py
│   │   ├── smartrecruiters.py
│   │   ├── workday.py
│   │   ├── generic.py
│   │   └── sample.py
│   ├── matching/
│   ├── services/            # 扫描、存储、报告、邮件同步和通知
│   ├── dashboard/
│   └── utils/
└── tests/
```

## 在其他电脑上使用

代码和配置保存在私有 GitHub 仓库中，但 SQLite 数据库、日志、日报、邮件文件和 `.env` 不会上传。第一次在另一台电脑使用时：

```powershell
git clone https://github.com/XuanshiChen/job-search-monitor.git
cd job-search-monitor
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python main.py init
streamlit run src/dashboard/app.py
```

这样会创建一套新的空白本地记录。GitHub 用于同步程序代码和公司配置，不会自动同步两台电脑上的职位状态与申请历史。

## 安装

要求 Python 3.12 或更高版本。在 PowerShell 中运行：

```powershell
cd D:\job_search
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
python main.py init
```

如果 PowerShell 不允许激活虚拟环境，可以在后续命令中直接使用 `.venv\Scripts\python.exe`。激活虚拟环境只是为了让命令更简洁，并不是强制要求。

## 第一次运行

```powershell
python main.py init
streamlit run src/dashboard/app.py
```

每天第一次打开 Dashboard 时，系统会扫描全部已启用的公司官网，不会连接 Gmail 或任何聚合招聘网站。同一天筛选职位、切换标签或修改状态不会反复扫描；需要再次扫描时可以点击侧边栏的 **Refresh jobs now**。控制面板默认地址为 `http://localhost:8501`。

无论是启动时自动刷新还是手动刷新，页面都会显示当前执行阶段、正在检索的公司或来源、已完成数量和总体进度条。

在 Jobs 表格中，点击 **Open job** 可以打开原始申请页面；点击任意职位行会把它载入 **Job detail and status** 区域。在该区域可以修改职位状态、填写备注并保存。把状态改为 `APPLIED` 时，系统会自动记录申请日期。

当天申请结束后，打开 Dashboard 的 **Daily report** 标签，点击 **Generate today's report**。网页会直接预览报告、把 Markdown 文件保存到 `reports/`，并提供下载按钮。也可以继续使用等效的命令行方式：

```powershell
python main.py report --print
```

也可以使用以下命令：

```powershell
python main.py dashboard
python main.py scan --company "Hydro One"
python main.py scan --new-only
python main.py status JOB_INTERNAL_ID APPLIED --notes "已在公司官网提交"
```

`--new-only` 只控制终端显示内容。所有抓取到的职位仍然会正常参与处理，以确保 `date_last_seen`、职位更新和关闭检测保持准确。

## 配置说明

### 已配置的目标公司

现在启用 20 个公司官网来源：Hydro One、IESO、Bruce Power、Ontario Power Generation、Siemens、SEL、GE Vernova、Toronto Hydro、BBA Consultants、AltaLink、Hatch、Tetra Tech、Burns & McDonnell、Eaton、Sargent & Lundy、Black & Veatch、ABB、Schneider Electric、Hitachi Energy 和 Stantec。

BC Hydro 仍按 critical 公司保留在配置中，但其公开搜索是 SAP WebDynpro 页面，没有稳定的公开职位列表接口，因此自动扫描关闭。WSP 的公开职位页会向无人值守访问返回 Cloudflare 验证页，也保持关闭。当前策略不再使用招聘提醒邮件补充，也不会连接 LinkedIn、Indeed、Glassdoor 或 Job Bank。

### 通用设置

在 `config/settings.yaml` 中可以修改：

- 每日申请目标和报告/通知分数阈值
- Dashboard 是否在每天第一次访问时自动扫描公司官网
- 首选省份等级和首选城市
- 每日报告及申请目标所使用的本地时区
- 加拿大范围搜索和国际职位处理方式
- 远程、混合和现场办公的评分权重
- 职位关闭天数和相似度阈值
- 网络请求超时、重试、退避、间隔和 User-Agent
- 同时扫描的公司来源上限（`scanner.max_concurrent_sources`，默认 `4`）
- 桌面通知和邮件通知开关

地点设置全部来自 YAML 配置，不是硬编码的程序分支。来源返回的加拿大省份缩写会被标准化，而配置的地点等级决定最终加分。

### 关键词和评分词库

编辑 `config/keywords.yaml` 可以修改：

- 目标职位类别
- 应届生和初级职位关键词
- 技术关键词及对应分值
- 职位名称硬排除词
- 软扣分关键词

职位分类结果和每一项评分原因都会显示在职位的匹配说明中。

### 添加公司

添加公司前，应先通过公司的公开招聘网址或公开网络请求确定其招聘平台，例如 Greenhouse、Lever、SmartRecruiters、Workday 或自建网站。然后把公司添加到 `config/companies.yaml` 中对应的等级。

`critical` 公司的扫描顺序最靠前，并获得最高的公司优先级加分。

Greenhouse 示例：

```yaml
companies:
  tier_1:
    - name: Example Energy
      careers_url: https://boards.greenhouse.io/exampleenergy
      source_type: greenhouse
      options:
        board_token: exampleenergy
        # 可选。开启后会为每个职位多请求一次详情，以获得 first_published。
        fetch_details: false
```

Lever 示例：

```yaml
  tier_2:
    - name: Example Grid
      careers_url: https://jobs.lever.co/examplegrid
      source_type: lever
      options:
        site: examplegrid
        # jobs.eu.lever.co 使用 region: eu。
        region: global
```

SmartRecruiters 示例：

```yaml
  tier_2:
    - name: Example Automation
      careers_url: https://careers.smartrecruiters.com/ExampleAutomation
      source_type: smartrecruiters
      options:
        company_identifier: ExampleAutomation
        fetch_details: true
```

不同 Workday 租户的配置可能不同，需要明确填写公开 host、tenant 和招聘站点名称：

```yaml
  tier_1:
    - name: Example Power
      careers_url: https://example.wd5.myworkdayjobs.com/en-US/Careers
      source_type: workday
      options:
        host: https://example.wd5.myworkdayjobs.com
        tenant: example
        site: Careers
        locale: en-US
        fetch_details: true
```

对于能够识别的 URL，`source_type: auto` 可以自动选择 Greenhouse、Lever、SmartRecruiters 或 Workday。重要公司建议明确填写招聘平台类型和平台参数，这样更加稳定。

公开 API 参考资料：

- [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html)
- [Lever Postings API](https://github.com/lever/postings-api)
- [SmartRecruiters Posting API](https://developers.smartrecruiters.com/docs/endpoints)

Workday 的公开招聘端点与租户配置密切相关，因此这里把它作为可配置的尽力支持适配器，而不是所有公司都完全相同的公开 API。

通用招聘网页会优先读取 `JobPosting` JSON-LD。对于没有 JSON-LD、但 HTML 结构稳定的公开职位列表，可以配置 CSS 选择器：

```yaml
  tier_3:
    - name: Example Utility
      search_url: https://careers.example.ca/jobs
      source_type: generic_html
      options:
        selectors:
          item: article.job-card
          title: h2
          link: a.job-link
          location: .job-location
          description: .summary
          id_attribute: data-job-id
```

不要配置需要登录、绕过 CAPTCHA 或规避反自动化保护的网页。系统只应使用公开 API、网页中的结构化数据或公开 HTML。

## 已停用的旧邮件接入

当前策略不抓取、不自动操作、也不导入 LinkedIn、Indeed、Glassdoor 或 Job Bank 的提醒，并且不会连接 Gmail。为了以后可以恢复，旧适配器和手动命令仍保留在代码中，但公司来源与 IMAP 配置均默认关闭。

现在不需要配置任何邮箱凭据。Dashboard 和 `scripts/run_daily.ps1` 会直接扫描公司官网，绝不会调用邮件同步。

## 添加新的职位来源适配器

1. 在 `src/sources/` 中建立新模块，并创建继承自 `JobSource` 的类。
2. 实现 `fetch_jobs()`，返回 `list[JobCandidate]`。
3. 使用基类提供的共享、限速 HTTP 客户端。
4. 将平台字段统一转换为标准职位字段，不要在适配器中直接写数据库。
5. 在 `src/sources/registry.py` 中注册适配器。
6. 添加使用模拟网络响应和固定样本的测试。

如果多个公司使用同一个招聘平台，应增加公司配置，而不是为每家公司分别编写抓取器。只有真正独特的公开招聘网站才应考虑公司专用适配器。

## 通知和敏感信息

桌面通知默认开启。只有新发现且分数不低于 `urgent_notification_score` 的职位才会触发即时通知，同时每次扫描有通知数量上限。如果 Windows 桌面通知不可用，可以在 `config/settings.yaml` 中关闭。

邮件通知已经实现，但默认关闭。启用方法：

1. 将 `.env.example` 复制为 `.env`。
2. 填写 SMTP 设置。
3. 将 `notifications.email_enabled` 设置为 `true`。

`.env` 和数据库文件已被 Git 忽略。不要把密码、API 密钥或邮件凭据写入 YAML 或 Python 代码。

旧 IMAP 变量和导入的 `.eml` 文件仍会被 Git 忽略，但当前策略不会使用它们。

## Windows 任务计划程序

项目提供的脚本只扫描 20 家已启用的公司官网，不会访问邮箱或聚合招聘网站；它不会自动生成日报，扫描失败时会停止：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\job_search\scripts\run_daily.ps1"
```

配置 Windows Task Scheduler：

1. 打开“任务计划程序”，选择“创建任务”。
2. 在“常规”中把名称设置为 `Job Search Monitor`。如果需要桌面通知，选择“仅当用户登录时运行”。
3. 在“触发器”中添加每日触发时间，例如每天上午 7:00。
4. 在“操作”中选择“启动程序”。
5. 程序填写：`powershell.exe`
6. 参数填写：`-NoProfile -ExecutionPolicy Bypass -File "D:\job_search\scripts\run_daily.ps1"`
7. “起始于”填写：`D:\job_search`
8. 在“设置”中开启“错过计划时间后尽快运行”，并禁止多个任务实例同时运行。
9. 手动点击一次“运行”，然后检查 `logs/job_monitor.log` 和 Dashboard。

当天完成申请后，在 Dashboard 的 **Daily report** 标签中手动生成日报。这样报告中的申请数量会包含你当天所有明确标记为 `APPLIED` 的职位。以下命令行方式仍然保留：

```powershell
python main.py report --print
```

如果移动了项目目录，需要同步修改 `scripts/run_daily.ps1` 中的路径。相同的命令行接口以后也可以由 cron 或 VPS 调用。除非先解决 SQLite 数据库和历史记录的安全持久化问题，否则不建议直接迁移到 GitHub Actions。

## 测试

```powershell
python -m pytest
python -m compileall -q main.py src
```

测试使用临时 SQLite 数据库以及模拟/本地来源，不会抓取真实招聘网站。测试覆盖：

- 职位标准化
- 去重优先级
- 职位更新与历史事件
- 评分逻辑
- 公司优先级
- 地点匹配
- 来源响应解析
- 状态与申请时间
- 完整扫描流程
- 每日报告

## 数据与运行行为

- SQLite 启用外键、WAL 模式和忙等待超时。
- 数据库初始化可重复执行，并记录数据库结构版本。
- 日志以 JSON Lines 格式轮转保存在 `logs/job_monitor.log`。
- 每家公司独立提交；一个来源失败不会中止其他公司的扫描。
- 每个职位独立提交；单个异常职位不会导致同一公司的其他职位丢失。
- 申请统计使用 `applied_date`，只有你明确把状态改为 APPLIED 时才会设置。
- APPLIED 之后再切换到其他状态，不会删除原来的申请时间。
- 历史职位只会关闭，不会删除。

建议定期备份 `data/jobs.db`。备份时最好不要同时进行扫描或通过控制面板写入。数据库中保存了完整的职位发现历史、备注和申请记录。

## 常见问题

### 某个来源没有发现职位

确认公开招聘网址和平台标识符是否正确。Workday 的 tenant 名称经常与公开品牌名不同。对于通用网页，检查职位是否由网页加载后再通过 API 动态获取；如果职位内容根本不在服务器返回的 HTML 中，静态 CSS 选择器无法读取。

### 来源返回 403、429、CAPTCHA 或要求登录

禁用该来源。不要缩短请求间隔，也不要尝试绕过访问控制。优先寻找公司提供的公开 API、职位 Feed，或者手动监控该公司。

### 控制面板无法导入 `src`

请从项目根目录按照文档中的命令启动。`app.py` 也会在直接通过 Streamlit 运行时把项目根目录加入导入路径。

### 桌面通知没有出现

确保运行时用户已经登录，并允许 Python/PowerShell 发送 Windows 通知。也可以设置 `desktop_enabled: false`。通知失败只会写入日志，不会导致扫描失败。

### 两个重复职位没有合并

检查两个来源中的公司名称是否一致。配置中应保留一个标准公司名称，`aliases` 列表可以用于记录别名和以后接入聚合来源。模糊匹配阈值刻意设置得较保守，避免把同一公司在不同城市的两个独立岗位错误合并。

### 职位描述内容太少

某些公开列表 API 只返回简短摘要。对于支持详情请求的适配器，可以开启 `fetch_details`。跨来源合并时，系统会保留已知的较长描述和较早的发布日期。

## 当前范围和后续计划

当前 MVP 保持本地化和可解释性，已经包括数据库与历史记录、来源适配器、评分、去重、命令行、报告、通知、控制面板和测试。

当前目标公司及其公开招聘平台已经接入。后续还可以增加：

- 需要身份验证的邮件或日历集成
- 对 critical 公司更高频率的扫描
- 更丰富的申请分析
- 完全在本地运行的简历技能提取和职位匹配

基础系统不需要付费 API 或付费 LLM。
