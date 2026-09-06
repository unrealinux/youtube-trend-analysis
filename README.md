# YouTube 数据分析平台

基于 FastAPI + YouTube Data API v3 的 YouTube 视频数据分析工具。

## 功能特性

### 核心功能
- ✅ **关键词搜索** - 按关键词搜索趋势视频
- ✅ **频道分析** - 分析特定频道的视频数据
- ✅ **对比分析** - 对比两组关键词的流量表现
- ✅ **数据可视化** - Chart.js 图表展示播放量、互动率等
- ✅ **历史查询** - SQLite 持久化搜索历史
- ✅ **配额监控** - YouTube API 配额使用情况追踪
- ✅ **数据导出** - 支持 JSON 和 CSV 格式导出

### 技术特性
- 🚀 **缓存优化** - TTL 缓存减少 API 调用
- 📊 **统计分析** - 自动计算播放量、点赞、评论等指标
- 🏷️ **标签云** - 热门标签可视化
- 🌙 **主题切换** - 深色/浅色主题
- 📱 **响应式设计** - 支持移动端浏览

## 安装

### 1. 获取 YouTube API Key
1. 访问 [Google Cloud Console](https://console.cloud.google.com/)
2. 创建项目并启用 YouTube Data API v3
3. 生成 API Key

### 2. 克隆项目
```bash
git clone <repository-url>
cd youtube-analysis
```

### 3. 安装依赖
```bash
pip install -r requirements.txt
```

### 4. 配置环境变量
```bash
cp .env.example .env
# 编辑 .env 文件，填入你的 API Key
YOUTUBE_API_KEY=your_api_key_here
```

## 运行

### 开发模式
```bash
python -m uvicorn app.main:app --reload --port 8001
```

### Docker 运行
```bash
docker-compose up -d
```

### 访问应用
打开浏览器访问: http://localhost:8000

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 主页面 |
| `/health` | GET | 健康检查 |
| `/api/trends/search` | POST | 关键词搜索 |
| `/api/trends/channel` | GET | 频道分析 |
| `/api/compare` | POST | 对比分析 |
| `/api/history` | GET | 获取历史记录 |
| `/api/history` | DELETE | 清空历史记录 |
| `/api/quota` | GET | 查询 API 配额 |
| `/api/cache/stats` | GET | 缓存状态 |
| `/api/cache/clear` | POST | 清空缓存 |

## 搜索参数

### 关键词搜索
```json
{
  "keywords": ["AI", "机器学习"],
  "max_results": 20,
  "order": "viewCount",
  "time_range": "past_month"
}
```

### 频道分析
```
GET /api/trends/channel?channel_id=UCxxx&max_results=20&order=date&time_range=past_year
```

### 对比分析
```json
{
  "query1": "AI",
  "query2": "机器学习"
}
```

## 数据指标

- **视频总数**: 返回的视频数量
- **平均播放量**: 所有视频的平均播放次数
- **平均点赞数**: 所有视频的平均点赞数
- **平均评论数**: 所有视频的平均评论数
- **月均发布**: 估计的月发布频率

## 项目结构

```
app/
├── main.py              # FastAPI 应用主入口
├── requirements.txt     # Python 依赖
├── templates/
│   └── index.html       # 前端页面
├── static/              # 静态资源
└── tests/
    └── test_main.py     # 单元测试
```

## 测试

```bash
pytest app/tests/ -v
```

## 优化亮点

### v2.1.0
- 添加 TTL 缓存（5分钟）
- 添加 SQLite 搜索历史持久化
- 添加 YouTube API 配额监控
- 添加对比分析功能
- 添加历史记录仪表板
- 改进错误处理和日志记录
- 添加完整的单元测试

### v2.0.0
- 重构为 FastAPI 应用
- 添加 Chart.js 数据可视化
- 支持深色/浅色主题
- 支持 JSON/CSV 导出

## 许可证

MIT License
