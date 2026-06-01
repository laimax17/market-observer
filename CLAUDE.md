# CLAUDE.md — market-observer

> 这是一个 read-only 的市场观察 sandbox 项目，与主交易系统
> `agentic_trading_system` 完全独立。

## 这个项目是什么
每日盘前产出一份 watchlist（10 支）的市场简报，推送到 Discord。
内容包括：
- 代码计算的技术指标（RSI/MACD/MA 等）
- 期权端 EOD 信号（IV term structure、put/call、IV skew）
- multi-agent LLM 团（技术面/期权/宏观 三专科 + 主编）写的叙述

> 权威设计见 docs/00_design.md。实现严格按该文档（任务 T-01..T-11）。

## 这个项目不是什么
- **不下单**。永远不调用任何交易 API。
- **不输出明确方向 + 置信度**。LLM 只对结构化事实写叙述，
  不给 BULLISH/BEARISH/0.73 这种伪精确数字。
- **不与主项目共享代码**。两个仓库各管各的。
- **不背 fail-closed 安全约束**。这是探索性原型，可以快速迭代。

## 数据源策略（起步阶段）
- 股票行情：免费源（yfinance / Alpaca paper account）
- 期权数据：免费源（yfinance EOD chain 等）
- **后果**：信号有延迟、深度有限。GEX/盘中 unusual flow 不在范围。
  能做的是日级信号。任何"看上去能直接下单"的冲动都要被警惕。

## 工作方式
- 全程中文交流。
- 一次只做一个任务，做完先停下来报告，等明确"继续"再下一个。
- 每个任务通过后做一次 git commit。
- 不实现没讨论过的功能。

## 与主项目的关系
将来若信号有价值并要接入主交易系统，路径是：
改主项目 docs/04_intelligence_layer.md → 走主项目的 T 任务流程实现。
**不**把本项目代码直接 import 进主项目。
