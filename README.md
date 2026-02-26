# AstrBot 终末地协议终端插件

基于 [森空岛 API](https://skland.com) 及 [终末地协议终端](https://end.shallow.ink) 的 AstrBot 插件，提供详尽的玩家数据查询、理智展示、干员面板及抽卡分析功能。

## 安装与配置

1. 在 AstrBot 插件管理器中搜索 `astrbot_plugin_endfield` 并安装。
2. 确保在系统环境中已安装并正确配置浏览器依赖以供 Playwright 渲染：`playwright install chromium`
3. 插件配置项（按需设置）：
   - `api_key`：可选。如需高级功能，请前往 [浅墨服务构建](https://end.shallow.ink) 获取。
   - `auth_client_name`：网页授权登录时的显示名称（默认：`终末地机器人`）

## 功能一览

| 指令前缀：`/` (或自定义) | 说明 |
|------|------|
| **基础功能** | |
| `帮助` 或 `zmd` | 打开帮助菜单 |
| **账号与绑定** | |
| `授权登陆` | 通过森空岛网页进行安全授权登录 |
| `扫码绑定` | 扫描二维码快捷登录 |
| `手机绑定 [手机号]` | 接收验证码登录 |
| `绑定列表` | 查看当前所有已绑定的账号状态 |
| `切换绑定 [序号]` | 切换当前主账号 |
| `删除绑定 [序号]` | 删除指定账号绑定 |
| **数据查询 (渲染图)** | |
| `便签` 或 `理智` | 查询当前理智、日常活跃度、回满时间 |
| `干员列表` | 查询当前持有的干员图鉴及等级 |
| `<干员名>面板` | 查询单个干员星级、等级及详细面板图 |
| `抽卡记录` | 查询近期抽卡历史记录 |
| `抽卡分析` | 生成全卡池抽卡数据统计分析图 |
| `签到` | 执行所有账号的森空岛每日签到 |

---

## 🎨 资源自定义 (背景与头像修改)

你可以通过替换插件目录中的资源文件来自定义生成的渲染图片样式：
路径：`AstrBot/data/plugins/astrbot_plugin_endfield/resources/`

- **理智图背景**：放入 `resources/img/stbg.png`
  - 建议尺寸或比例配合渲染框使用。用于 `/理智` 查询页面背景。
- **干员列表背景**：放入 `resources/operator/img/opbg.png`
  - 用于 `/干员列表` 查询页面背景。
- **随机干员立绘/头像**：放入 `resources/img/operator/` 文件夹下
  - 在生成 `/理智` 图时，系统会默认在此文件夹下随机抽取图片作为右侧展示（如无内容则不显示）。支持 `png`, `jpg`, `webp` 格式。

*注：部分底层素材如五角星、面板图标等可在 `resources/meta/` 目录下进行同名替换。*

---

## 常见问题排查

若插件未能响应功能或图片无法渲染，请检查：
1. 是否已执行 `playwright install chromium` 确保无头浏览器能正常捕捉画面。
2. 若图片卡死、发生 500 报错，可进入 `render_cache/` 检查本地图片渲染情况。系统会自动清理该目录。

## 鸣谢

本项目逻辑主要移植与参考自 Yunzai 优秀插件 [endfield-plugin](https://github.com/Entropy-Increase-Team/endfield-plugin)。
- 感谢原作者及贡献者：[@QingYingX](https://github.com/QingYingX) 与 [@浅巷墨黎（Dnyo666）](https://github.com/dnyo666)
- 感谢 [终末地协议终端](https://end.shallow.ink) 提供的底层封装。

如有其它问题，请提交 Issue。
