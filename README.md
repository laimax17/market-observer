# market-observer

只读的盘前观察 Agent。每个交易日早晨为一个 10 只股票的观察清单生成一份**盘前简报**，并推送到你的 Discord。

它会做三件事：

- **代码计算技术指标**（RSI、MACD、均线、波动率、ATR、区间位置、相对成交量）
- **EOD 期权信号**（近月/次月 ATM 隐含波动率、IV 期限结构是否倒挂、Put/Call 比、IV skew）
- **多智能体 LLM 分析团**（技术面 / 期权 / 宏观 三位专科分析师 + 一位主编），把上面这些结构化事实写成一段可读的叙事

它**不下单**、**不输出"方向 + 置信度"数字**、**不碰任何真实交易系统**。数据来自免费源（yfinance），可能延迟，仅供研究参考。

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
                         │  4. 渲染 render_briefing → Markdown          │
                         └───────────────────┬─────────────────────────┘
                                             ▼
                    ┌────────────────────────┴────────────────────────┐
                    ▼                                                  ▼
       ┌────────────────────────┐                      ┌──────────────────────────┐
       │ 5a. 存盘                │                      │ 5b. 推送 Discord          │
       │ output/briefing_日期.md │                      │ 按2000字上限自动分段(chunk)│
       └────────────────────────┘                      │ 逐段 POST 到 webhook       │
                                                        └──────────────────────────┘
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

**推送行为：** 简报是 Markdown，可能上千字；Discord 单条消息上限 2000 字。程序会自动按行边界把内容切成 ≤1900 字的若干段（`chunk_text`，留了余量），逐段 POST。所以一份简报到你手机上可能是连续几条消息。任何一段失败会记日志，但**已存到本地的 `.md` 不受影响**。

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

## 6. 测试

```bash
uv run ruff check .   # 静态检查
uv run pytest -q      # 全套单测（83 个，用 FakeProvider + ScriptedLLM，不需要任何真实凭证/网络）
```

整条链路都用注入式的假 Provider 和假 LLM 测过，所以无凭证、无网络也能完整验证逻辑。

---

## 7. 目录结构

```
src/market_observer/
  config.py              # 环境变量 → Settings
  pipeline.py            # 全链路装配（可测，无网络/LLM 构造）
  run_briefing.py        # 薄入口：构造真实 Provider/LLM/Discord，跑 + 存 + 推
  domain/                # 纯计算：models / indicators / options_math
  data/                  # Provider 协议 + yfinance 实现 + 选股/行情/期权/宏观/事件
  agents/                # LLM 客户端 + 3专科 + 主编 + orchestrator(固定DAG)
  render/markdown.py     # Briefing → Markdown
  notify/discord.py      # 分段 + webhook 推送
docs/00_design.md        # 权威设计文档
crontab.example          # 定时任务示例
```

---

## 8. 与 `agentic_trading_system` 的关系

这是**有意分开**的沙箱项目。主项目 `agentic_trading_system` 是 fail-closed、单写者、事件溯源的交易核心，契约已冻结；本项目是探索性的、只读的，可能快速演进。**两者不共享代码。**

如果这里的信号被证明有价值，集成路径是走主系统的 `04_intelligence_layer` 设计——而不是直接 link 代码。详见 [`docs/00_design.md`](docs/00_design.md)。
