# YouTube 数据分析平台

基于 FastAPI + YouTube Data API v3 的 YouTube 视频（含 Shorts）数据分析工具。

## 功能特性

### 核心功能
- ✅ **关键词搜索** - 按关键词搜索视频并统计播放量/互动率
- ✅ **Shorts 分析** - 时长过滤后的 Shorts 数据（含 1–3 分钟长 Shorts）
- ✅ **频道分析 / 频道洞察** - 按频道 ID 分析视频与 Shorts 表现
- ✅ **对比分析** - 对比当前关键词与另一组关键词的流量表现
- ✅ **批量扫描** - 多关键词并发扫描并按平均播放量排序
- ✅ **热门类别** - 扫描预置类别，找出高播放量赛道
- ✅ **特征分析** - 标题长度、时长区间、发布时段、成功评分
- ✅ **趋势追踪** - 多次快照记录关键词趋势变化（快照与判定基于播放速度，可强制刷新，也可后台自动快照）
- ✅ **频道搜索** - 按名称搜频道并显示订阅/视频数，一键跳到频道分析
- ✅ **数据可视化** - Chart.js 图表展示播放量、互动率、趋势
- ✅ **搜索历史** - SQLite 持久化，前端"最近搜索"读取后端
- ✅ **配额监控** - 按实际 API 调用记账（search.list 100 单位 / 次，videos.list 1 单位 / 次）
- ✅ **数据导出** - 支持 JSON 和 CSV（CSV 带 BOM，Excel 直接打开不乱码）

### 技术特性
- 🚀 **缓存优化** - TTL 缓存（5 分钟）+ 容量上限，减少 API 调用
- 📊 **统计分析** - 播放量、点赞、评论、分位数、时长分桶等
- 🏷️ **标签云** - 热门标签可视化
- 🌙 **主题切换** - 深色/浅色主题
- 📱 **响应式设计** - 支持移动端浏览

## 安装

### 1. 获取 YouTube API Key
1. 访问 [Google Cloud Console](https://console.cloud.google.com/)
2. 创建项目并启用 YouTube Data API v3
3. 生成 API Key

### 2. 安装依赖
```bash
pip install -r requirements.txt
```

### 3. 配置环境变量
```bash
cp .env.example .env
# 编辑 .env，填入你的 API Key
```

| 变量 | 必填 | 说明 |
|------|------|------|
| `YOUTUBE_API_KEY` | 是 | YouTube Data API v3 的 Key |
| `HTTP_PROXY` / `HTTPS_PROXY` | 否 | 所在网络无法直连 googleapis.com 时设置代理 |
| `YT_HISTORY_DB` | 否 | SQLite 路径，默认 `youtube_history.db`（目录不存在会自动创建） |
| `CORS_ORIGINS` | 否 | 允许跨域的来源，逗号分隔；默认已含 8000/8001 端口的 localhost |
| `QUOTA_TIMEZONE` | 否 | 配额日切时区，默认 `America/Los_Angeles`（YouTube 按太平洋时间重置） |
| `MAX_CONCURRENT_SCANS` | 否 | 批量扫描/热门类别的并发上限，默认 `8` |
| `TREND_SNAPSHOT_INTERVAL_HOURS` | 否 | 自动趋势快照间隔小时，默认 `24`；`0` 关闭 |
| `TREND_SNAPSHOT_MAX_KEYWORDS` | 否 | 每轮自动快照的关键词上限，默认 `10` |

### 4. 先验证 Key 能否用（可选，推荐）
```bash
python3 test_api.py
```
会实际调用 `search.list` 和 `channels.list` 各一次，报告 Key 是否有效、
网络是否可达、配额是否耗尽。**注意 `search.list` 每次消耗 100 单位、
每天上限 100 次**，所以这个脚本本身也会花掉配额。

## 运行

```bash
python3 -m uvicorn app.main:app --reload --port 8001
```
或直接 `python3 main.py`（内部同样是 8001 + reload）。

打开浏览器访问：**http://localhost:8001**

> README 早期版本写的是 8000 端口，但 `main.py` 和 CORS 默认值都用 8001。
> 两个端口现在都在 CORS 白名单里，但建议统一用 8001。
> 如需换端口，同时把新来源加进 `CORS_ORIGINS`。

需要代理时不要改代码，在 `.env` 里写即可（`app.config.PROXY_URL` 会读取它）：
```
HTTP_PROXY=http://127.0.0.1:10809
HTTPS_PROXY=http://127.0.0.1:10809
```

## API 端点

所有接口都在 `/api` 下（`main.py` 里统一挂载）。关键字搜索是 **GET + query 参数**。

| 端点 | 方法 | 主要参数 | 说明 |
|------|------|----------|------|
| `/` | GET | - | 主页面 |
| `/health` | GET | - | 健康检查（含 Key 是否配置、数据库路径） |
| `/api/trends/search` | GET | `keywords`(逗号分隔), `max_results`(1-50), `order`, `time_range` | 关键词搜索 |
| `/api/trends/shorts/search` | GET | 同上 | 仅 Shorts（≤180 秒） |
| `/api/trends/channel` | GET | `channel_id`(UC 开头), `max_results`, `order`, `time_range` | 频道视频分析 |
| `/api/compare` | POST | JSON: `{"query1": "...", "query2": "...", "time_range": "past_year"}` | 两组关键词对比 |
| `/api/trends/batch-scan` | GET | `keywords`, `max_results`, `time_range` | 多关键词并发扫描 |
| `/api/trends/hot-categories` | GET | `time_range`, `max_results`(1-30) | 热门类别扫描 |
| `/api/trends/feature-analysis` | GET | `keyword`, `max_results`, `time_range` | 特征分析 |
| `/api/shorts/detailed` | GET | `keyword`, `max_results`, `time_range` | 深度 Shorts 分析 |
| `/api/shorts/channel-insights` | GET | `channel_id`, `max_results`, `time_range` | 频道 Shorts 洞察 |
| `/api/trends/trend-tracking` | GET | `keyword`, `limit`(1-100), `refresh`(bool) | 趋势追踪；`refresh=true` 时重扫并追加快照 |
| `/api/trends/channels/search` | GET | `q`, `max_results` | 按名称搜频道 |
| `/api/quota` | GET | - | 今日配额用量（来自真实记账） |
| `/api/history` | GET | `limit` | 最近搜索历史 |
| `/api/history` | DELETE | - | 清空搜索历史 |
| `/api/cache/stats` | GET | - | 缓存条目数与 TTL |
| `/api/cache/clear` | POST | - | 清空缓存 |

`order` 可选：`relevance` / `date` / `viewCount` / `rating` / `velocity`。
`velocity` 是本地重排：先取播放量候选池，再按「播放量/视频年龄(天)」降序，
用于看“最近什么在爆”，而不是“谁攒得久”。
`time_range` 可选：`today` / `this_week` / `this_month` / `past_year`（`/api/compare` 也支持，默认 `past_year`）。
传入其他值会返回 **400**（早期版本会静默回退成 365 天，容易把错误的时间范围当成过去一年）。

### 请求示例

关键词搜索：
```bash
curl "http://localhost:8001/api/trends/search?keywords=AI&max_results=20&order=viewCount&time_range=this_month"
```

对比分析：
```bash
curl -X POST http://localhost:8001/api/compare \
  -H "Content-Type: application/json" \
  -d '{"query1": "AI", "query2": "机器学习"}'
```

趋势追踪（强制刷新会新增一个快照点）：
```bash
curl "http://localhost:8001/api/trends/trend-tracking?keyword=AI&refresh=true"
```

## 数据指标

- **视频总数**：返回的视频数量
- **平均播放量**：所有视频的平均播放次数
- **平均点赞/评论数**
- **播放速度**：`播放量 / 视频年龄(天)`，年龄下限 1 天（避免刚发布的视频产生天文数字）。
  这是唯一的**时间归一化**指标：累计播放量会随视频变老而增长，不能用来判断“是不是在变热”。
- **月均发布**：按样本发布跨度估算的月发布频率（跨度不足 7 天时按 7 天下限计算，避免同天批量发布被放大成异常值）
- **互动率**：`(点赞 + 评论) / 播放量`
- **分位数 / 时长分桶**：深度分析面板中的 p25 / p50 / p75 与 0-15s / 15-30s / 30-45s / 45-60s 分布

> **Shorts 判定**：`#shorts` 关键词搜索 + 时长 ≤ 180 秒。
> YouTube 已在 2024 年 10 月把 Shorts 上限从 60 秒提高到 3 分钟，
> 因此本项目的判定阈值是 180 秒而不是 60 秒。

## 项目结构

```
.
├── main.py                  # 根入口（8001 + reload）
├── test_api.py              # API Key / 网络连通性诊断脚本
├── verify_core.py           # 端到端验证（需服务已在 8001 运行）
├── requirements.txt
├── .env.example
├── app/
│   ├── main.py              # FastAPI 应用：CORS、路由挂载、启动时建表
│   ├── config.py            # 常量、缓存、CORS 白名单、热门类别
│   ├── models.py            # Pydantic 响应模型
│   ├── database.py          # SQLite 读写 + 异步包装（避免阻塞事件循环）
│   ├── services.py          # 业务逻辑与 YouTube API 调用
│   └── routers/
│       ├── trends.py        # /api/trends/*、/api/shorts/*、/api/compare
│       └── system.py        # /health、/api/quota、/api/history、/api/cache/*
├── templates/
│   └── index.html           # 前端页面（Chart.js）
└── tests/
    ├── conftest.py              # 把测试指向临时数据库，避免污染真实数据
    ├── test_main.py             # 端点与模型测试
    └── test_core_logic.py       # 统计口径、缓存、配额、refresh 等回归测试
```

## 测试

```bash
python3 -m pytest tests/ -v
```

`tests/conftest.py` 会把整个测试会话指向一个临时 SQLite 文件，
所以跑测试不会动你的 `youtube_history.db`。

端到端验证（需要先启动服务）：
```bash
python3 verify_core.py
```
它会验证系统端点、无 Key 时的降级行为、参数校验，并用 mock 数据跑一遍全部业务接口。

## 配额说明

YouTube Data API v3 免费配额为 **每天 10000 单位**（按太平洋时间午夜重置）：

| 调用 | 消耗 |
|------|------|
| `search.list` | 100 单位（即每天最多约 100 次） |
| `videos.list` | 1 单位（与传入的 ID 数量无关） |
| `channels.list` | 1 单位 |

应用会累计每次真实（非缓存命中）调用的消耗，`/api/quota` 和前端工具栏的
「📉 API 配额」按钮读的就是这份记账。一次「热门类别」扫描会发出 34 次
`search.list`（约 3300 单位，默认并发上限 8），请留意配额。

## 自动趋势快照

`trend_tracking` 里已有的关键词，后台每 `TREND_SNAPSHOT_INTERVAL_HOURS`（默认 24）
小时自动追加快照，让趋势曲线不需要每天手动点「强制刷新」。

- 只处理**已被追踪过**的关键词，不会自己开始追踪新词，因此不会无故消耗配额；
- 每轮最多 `TREND_SNAPSHOT_MAX_KEYWORDS`（默认 10）个，且当日配额超过 80% 时跳过；
- 设 `TREND_SNAPSHOT_INTERVAL_HOURS=0` 可完全关闭。

## 许可证

MIT License
