# market-observer

只读的盘前观察 Agent。每个交易日早晨为一个 10 只股票的观察清单生成一份**盘前简报**，并推送到你的 Discord。

它会做三件事：

- **代码计算技术指标**（RSI、MACD、均线、波动率、ATR、区间位置、相对成交量）
- **EOD 期权信号**（近月/次月 ATM 隐含波动率、IV 期限结构是否倒挂、Put/Call 比、IV skew、期权隐含波动幅度）
- **多智能体 LLM 分析团**（技术面 / 期权 / 宏观 三位专科分析师 + 一位主编），为每只标的写出四段式解读（📌近况 / 🔍归因 / 🔭预测），把结构化事实 + 真实新闻标题写成可读叙事

它会给**方向 + 粗略概率 + 目标价区间**，但这是**研究观点不是交易信号**：目标价锚定代码算出的关键价位（期权隐含 1σ / ATR / 均线），不凭空捏造；概率是模型粗略估计、非校准概率。它**永不下单、永不接入任何真实交易账户、不碰主交易系统**。数据来自免费源（yfinance），可能延迟，仅供研究参考，请独立判断、自负盈亏。

---

## 1. 全链路是怎么跑的

一次 `run_briefing` 从头到尾经过这几步（全部在 `src/market_observer/pipeline.py` 里串起来）：

```
                         ┌─────────────────────────────────────────────┐
   定时触发(cron)         │  1. 选股 build_watchlist                      │
   或手动运行     ───────►│     从真实 S&P500 (~503只) 按20日均量排序，     │
                         │     取前 N 只；MO_PINNED_SYMBOLS 固定置顶      │
                         └───────────────────┬─────────────────────────┘
                                             ▼
                         ┌─────────────────────────────────────────────┐
                         │  2. 装配数据 assemble_briefing_data          │
                         │     对每只股票：                              │
                         │       · 历史行情 → 技术指标                    │
                         │       · 期权链   → 期权信号                    │
                         │       · 事件     → 距财报天数                  │
                         │     再拉一次宏观快照(VIX/美元/10Y/原油/黄金)    │
                         └───────────────────┬─────────────────────────┘
                                             ▼
              ┌──────────────────────────────────────────────────────────┐
              │  3. 多智能体 DAG  run_briefing (固定顺序，非LLM路由)        │
              │                                                            │
              │     数据 ──► 技术面分析师 ─┐                                │
              │          ──► 期权分析师   ─┼─► 主编(综合) ─► 叙事            │
              │          ──► 宏观分析师   ─┘                                │
              │                                                            │
              │     固定 4 次 LLM 调用（每个领域把10只股票打包成1次，         │
              │     不是每股一次，所以成本和股票数无关）                      │
              └───────────────────┬──────────────────────────────────────┘
                                  ▼
                         ┌─────────────────────────────────────────────┐
                         │  4. 双渲染器（共享 render/format.py 格式真源）│
                         └───────────────────┬─────────────────────────┘
                                             ▼
                    ┌────────────────────────┴────────────────────────┐
                    ▼                                                  ▼
       ┌────────────────────────┐                      ┌──────────────────────────┐
       │ 5a. 存盘 (markdown)     │                      │ 5b. 推送 Discord (embeds) │
       │ output/briefing_日期.md │                      │ 彩色卡片，按≤10卡/≤6000字 │
       │ 右对齐表格 + 四段式解读 │                      │ 分批 send_embeds 逐批POST │
       └────────────────────────┘                      └──────────────────────────┘
```

### 优雅降级（任何一环挂了都不会整体失败）

| 情况 | 行为 |
|---|---|
| 没配 `MO_DEEPSEEK_API_KEY` | 跑**纯数据简报**：只有指标和期权信号，没有叙事 |
| 某只股票缺数据 | 该项显示 `—`，绝不编造数字 |
| 某位分析师 LLM 调用失败 | 标记该 agent 不可用，简报照常出，叙事退回其他专科的笔记 |
| 主编失败 | 简报保留纯数据 + 各专科分项笔记 |
| 抓 S&P500 成分股失败 | 退回内置约 40 只大盘股 fallback 名单 |
| 没配 `MO_DISCORD_WEBHOOK_URL` | 只存盘，不推送 |

---

## 2. 消息怎么推送给你（Discord 设置）

简报是通过 **Discord Webhook** 推给你的——这是 Discord 自带的、最省事的推送方式，**不需要做机器人、不需要 OAuth**。

**怎么拿到 webhook URL：**

1. 在 Discord 里建一个频道（或用现有的），比如 `#盘前简报`
2. 频道名右键 → **编辑频道 → 整合 (Integrations) → Webhook → 新 Webhook**
3. 点 **复制 Webhook URL**，长这样：
   `https://discord.com/api/webhooks/123456.../abcdef...`
4. 把它填进 `.env` 的 `MO_DISCORD_WEBHOOK_URL`

**推送行为：** Discord 不渲染 Markdown 表格，所以推送用的是**原生彩色 embeds 卡片**：一张总览卡（今日看点 + 全盘综述 + 宏观），加每只标的一张卡（按强/弱/中性着色、指标排成 inline 字段、四段式解读放在卡片描述里）。Discord 限制单条消息 ≤10 张卡且 ≤6000 字，程序用 `batch_embeds` 自动分批，再用 `send_embeds` 逐批 POST。所以一份简报到你手机上可能是连续几条消息。任何一段失败会记日志，但**已存到本地的 `.md` 不受影响**。（纯文本 `send` 仍保留作兜底。）

> 手机上你在 Discord App 里就能直接看到，无需开电脑。

---

## 3. 所有配置项（环境变量）

所有配置通过环境变量注入，前缀 `MO_`。本地开发把 `.env.example` 复制成 `.env` 填好即可（`.env` 已在 `.gitignore` 里，**不会进仓库**）。

```bash
cp .env.example .env
# 然后编辑 .env
```

| 变量 | 必填? | 默认值 | 说明 |
|---|---|---|---|
| `MO_DEEPSEEK_API_KEY` | 选填 | 无 | DeepSeek API Key。不填则跑纯数据简报（无叙事） |
| `MO_DEEPSEEK_BASE_URL` | 选填 | `https://api.deepseek.com` | LLM 接口地址（OpenAI 兼容） |
| `MO_DEEPSEEK_MODEL` | 选填 | `deepseek-chat` | 模型名 |
| `MO_DISCORD_WEBHOOK_URL` | 选填 | 无 | Discord webhook。不填则只存盘不推送 |
| `MO_PINNED_SYMBOLS` | 选填 | `SPY,QQQ` | 永远固定进观察清单的代码（逗号分隔），通常放指数 ETF |
| `MO_WATCHLIST_SIZE` | 选填 | `10` | 观察清单总数（pinned 也计入这个数） |
| `MO_LLM_TEMPERATURE` | 选填 | `0.2` | LLM 采样温度，越低越稳 |
| `MO_LLM_TIMEOUT_SECONDS` | 选填 | `60` | 单次 LLM 调用超时 |
| `MO_LLM_MAX_RETRIES` | 选填 | `2` | LLM 调用失败重试次数 |
| `MO_OUTPUT_DIR` | 选填 | `output` | 本地 Markdown 存档目录 |

> 全是"选填"——**完全不配也能跑**（纯数据 + 只存盘）。配上 LLM key 得到叙事，再配上 webhook 才会推到 Discord。

---

## 4. 快速开始

需要 [uv](https://docs.astral.sh/uv/)（Python 包管理器）和 Python ≥ 3.12。

```bash
# 1. 安装依赖
uv sync

# 2. 配置（可选，详见上面第3节）
cp .env.example .env && vi .env

# 3. 跑一次
uv run python -m market_observer.run_briefing
```

跑完后：

- 简报存在 `output/briefing_<日期>.md`
- 配了 webhook 的话，同时推到了你的 Discord

**先试纯数据（不用任何 key）：** 直接执行第 3 步即可，会生成只含指标和期权信号的简报，用来验证数据链路。

---

## 5. 挂成每天自动跑（cron）

仓库里有 [`crontab.example`](crontab.example)。它在**工作日早晨美股开盘前**跑一次：

```cron
# 周一至五 12:00 UTC（约等于美东 08:00），按你服务器时区调整
0 12 * * 1-5  cd /path/to/market-observer && uv run python -m market_observer.run_briefing >> output/cron.log 2>&1
```

安装：把里面的路径改成你的实际路径，然后 `crontab crontab.example`（或 `crontab -e` 粘进去）。日志会追加到 `output/cron.log`，手机上也能 tail 看。

---

## 6. 在 GitHub 上 fork 并自动跑（推荐：不用自己开机器）

不想让简报依赖你自己的电脑/服务器常开机，最省事的方式是 **fork 这个仓库，让 GitHub Actions 在云端每天定时帮你跑**。仓库里已带好工作流 [`.github/workflows/briefing.yml`](.github/workflows/briefing.yml)，fork 过去开箱即用。

**步骤：**

1. **Fork**：打开 `https://github.com/laimax17/market-observer`，点右上角 **Fork**，复制到你自己的账号下（比如 `你的用户名/market-observer`）。

2. **打开 Actions**：fork 出来的仓库默认会禁用 Actions。进 fork 仓库的 **Actions** 标签页，点 **“I understand my workflows, go ahead and enable them”** 启用。

3. **配置密钥（Secrets）**：进 **Settings → Secrets and variables → Actions → New repository secret**，添加两个（都可选，缺了会优雅降级）：
   | Secret 名 | 值 |
   |---|---|
   | `MO_DEEPSEEK_API_KEY` | 你的 DeepSeek API key（缺了 → 只出纯数据简报，无叙事） |
   | `MO_DISCORD_WEBHOOK_URL` | 你的 Discord webhook（缺了 → 只存档不推送） |

   > ⚠️ 密钥**只**放在这里（GitHub 加密保存），**绝不要**写进代码或 `.env.example` 提交上去。

4. **给工作流写权限**：工作流跑完会把当天简报 commit 回 `briefings/` 目录，需要写权限。进 **Settings → Actions → General → Workflow permissions**，选 **“Read and write permissions”** 并保存（否则最后那步 `git push` 会失败）。

5. **手动试跑一次**：进 **Actions → 左侧 `daily-briefing` → 右侧 “Run workflow”** 手动触发。绿勾即成功；这时你的 Discord 应该收到推送，`briefings/` 里也多出一份 `briefing_日期.md`。

6. **之后自动跑**：工作流已设定 **周一至五 12:00 UTC（≈ 美东 08:00）** 自动触发（GitHub 定时可能延迟最多 ~15 分钟，盘前无所谓）。想改时间或观察清单，编辑 `briefing.yml` 里的 `cron` 和 `MO_PINNED_SYMBOLS` / `MO_WATCHLIST_SIZE` 即可。

**和 cron 方式的取舍**：GitHub Actions 免费额度对每天一跑绰绰有余、不用自己维护机器；缺点是定时不精确（可接受）、简报会以 commit 形式存在你的公开 fork 里（不想公开就把 fork 设为 private）。

---

## 7. 测试

```bash
uv run ruff check .   # 静态检查
uv run pytest -q      # 全套单测（107 个，用 FakeProvider + ScriptedLLM，不需要任何真实凭证/网络）
```

整条链路都用注入式的假 Provider 和假 LLM 测过，所以无凭证、无网络也能完整验证逻辑。

---

## 8. 目录结构

```
src/market_observer/
  config.py              # 环境变量 → Settings
  pipeline.py            # 全链路装配（可测，无网络/LLM 构造）
  run_briefing.py        # 薄入口：构造真实 Provider/LLM/Discord，跑 + 存 + 推
  domain/                # 纯计算：models / indicators / options_math / forecast(价位+近期收益)
  data/                  # Provider 协议 + yfinance 实现 + 选股/行情/期权/宏观/事件/新闻
  agents/                # LLM 客户端 + 3专科 + 主编 + orchestrator(固定DAG)
  render/format.py       # 双渲染器共享的格式真源（数字/强弱标签+颜色/今日看点）
  render/markdown.py     # Briefing → Markdown（存档）
  render/discord.py      # Briefing → 彩色 embeds 卡片（推送）
  notify/discord.py      # embeds 分批 + webhook 推送（含文本兜底）
docs/00_design.md        # 权威设计文档
crontab.example          # 定时任务示例
```

---

## 9. 与 `agentic_trading_system` 的关系

这是**有意分开**的沙箱项目。主项目 `agentic_trading_system` 是 fail-closed、单写者、事件溯源的交易核心，契约已冻结；本项目是探索性的、只读的，可能快速演进。**两者不共享代码。**

如果这里的信号被证明有价值，集成路径是走主系统的 `04_intelligence_layer` 设计——而不是直接 link 代码。详见 [`docs/00_design.md`](docs/00_design.md)。
