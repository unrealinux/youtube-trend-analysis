---
target: templates/index.html
total_score: 23
max_score: 40
na_heuristics: 
p0_count: 1
p1_count: 2
target_identity: "file:/Users/xieyingfei/Documents/youtube-trend-analysis/templates/index.html"
target_fingerprint: "sha256:62d1a715366511a96f532d50e171a242758dd3812620c9c0e95bc291b286c97a"
target_path: /Users/xieyingfei/Documents/youtube-trend-analysis/templates/index.html
timestamp: 2026-11-04T04-51-58Z
slug: templates-index-html
---
## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 3 | 有全局 loading 与错误 alert，但长扫描无进度、配额消耗无回执 |
| 2 | Match System / Real World | 3 | 术语贴合（播/天、头部占比），判定阈值只在图例一行里 |
| 3 | User Control and Freedom | 2 | 扫描进行中无法取消；无撤销；缓存清理只能走 API |
| 4 | Consistency and Standards | 2 | 结果区位置不统一（面板内 vs 面板下），"分析 →"只出现在部分表 |
| 5 | Error Prevention | 3 | 三个高消耗入口有配额确认，跨域/参数校验到位 |
| 6 | Recognition Rather Than Recall | 3 | Tab 常驻、历史可点，但切换 Tab 后看不到上一轮参数 |
| 7 | Flexibility and Efficiency | 2 | 无键盘快捷键；面板参数不进 URL；无批量导出 |
| 8 | Aesthetic and Minimalist Design | 2 | 9 个 Tab 同权重平铺；历史 chip 噪音大 |
| 9 | Error Recovery | 2 | alert() 弹原始 detail，技术上可读，用户不可行动 |
| 10 | Help and Documentation | 1 | 应用内无帮助，只有一行图例 |
| **Total** | | **23/40** | **Acceptable（50–69%）** |

## Design Specificity Verdict

**LLM 评估**：数据模型是产品专属的（判定、头部占比、集中度），但**外壳是品类通用的**——深色卡片 + 红色强调 + 平铺 Tab，换成任何 YouTube 分析工具都成立。判定是本产品最独特的资产，却只以一个彩色词出现在表格里，没有成为版面的主角。最大的机会：让"判定"驱动版面，而不是让它藏在第 2 列。

**确定性扫描**：31 条，全部集中在颜色与一处阴影。
- 30 条 `low-contrast`：占位符 `#666 on #2a2a2a` = 2.5:1；正文 `#666 on #0f0f0f` = 3.3:1；`#fff on #ff0000` = 4.0:1（主按钮与激活 Tab）
- 1 条 `dark-glow`：深色页上的红色 box-shadow

扫描没有发现结构问题——结构问题由 DOM 实测补上：30 个 `<label>` **0 个**带 `for`；`aria-*`/`role` **0 个**；27 个按钮里 **12 个**高度 < 44px；整份 CSS 只有 **1 条** `:focus` 规则。

**视觉叠加**：已注入检测脚本（6 处高亮），live-server 已停止。叠加只覆盖对比度问题，布局与无障碍问题需看下面实测数据。

## Overall Impression

桌面端是干净、克制的工具界面，判定与配额确认体现了真实的产品思考。但它在两件事上还没兑现自己的第一条原则：**移动端基本不可用**，**结果页不给结论**。前者是 P0，后者是产品定位问题——你定义了"每个视图都要以决定收尾"，而现在的视图以一张表收尾。

## What's Working

1. **配额前置确认**——三个高消耗入口会先告诉你花多少单位，这在同类工具里少见，直接对应"配额是预算"的产品原则。
2. **判定 + 原因的白盒设计**——`值得做/观望/过宽/避开` 每条都带人话理由，没有黑箱分数。
3. **桌面端信息密度合适**——统计卡、表格、图表三层递进，扫读成本低。

## Priority Issues

**[P0] 移动端布局崩坏**
- Why：390px 视口下 `body.scrollWidth` = 588px，横向溢出 198px；Tab 文字被压成竖排（`.tab` 没有 `white-space: nowrap`，Tab 容器不可滚动），9 个 Tab 里只能看到 4 个半。
- Fix：Tab 栏加 `overflow-x:auto` + `.tab{white-space:nowrap;flex:0 0 auto}`；`.form-row` 在窄屏改为单列；所有 `.batch-table` 包一层 `overflow-x:auto`。
- Suggested command：`/impeccable adapt`

**[P1] 结果页不给结论**
- Why：产品原则第 1 条要求每个视图以决定收尾，但扫描后只有表格，用户得自己比三列才能得出结论；这正是你之前手动绕了几轮才得到的判断。
- Fix：每个结果区顶部加一行结论横幅，例如"最值得做：AI编程（互动 2.38%）· 避开：音乐（互动 0.61%）"，由现有 verdict 字段直接生成。
- Suggested command：`/impeccable shape`

**[P1] 表单与状态对辅助技术不可见**
- Why：30 个 label 没有一个关联到 input，屏幕阅读器读不出字段名；0 个 `aria-live`，异步结果加载完成后不会被播报；判定只用颜色区分。
- Fix：补 `for`/`id`；结果容器加 `role="status" aria-live="polite"`；判定除颜色外加图标或文字前缀。
- Suggested command：`/impeccable audit`

**[P2] 对比度系统性不足**
- Why：占位符 2.5:1、次要文字 3.0–3.3:1、白字红底 4.0:1，全部低于 AA 4.5:1；长时间使用会明显费眼。
- Fix：把 `#666` 提到 `#9a9a9a`（约 4.6:1），主按钮底色 `#ff0000` 改 `#c40000` 或文字改深色。
- Suggested command：`/impeccable colorize`

**[P3] 历史 chip 噪音**
- Why：当前记录里 `#shorts` 出现 3 次，还有 `#shorts`/`Yt shorts` 这类无意义查询；chip 不可单条删除。
- Fix：写入前去重并过滤空泛词；每个 chip 加删除。
- Suggested command：`/impeccable polish`

## Persona Red Flags

**Alex（数据型高级用户）**：没有任何键盘快捷键；切换 Tab 后参数清空，必须重填；导出按钮是全局的，但只有关键词搜索的结果进 `currentData`，其他面板导出前要先回关键词页跑一次。60 秒内完成"挖赛道→验证"的路径不存在。

**Sam（辅助技术用户）**：Tab 到输入框时听不到字段名（label 未关联）；提交搜索后没有 `aria-live` 播报"20 条结果"；判定色 block 对色盲不可分辨；主按钮白字红底 4.0:1 不达标。

**Casey（手机上一个手操作）**：Tab 竖排且横向溢出，切到"频道榜单"要先横向滑 198px；"搜索"按钮在表单最上方，拇指需要跨屏；12 个按钮触控高度不足 44px。

## Minor Observations

- 桌面端 6 张统计卡在 5 列网格里，第 6 张单独掉到下一行，留下一个孤立的卡片。
- 空状态用大号电视 emoji 居中枢，与结果区的紧凑排布是两套语气。
- `favicon` 现在是 `data:,`，标签页图标为空白。
- 选项卡激活态用纯红块，与"主按钮"抢同一个视觉层级。

## Questions to Consider

- 如果"判定"是产品的核心资产，为什么它只是表格里的第二列，而不是页面的第一屏？
- 手机端你实际会用吗？如果用，现在等于没有移动端。
- 九个 Tab 是九个工作流，还是可以合并成"找赛道 / 验赛道 / 看频道"三个？
- 如果结果页只允许保留一句话，那句话是什么？
