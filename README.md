# 智能电脑配置生成系统

基于AI的电脑配置推荐系统，支持实时价格查询。

## 界面预览

<img width="48%" src="https://raw.githubusercontent.com/kisszz666/ai-combud/master/demo/screenshot-1.png" />
<img width="48%" src="https://raw.githubusercontent.com/kisszz666/ai-combud/master/demo/screenshot-2.png" />

<img width="48%" src="https://raw.githubusercontent.com/kisszz666/ai-combud/master/demo/screenshot-3.png" />
<img width="48%" src="https://raw.githubusercontent.com/kisszz666/ai-combud/master/demo/screenshot-4.png" />

## 演示视频

<img width="60%" src="https://raw.githubusercontent.com/kisszz666/ai-combud/master/demo/demo.gif" />

> 📹 点击观看完整视频：[demo/demo.mp4](demo/demo.mp4)

## 项目简介

本系统通过AI大模型根据用户预算和使用需求，智能推荐最优电脑硬件配置，并实时从中关村在线获取配件价格。

## 核心功能

- 🤖 **AI智能推荐** - 基于DeepSeek大模型智能生成配置方案
- 💰 **实时价格查询** - 自动抓取中关村在线真实价格
- 📊 **预算智能控制** - 自动调整配置以匹配用户预算（允许25%浮动）
- 🎯 **场景化推荐** - 支持游戏、办公、设计等多种使用场景

## 技术栈

### 后端
- Python 3.8+
- FastAPI (Web框架)
- httpx (HTTP客户端)
- lxml (HTML解析)

### 前端
- Vue 3 + Vite
- 原生CSS (紫色-黑色主题)

## 项目结构

```
fianl4/
├── backend/                 # 后端代码目录
│   ├── __init__.py         # Python包初始化文件
│   ├── main.py             # FastAPI主应用
│   ├── ai_service.py       # AI服务模块
│   ├── price_service.py    # 价格服务模块
│   └── requirements.txt    # 后端依赖
├── src/                    # 前端源代码
│   ├── App.vue             # 主组件
│   ├── main.js             # 入口文件
│   └── style.css           # 全局样式
├── public/                 # 静态资源
├── price_search.py         # 价格爬虫脚本（已提供）
├── index.html              # HTML入口
├── vite.config.js          # Vite配置
└── package.json            # 前端依赖配置
```

## 快速开始

### 环境要求

- Python 3.8 或更高版本
- Node.js 16 或更高版本
- npm 或 yarn

### 🚀 启动步骤（重要！请按顺序执行）

#### 步骤1：安装Python后端依赖

```bash
pip install fastapi uvicorn httpx pydantic lxml
```

或者进入backend目录执行：
```bash
cd backend
pip install -r requirements.txt
cd ..
```

#### 步骤2：启动后端服务（端口8000）

**打开一个新的命令行窗口**，执行：
```bash
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

等待看到以下输出表示启动成功：
```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Application startup complete.
```

#### 步骤3：安装前端依赖

**在原始命令行窗口**，执行：
```bash
npm install
```

#### 步骤4：启动前端开发服务器

```bash
npm run dev
```

等待看到以下输出表示启动成功：
```
VITE v.x.x.x  ready in xxx ms
➜  Local:   http://localhost:5173/
```

#### 步骤5：访问应用

打开浏览器访问：**http://localhost:5173/**

### ⚠️ 注意事项

1. **必须先启动后端再启动前端**，否则Vite的代理配置可能无法正确初始化
2. 确保端口8000没有被其他程序占用
3. 如果端口被占用，可以修改：
   - 后端启动命令中的`--port`参数
   - `vite.config.js`中的`proxy.target`
   - `src/App.vue`中的`API_URL`变量

### 生产部署

#### 构建前端：
```bash
npm run build
```
构建产物会在 `dist/` 目录下。

#### 启动后端生产服务：
```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 4
```

## API接口文档

### 自动生成文档

启动后端后，访问以下地址查看完整API文档：
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### 主要接口

#### 生成配置

**POST** `/api/generate`

请求体：
```json
{
  "budget": 5000,
  "use_case": "3A游戏"
}
```

响应体：
```json
{
  "success": true,
  "total_price": 5758,
  "budget": 5000,
  "remaining": -758,
  "configs": [
    {
      "category": "CPU",
      "model": "AMD 锐龙5 5600 (散片)",
      "reason": "6核12线程，3.5GHz基础频率，游戏性能足够",
      "price": 548,
      "price_status": "success",
      "price_error": null,
      "title": "AMD 锐龙5 5600",
      "link": "https://detail.zol.com.cn/cpu/..."
    }
  ],
  "message": "配置生成成功！",
  "retry_count": 1
}
```

#### 健康检查

**GET** `/health`

响应：
```json
{"status": "healthy"}
```

## 使用说明

1. 打开浏览器访问 http://localhost:5173/
2. 在"预算金额"输入框中输入您的预算（单位：元）
3. 在"使用场景"输入框中描述您的需求（或点击预设标签）
4. 点击"🚀 生成智能配置"按钮
5. 等待10-30秒，系统将：
   - AI生成配置方案
   - 实时抓取中关村在线价格
   - 自动调整以匹配预算
6. 查看推荐配置单，包含每个配件的型号、价格和购买链接

## 配置优先级规则

根据使用场景，系统会按以下优先级分配预算：

| 使用场景 | 优先级顺序 |
|---------|----------|
| 3A游戏 | 显卡 > CPU > 内存 > SSD |
| 电竞网游 | CPU > 显卡 > 内存 > SSD |
| 视频剪辑 | 内存 > CPU > 显卡 > SSD |
| 办公家用 | CPU > SSD > 内存 > 主板 |

## 技术实现说明

### AI配置生成
- 使用DeepSeek大模型（deepseek-v4-pro）
- 兼容Anthropic API格式
- 通过System Prompt约束输出格式
- 自动重试最多3次以匹配预算

### 价格获取
- 使用已提供的`price_search.py`抓取脚本
- 从中关村在线（detail.zol.com.cn）获取价格
- 按"最新时间"排序，获取京东渠道价
- 抓取失败返回明确错误，不使用估算值

### 预算控制
- 允许实际总价超过预算25%
- 超出时自动重新生成配置
- 最多重试3次

## 常见问题

### Q: 启动报错"端口被占用"
A: 修改后端启动命令中的端口号，同时更新`vite.config.js`中的代理配置。

### Q: 价格显示"获取失败"
A: 可能是网络问题或AI生成的型号不够精确，可以重试或修改需求描述。

### Q: 配置总价远超预算
A: AI会自动调整，但如果连续3次都超出预算，可能需要提高预算或降低需求。

### Q: 如何关闭服务
A: 在命令行窗口按 `Ctrl + C` 停止服务。

## 许可证

MIT License
