# AI智能政企瞭望舆情收集分析系统

基于 Flask + SQLite 的轻量级舆情采集与分析后台，包含：爬虫源管理、关键词采集、数据管理、AI 模型管理、AI 分析报告、数智大屏。

## 快速开始（Windows / macOS / Linux）

1. 安装依赖

```bash
pip install -r requirements.txt
```

2. 启动服务（默认端口 8000）

```bash
python app.py
```

3. 浏览器访问
- http://localhost:8000/

默认账号：
- 用户名：admin
- 密码：admin123

数据存储：
- 首次启动会在 `instance/app.db` 创建 SQLite 数据库并写入默认数据。

环境变量（可选）：
- `PORT`：服务端口（默认 8000）
- `APP_SECRET_KEY`：Flask Session 加密密钥（默认 dev-secret-key）

## 功能模块

### 后台管理
- 登录/退出
- 健康检查：`/healthz`

### 爬虫管理
- 新增/启用/停用/删除爬虫源（Baidu / Google News RSS / GDELT）

### 采集管理
- 输入关键词，选择启用的爬虫源进行采集
- 采集过程实时推送并渲染数据流
- 支持将采集条目保存到数据库

### 数据管理
- 展示已保存数据
- 支持标题/来源模糊搜索
- 支持分页
- 支持批量删除

### AI 模型管理
- 维护大模型接入配置（Base URL / API Key / Model / 系统提示词）
- 支持启用/停用与删除模型配置
- 支持弹窗对话测试
- 统计并汇总 Token 消耗

### AI 分析报告
- 展示保存数据概览、关键词/来源统计
- 提供文本分析接口：优先使用已启用的 AI 模型；不可用时回退本地分析

### AI 数智大屏
- 左侧显示百度热搜（支持自动刷新）
- 下方显示热搜来源/分类分布图
- 右侧为 3D 地球（可鼠标旋转），展示全球城市汇聚到北京的光线流动特效
- 3D 组件不可用时自动回退为 2D 中国地图渲染

## 主要页面入口
- 数智大屏：`/bigscreen`
- 采集管理：`/collect`
- 数据管理：`/data`
- 爬虫管理：`/crawlers`
- AI 模型管理：`/models`
- AI 分析报告：`/report`

## 代码结构（核心）
- `app.py`：后端入口、路由、数据模型、采集与 AI 调用
- `templates/`：页面模板
- `static/css/style.css`：全局样式
- `static/js/`：前端脚本（采集/大屏等）
