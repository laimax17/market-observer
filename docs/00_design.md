# market-observer — 设计文档

> 版本：v1.0 ｜ 状态：DRAFT（已对齐，开始实现）｜ 最后更新：2026-05-31
> 本文档是本项目的权威设计依据。实现严格按本文档；与 `CLAUDE.md` 一并构成项目规则。

---

## 1. 这是什么 / 不是什么

### 1.1 是什么
一个 **read-only 的市场观察 agent**。每日盘前产出一份 watchlist（10 支标的）的市场简报，
推送到 Discord。简报由三部分构成：

- **代码计算的技术指标**（RSI / MACD / 均线 / 波动率等）—— 零 LLM。
- **期权端 EOD 信号**（IV term structure、put/call ratio、IV skew）—— 零 LLM。
- **LLM 写的叙述**：multi-agent 团对以上结构化事实的解读。

目的：把市场状态结构化地呈现给人（项目所有者），**辅助他自己做判断**，不替他做决策。

### 1.2 不是什么（硬边界）
- **不下单**。永远不调用任何交易 API。
- **不输出明确方向 + 置信度数字**。LLM 只对结构化事实写叙述，不给
  `BULLISH / BEARISH / 0.73` 这类伪精确输出。理由见 §6。
- **不与主项目 `agentic_trading_system` 共享代码**。两个仓库各管各的。
- **不背 fail-closed 安全约束**。这是探索性原型，可以快速迭代；它碰不到资金，
  最坏后果是"人读到一个不准的观点"，不是亏钱。
- **不做期权策略 / 不算 GEX 实时 dealer positioning**。免费/延迟数据下做不出，
  不在本期范围（见 §4）。

---

## 2. 与主项目的关系

`agentic_trading_system` 是一个 fail-closed、单写者、事件溯源、契约 FROZEN 的交易核心；
本项目是它之外的独立 sandbox，read-only、可快速迭代。**代码不共享。**

将来若本项目的信号被验证有价值、要接入主交易系统，正路是：
改主项目 `docs/04_intelligence_layer.md` → 走主项目的 T 任务流程重新实现。
**不**把本项目代码直接 import 进主项目。

本项目刻意继承了主项目智能层（`04`）设计里几条经过审查的原则（见 §5、§6），
因为那些原则正好是 multi-agent 看盘场景要避开的坑。

---

## 3. 架构总览

```
        ┌─────────────────────────────────────────────────────────┐
        │  数据层 (纯代码, 零 LLM)                                   │
        │  watchlist 选取 → 行情/期权/宏观/事件抓取 → 指标计算        │
        │  产出: 每支标的的结构化 SymbolSnapshot + 全盘 MacroContext  │
        └───────────────────────────┬─────────────────────────────┘
                                    │ 结构化事实 (不是自然语言)
        ┌───────────────────────────▼─────────────────────────────┐
        │  Agent 层 (multi-agent, 固定 DAG)                         │
        │                                                          │
        │   技术面分析师      期权分析师       宏观分析师            │
        │   (吃全部10支)      (吃全部10支)     (吃宏观上下文)         │
        │        └───────────────┼───────────────┘                 │
        │                        ▼                                 │
        │                  主编 Synthesizer                         │
        │          每支标的一段叙述 + 全盘综述                       │
        │                                                          │
        │   编排器 Orchestrator: 确定性代码, 固定 DAG, 非 LLM        │
        └───────────────────────────┬─────────────────────────────┘
                                    │ Briefing 对象
        ┌───────────────────────────▼─────────────────────────────┐
        │  渲染 + 推送 (纯代码)                                      │
        │  Briefing → markdown → Discord webhook                    │
        └─────────────────────────────────────────────────────────┘
```

**一句话**：代码负责"取数据、算指标"，agent 团只负责"解读"，编排是固定的代码流程，
最后渲染成 markdown 推到 Discord。

---

## 4. 数据层

### 4.1 Watchlist
- 范围：**S&P 500 按 20 日均成交量取前 10**，每周一重新拉取一次。
- 可在 config 里手工 override（追加/固定某些标的，如 SPY/QQQ）。
- 选用成交量前 10 的理由：流动性好、期权链深、信号噪声相对低。

### 4.2 数据源（起步阶段：免费 / 延迟）
| 用途 | 源 | 备注 |
|------|----|----|
| 股票 OHLCV | yfinance | 日级 + 近期 intraday，免费、可能延迟 |
| 期权链 (EOD) | yfinance options chain | 行权价 / IV / OI / volume |
| 宏观 | yfinance（^VIX / DX-Y.NYB / ^TNX / CL=F / GC=F） | VIX、美元指数、10Y、油、金 |
| 事件 | yfinance（earnings / dividends 日历） | 临近财报/除息提示 |

> **后果（必须始终牢记）**：信号有延迟、深度有限。GEX / 盘中 unusual flow 做不出。
> 本期只做**日级信号**。任何"看上去能直接下单"的冲动都要警惕。
> 数据源抽象成接口，将来换 Polygon Options 只换实现，不改上层。

### 4.3 技术指标（代码计算，纯函数）
- 趋势：SMA(20/50/200)、价格相对均线位置、均线多空排列。
- 动量：RSI(14)、MACD(12/26/9)。
- 波动率：已实现波动率（20 日）、ATR(14)、近 N 日区间位置。
- 量：相对成交量（当日 vs 20 日均量）。

### 4.4 期权信号（代码计算）
- **IV term structure**：近月 ATM IV vs 次月/季月 ATM IV，判断倒挂（临近事件定价）。
- **Put/Call ratio**：成交量与未平仓量两个口径。
- **IV skew**：同到期 OTM put IV − OTM call IV（下行保护需求）。
- **ATM IV 水平**与其历史分位（数据足够时）。

> 期权信号只在标的有足够期权链深度时计算；不足则标注"数据不足"，不硬造。

---

## 5. Agent 层

### 5.1 Agent 清单
| Agent | 用 LLM | 职责 | 输入 |
|-------|--------|------|------|
| 技术面分析师 | 是 | 解读全部 10 支的技术面结构化事实 | 10 支的技术指标 |
| 期权分析师 | 是 | 解读全部 10 支的期权端信号 | 10 支的期权信号 |
| 宏观分析师 | 是 | 解读当日宏观环境（VIX/利率/美元/商品） | MacroContext |
| 主编 Synthesizer | 是 | 合成：每支标的一段叙述 + 全盘综述 | 上述三者的输出 |
| 编排器 Orchestrator | **否（纯代码）** | 固定 DAG 调度，收集输出 | — |

### 5.2 固定 DAG
```
数据层产出
   ├─► 技术面分析师 ─┐
   ├─► 期权分析师   ─┼─► 主编 Synthesizer ─► Briefing ─► 渲染 ─► Discord
   └─► 宏观分析师   ─┘
```
- 三个专科 agent 可并行（无依赖）；主编依赖三者全部完成。
- **每个专科 agent 一次吃下全部 10 支的本领域数据**，不是按股票循环。
  => 每份简报 LLM 调用次数固定为 **4 次**（3 专科 + 1 主编），与标的数无关。
- 编排器是 Python 代码里的固定流程，**不是 LLM**，不允许"让某个 agent 决定下一步叫谁"
  （防死循环 + 成本不可控）。
- 工作流必须可终止：固定步数，无 LLM 驱动循环。

### 5.3 非确定性边界
- 所有 LLM 调用：低温度、强制结构化/受约束输出、超时上限、重试上限（默认 2 次）。
- 格式不合规重试上限内失败 → 该 agent 输出标记为"不可用"，简报照常出（用占位说明），
  不卡死整条流程。
- LLM 厂商不可用 → 降级：只出"纯数据简报"（数据层 + 渲染），不带叙述。

---

## 6. 为什么不给"方向 + 置信度"（继承主项目 §6 的判断）

- **LLM 自报置信度不是校准概率**。直接拿 0.6 当阈值，是用未标定的仪表做判断。
  校准需要历史回测建立"自报置信度 → 实际命中率"映射，本期没有，故不输出该数字。
- **"引用真实数据" ≠ "推理正确"**。强制 agent 引用真实指标只能抓"捏造数据"，
  抓不住"用真数据推出错结论"。CoT 不增加正确性，只让错误更长更有说服力。
- 因此 agent 输出定位为**解读叙述**：陈述"看到了什么信号、通常如何理解、需要留意什么"，
  把判断权留给人。这也避免使用者不自觉地信任一个伪精确数字而被带偏。

---

## 7. 渲染 + 推送
- `Briefing` 对象 → markdown。结构：日期/时间戳 → 全盘综述 → 宏观快照 → 每支标的卡片
  （技术面数据表 + 期权信号 + 主编叙述）→ 免责声明（"非交易建议、数据可能延迟"）。
- 推送：Discord webhook。Discord 单条消息有长度上限，超长按标的分段发送。
- 同时在本地 `output/` 留一份 markdown 存档（已 gitignore）。

---

## 8. 模块结构

```
market-observer/
  pyproject.toml            # uv 项目
  .env.example              # DEEPSEEK_API_KEY / DISCORD_WEBHOOK_URL / 配置
  docs/00_design.md         # 本文档
  src/market_observer/
    config.py               # pydantic-settings
    domain/
      models.py             # SymbolSnapshot/OptionsSignal/MacroContext/EventInfo
                            # AgentInput/AgentOutput/Briefing
      indicators.py         # 技术指标纯函数 (RSI/MACD/MA/RV/ATR...)
      options_math.py       # 期权信号纯函数 (term structure/skew/pc ratio)
    data/
      watchlist.py          # S&P500 top10 by volume
      market_data.py        # yfinance OHLCV → 指标输入
      options_data.py       # yfinance 期权链 → 期权信号
      macro.py              # 宏观抓取
      events.py             # 财报/除息日历
      provider.py           # 数据源接口抽象 (将来换 Polygon)
    agents/
      base.py               # Agent 抽象基类
      llm_client.py         # LLMClient 抽象 + DeepSeekClient
      technical_agent.py
      options_agent.py
      macro_agent.py
      synthesizer.py
      orchestrator.py       # 确定性固定 DAG
    render/markdown.py
    notify/discord.py
    run_briefing.py         # 入口
  tests/                    # 每个模块对应测试
  crontab.example           # 每日盘前定时
```

---

## 9. 任务路线图

| 任务 | 内容 | DoD |
|------|------|-----|
| **T-01** | 项目骨架：pyproject(uv)、目录、config、.env.example、ruff/pytest、空测试 | CI/本地 pytest 跑通空测试；ruff 通过 |
| **T-02** | domain 模型：所有 Pydantic 数据模型 | 模型有校验；序列化往返单测 |
| **T-03** | 技术指标 `indicators.py` | 对已知输入的指标值有单测（穷举边界） |
| **T-04** | 期权信号 `options_math.py` | term structure/skew/pc ratio 纯函数 + 单测 |
| **T-05** | 数据源：watchlist + market_data + provider 接口 | 选股逻辑单测；抓取走 mock 单测 |
| **T-06** | 数据源：options_data + macro + events | mock 单测；数据不足时优雅降级 |
| **T-07** | LLM client：抽象 + DeepSeekClient | mock HTTP 单测；超时/重试/降级路径有测 |
| **T-08** | Agent 层 + 编排器 | mock LLM 单测：DAG 顺序固定、可终止、单 agent 失败不拖垮 |
| **T-09** | 渲染 markdown | 对样例 Briefing 渲染输出有快照测 |
| **T-10** | Discord 推送 | mock webhook 单测；超长分段逻辑有测 |
| **T-11** | 端到端入口 run_briefing + crontab.example | 用 mock 数据源 + mock LLM 跑通整链；真实 key 留待补 |

> 真实 DeepSeek key 与 Discord webhook 由项目所有者后续注入 `.env`；
> T-07/T-10/T-11 的真实联网验证在 key 就位后进行，代码与 mock 测试先行完成。

---

## 10. 风险 / 注意事项

- **R-1（数据）**：免费源可能返回延迟/陈旧/缺失数据。期权链在冷门标的上稀疏。
  策略：数据不足显式标注"数据不足"，不硬造信号；简报头部统一声明"数据可能延迟"。
- **R-2（信号有效性）**：期权信号（尤其散户可见的 put/call、unusual activity）预测力弱且不稳定。
  本项目定位是"呈现+辅助"，不是"alpha 源"。不得据此实盘。
- **R-3（multi-agent ≠ 更准）**：LLM 互查有相关性失败。本项目未设质疑 agent；
  即便设了也只缓解不消除。叙述是参考，不是保证。
- **R-4（成本）**：固定 4 次 LLM 调用/简报，10 支标的，成本可控；
  但若将来扩标的或加质疑 agent，需重新评估。DeepSeek 便宜、上下文大，适合本期。
- **R-5（边界侵蚀）**：最大的风险是这个"辅助工具"逐渐被当成"信号源"去实盘。
  §1.2 的硬边界（不下单、不给方向+置信度、不接主项目）是防这个的，不得弱化。
```
