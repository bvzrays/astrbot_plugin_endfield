<div align="center">

# 🌌 astrbot_plugin_endfield
### *plugin_endfield  astrbot移植版*

[![GitHub stars](https://img.shields.io/github/stars/bvzrays/astrbot_plugin_endfield?style=for-the-badge&color=FF6B6B)](https://github.com/bvzrays/astrbot_plugin_endfield/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/bvzrays/astrbot_plugin_endfield?style=for-the-badge&color=4ECDC4)](https://github.com/bvzrays/astrbot_plugin_endfield/network)
[![GitHub issues](https://img.shields.io/github/issues/bvzrays/astrbot_plugin_endfield?style=for-the-badge&color=45B7D1)](https://github.com/bvzrays/astrbot_plugin_endfield/issues)
[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-FF6B6B?style=for-the-badge&logo=python)](https://github.com/Soulter/AstrBot)

<img src="resources/img/ET logo.svg" width="200" alt="终末地协议终端Logo" />

### 🚀 基于森空岛API & 终末地协议终端的终末地查询工具
### 绑定 · 便签 · 干员面板 · 抽卡分析 · 签到

**如果这个插件对你有帮助，请点亮⭐支持一下！**

</div>

---

## 📑 目录

- [✨ 特性一览](#-特性一览)
- [🔧 安装与配置](#-安装与配置)
- [📁 项目结构](#-项目结构)
- [🎮 功能详解](#-功能详解)
- [📸 功能预览](#-功能预览)
- [🎨 自定义美化](#-自定义美化)
- [📋 TODO](#-todo)
- [❓ 常见问题](#-常见问题)
- [📜 更新日志](#-更新日志)
- [🙏 鸣谢](#-鸣谢)

---

## ✨ 特性一览

✅ **账号管理** - 扫码登录/网页授权/国际服登录/多账号切换/删除，主账号快速切换  

✅ **消息撤回** - 登录链接与二维码超时、完成或被拒时自动撤回，保护账号安全

✅ **数据查询** - 便签、理智、干员面板、抽卡记录/分析、全服统计、成就、日历

✅ **建设进度** - 帝江号建设、地区建设（含调度券显示）

✅ **自动签到** - 每日自动执行森空岛签到

✅ **公告推送** - 官方公告列表/最新公告/订阅推送

✅ **订阅提醒** - 理智满值、调度券满值时自动推送；支持取消订阅

✅ **卡池分析** - 抽卡数据统计与可视化，支持抽卡分析同步  

---

## 🔧 安装与配置

### 快速安装

在AstrBot插件管理器中搜索 `astrbot_plugin_endfield` 安装，或通过Git克隆：

```bash
cd AstrBot/data/plugins
git clone https://github.com/Entropy-Increase-Team/astrbot_plugin_endfield.git
```

### 环境依赖

确保已安装Playwright浏览器内核：

```bash
playwright install chromium
```

### 配置项说明

| 配置项 | 类型 | 默认值 | 说明 |
|:-------|:-----|:-------|:-----|
| `api_key` | string | 无 | ⚠️ 必填，前往[协议终端](https://end.shallow.ink)获取 |
| `auth_client_name` | string | `终末地机器人` | 网页授权登录时的显示名称 |
| `operator_list_bg` | string | `random` | 干员列表背景图 (`random`/`bg1.png`/`bg2.png`) |
| `render_timeout` | number | `30000` | 图片渲染超时时间（毫秒） |

---

## 📁 项目结构

```
astrbot_plugin_endfield/
├── main.py                 # 插件入口，指令路由
├── metadata.yaml           # 插件元数据
├── _conf_schema.json       # WebUI配置schema
├── core/                   # 核心逻辑
│   ├── client.py           # API异步客户端
│   ├── user.py             # 用户数据中心
│   └── render.py           # HTML渲染助手
├── data/                   # 持久化存储
│   └── users.json          # 用户绑定数据
└── resources/              # 资源文件
    ├── cache/              # 图片缓存
    ├── img/                # 静态图片资源
    ├── operator/           # 干员列表模板
    ├── gacha/              # 抽卡分析模板
    ├── stamina/            # 理智模板
    └── help/               # 帮助菜单模板
```

---

## 🎮 功能详解

> 💡 **指令前缀**：默认为 `/`，在AstrBot配置中自定义

### 🔐 账号与绑定

| 指令 | 说明 |
|:-----|:-----|
| `授权登陆` | 网页安全授权登录（推荐） |
| `扫码绑定` | 扫描二维码快捷登录 |
| `手机绑定 [手机号]` | 验证码登录（暂不可用） |
| `国际服登录` | 邮箱+密码登录国际服 |
| `绑定列表` | 查看所有绑定账号 |
| `切换绑定 [序号]` | 切换当前主账号 |
| `删除绑定 [序号]` | 解绑指定账号 |

### 📊 数据查询

| 指令 | 说明 |
|:-----|:-----|
| `便签` | 账号数据总览 |
| `理智` | 理智查询 |
| `干员列表` | 持有干员图鉴 |
| `同步面板` | 同步干员详细战斗属性 |
| `<干员名>面板` | 单干员详情（技能/武器/基质） |
| `抽卡记录` | 近期抽卡历史 |
| `抽卡分析` | 全卡池统计分析 |
| `抽卡分析同步` | 先同步抽卡数据再分析 |
| `全服统计` | 全服抽卡统计与排行 |
| `签到` | 执行每日签到 |
| `日历` | 活动版本日历 |
| `帝江号建设` / `帝江号` | 帝江号基建进度 |
| `地区建设` / `建设` | 地区开发进度与调度券 |
| `成就列表` / `成就` | 成就达成情况 |

### 📢 公告与订阅

| 指令 | 说明 |
|:-----|:-----|
| `公告` | 官方公告列表 |
| `公告最新` | 获取最新一条公告全文 |
| `订阅公告` | 订阅公告推送 |
| `取消订阅公告` | 取消公告订阅 |
| `订阅理智` | 理智满值时推送 |
| `取消订阅理智` | 取消理智订阅 |
| `订阅调度券` | 调度券满值时推送 |
| `取消订阅调度券` | 取消调度券订阅 |

> 💡 发送  `zmd` 可查看插件完整帮助菜单（图片版）。

---

## 📸 功能预览

<details open>
<summary>点击展开预览图</summary>

| `便签` | `理智` |
|:---:|:---:|
| <img src="https://github.com/user-attachments/assets/d9c07469-f00d-42c2-820a-f46402adf714" width="400"> | <img src="https://github.com/user-attachments/assets/0a723c50-d81d-444f-932e-32918a0ee2ed" width="400"> |

| `干员列表` | `抽卡分析` |
|:---:|:---:|
| <img src="https://github.com/user-attachments/assets/3355b411-215a-4bf9-b536-e67804e8d122" width="400"> | <img src="https://github.com/user-attachments/assets/5e86a76b-0d06-4f7b-97fc-6b914f57efb3" width="300"> |

| `帝江号建设` | `地区建设` |
|:---:|:---:|
| <img src="https://github.com/user-attachments/assets/df71bbdf-febb-4f00-95ba-157f5788629f" width="400"> | <img src="https://github.com/user-attachments/assets/cf3b53b9-fa78-4b71-b682-9077dfafc3c1" width="400"> |

| `公告` | `日历` |
|:---:|:---:|
| <img src="https://github.com/user-attachments/assets/99da9389-1326-487c-b11e-d526d0e251fb" width="400"> | <img src="https://github.com/user-attachments/assets/b3dbe4c9-d916-4abd-aeee-a434a43d4c0f" width="600"> |

| `xx面板` | `zmd` |
|:---:|:---:|
| <img width="2200" height="2036" alt="9045c4a094a6b434815e31abc696dbac" src="https://github.com/user-attachments/assets/d8422043-106f-44e3-9b61-4b3faa5998f0" /> | <img width="1280" height="1480" alt="d4082ea8f03d293df5a73d21e7ed3acc_720" src="https://github.com/user-attachments/assets/4bb0bf3d-b9b9-43f2-a5ba-81bc7909956f" /> |

| `全服统计` |
|:---:|
| <img width="1440" height="2942" alt="f282a7a2b2947e407c49ebc880bd6bd2" src="https://github.com/user-attachments/assets/a5152de5-03bf-49cc-be6e-4a12e25477b5" width="400"/> |
 |

</details>

---

## 🎨 自定义美化

你可以通过替换资源文件来自定义渲染图片样式：

**路径：** `AstrBot/data/plugins/astrbot_plugin_endfield/resources/`

| 功能 | 自定义方式 |
|:-----|:----------|
| **理智图背景** | 替换 `img/stbg.png` |
| **干员列表背景** | 替换 `operator/img/opbg.png` |

---

## 📋 TODO

- [x] **基础查询** (帝江号建设/地区建设/订阅理智/日历)
- [x] **公告系统** (列表获取与推送)
- [ ] **抽卡辅助** (全服统计/模拟抽卡)
- [x] **管理员功能** (全员签到/数据同步)
- [ ] **MaaEnd远程控制** (状态监控与远程操作)



---

## 📜 更新日志

<details>
<summary>点击展开版本历史</summary>

### 2.5.0 (2026-03-15)
- 🎨 干员面板：武器区展示基质属性（gemSkills），基质名称/星级/图标
- 🎨 抽卡分析：三列布局，统计区增加平均出红与 6/5/4 星总量
- 🔧 便签页：角色稀有度与徽章显示
- 🔧 帝江号建设：接口适配
- 📐 干员面板排版修复
- ✨ 新增成就显示
- ✨ 地区建设新增调度券显示
- ✨ 新增调度券满值订阅

### 2.2.0 (2026-03-13)
- ✨ 移除 Wiki 和 攻略 模块，后续再添加
- 🚀 优化版本日历：伸展时间轴并提升渲染宽度（3000px），解决拥挤
- 🔧 修复日历页尾 Logo显示不全的问题
- 🚀 国际服支持

### 2.0.0 (2026-03-05)
- ✨ 新增全服统计
- 🛡️ 登录成功/超时/被拒后自动撤回二维码或链接 
- 🚀 优化协议端调用
- 📊 优化 API 日志

### 1.8.0 (2026-03-03)
- ✨ 干员面板修复
- 🚀 理智推送逻辑重构
- 🎨 菜单与 UI 修复: 解决了帮助菜单图片渲染可能失败的问题
- 🔧 全模块局部 import 顶层化，修复 getaddrinfo 同步阻塞问题
- 🔧 森空岛自动签到修复，具体查看配置项
### 1.7.0 (2026-03-02)
- ✨ 添加并重构干员面板 (`xxx面板`)：全新左右布局，支持展示武器被动、精炼、装备套装属性及详情
- 🎨 帮助菜单图片版重绘 (`zmd`)
- 🚀 修复了 IDE 格式化可能导致的模板语法破碎问题
- 🔧 修复公告订阅逻辑，简化配置项

### 1.6.0 (2026-03-01)
- ✨ 新增 `/日历` 指令，自动获取Wiki横幅
- 🎨 优化日历UI和绑定列表显示
- 🔧 完善自动签到与订阅理智功能

### 1.5.0 (2026-03-01)
- ✨ 新增 `/帝江号建设`、`/地区建设` 指令
- 🎨 重构信赖与心情展示UI

### 1.4.0 (2026-02-28)
- ✨ 新增公告列表渲染 (`/公告`)
- ✨ 新增单条公告详情 (`/公告 <序号>`)
- 🚀 优化图片动态填充逻辑

### 1.3.0 (2026-02-27)
- 📊 重构抽卡分析，移除5星标记
- ⚡ 提升渲染稳定性，超时延长至30s

### 1.2.0 (2026-02-27)
- ⚡ 抽卡分析异步化优化
- 🎨 便签面板UI重绘
- 🐛 修复干员列表显示问题
- 🖼️ 帮助菜单改为图片输出

### 1.1.0 (2026-02-26)
- 🐛 修复理智查询问题
- ✨ 新增干员列表功能

</details>

---

## 🙏 鸣谢


### 其他框架

- **云崽**：[endfield-plugin](https://github.com/Entropy-Increase-Team/endfield-plugin)
- **Astrbot**：[astrbot_plugin_endfield](https://github.com/Entropy-Increase-Team/astrbot_plugin_endfield)
- **Nonebot2**：[nonebot-plugin-endfield](https://github.com/Entropy-Increase-Team/nonebot-plugin-endfield)

特别感谢：
- [@QingYingX](https://github.com/QingYingX) & [@浅巷墨黎](https://github.com/dnyo666)
- [终末地协议终端](https://end.shallow.ink) 提供的底层API封装
- 所有贡献者和测试者

---

<div align="center">

### 💬 加入

| 终末地协议终端交流群 | AstrBot移植版反馈群 |
|:---:|:---:|
| [160759479](https://qm.qq.com/q/zZXruW6V4Q) | [870543663](https://qm.qq.com/q/kPxQZy5gg8) |

---
## ❓ 常见问题

### Q1: 安装后无法渲染图片？

✅ 检查是否已执行 `playwright install chromium`。如果在 AstrBot 环境下缺失依赖，请在 **WebUI 的日志页面右上角** 使用内置的 `pip安装` 功能安装 `playwright`，并确保无头浏览器已正确安装。


### Q2: 提示 `TypeError: ... takes 2 positional arguments but 3 were given`？

✅ 这种报错通常由插件重复加载或 AstrBot 框架版本未对齐引起。**尝试完全重启 AstrBot 框架**（而非仅重载插件）通常即可解决。

### Q3: 网页授权成功，但 Bot 提示 `framework_token` 无效或绑定失败？

✅ **可能原因：**

 **网络干扰**：环境开启了全局代理（梯子）导致 Token 校验失败，请尝试关闭代理重试。
 
 **Token 过期**：授权过程超时。
 
 **配置文件冲突**：若上述无效，请尝试删除插件 `data/users.json` 中对应的绑定记录，或去协议终端删除绑定后重新扫码。

### Q4: 提示 522 错误或“凭证信息不完整（缺少角色ID）”？

✅ 这种情况通常是 API 握手失效或缓存过期。请尝试**删除该账号绑定**，并重新执行 `/授权登陆` 或 `/扫码绑定`。

### Q5: 图片生成卡死或报 500 错误？

✅ 检查 `resources/cache/` 目录的读写权限，尝试手动删除缓存目录下的临时文件后重试。

**💡网络环境： 绑定或查询失败时，请优先确认 Bot 部署环境的网络连通性。**


**如果喜欢这个插件，别忘了给仓库点个⭐！**

[⬆ 返回顶部](#-astrbot_plugin_endfield)

</div>










