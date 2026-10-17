# 开源 API 测试平台前端 UI 模式调研

> 2026-10,为前端平台化改版(前端设计 §10)做的专项调研。方式:官方文档 + README + 第三方评测交叉验证;官方站部分抓取被网络策略拦截,正文来自搜索摘要,交互细节未能从截图证实的已标注「未证实」。结论直接支撑 issue #19/#20/#21 的形态选型。

## A. 用例/接口如何组织

### A1. 左树右表(模块树 + 表格)是主流基线 —— MeterSphere / YApi / Apifox 一致

**MeterSphere「接口定义」页**([官方文档](https://metersphere.io/docs/v2.x/user_manual/api_test/api_definition/)):左侧接口模块树(最多 8 级,hover 出 `+`;新建子模块/模糊搜索/重命名/删除/拖拽调整)+ 右侧 API 列表 + 接口 CASE 列表 + 文档。用例不是独立页面,点 `CASE` 打开该接口下的用例列表(具体是抽屉还是路由页未证实)。

**YApi**([教程](https://ued.qunar.com/yapi/documents/index.html)):左侧分类目录树,右侧该分类下接口表格(分页/筛选),点接口出「预览/编辑/运行」三 tab。定制分支细节:后端 `list_menu` 支持 `islist` 参数(不传=树,传 1=扁平列表,同一份数据两种渲染);树可拖动、树宽可调;表格有「包含子分类接口」复选框。

**Apifox**([页面布局](https://docs.apifox.com/page-layout)):目录树分 8 段(分支版本/搜索/新建/项目概览/接口/数据模型/组件库/快捷请求),接口与接口用例放同一棵树 —— 这是「API 设计+管理+Mock+文档」一体化产品的形态。

### A2. 平铺列表的定位

没有一家纯平铺;平铺只作为「某树节点选中后的右侧内容」。唯一例外 Bruno:侧栏是文件夹层级(磁盘目录即集合),本质仍是树([Quick Start](https://docs.usebruno.com/introduction/quick-start))。

### A3. 树节点的批量/标签/拖拽

- 标签:Apifox 场景用例可打标签并在搜索框按标签过滤([新建场景用例](https://docs.apifox.com/5629565m0))。
- 批量:MeterSphere 报告页批量导出/删除;RunnerGo 企业版批量禁用/同步/删除。
- 跨层拖拽:MeterSphere **场景步骤**可拖拽改层级(见 B);**模块树**的跨层拖拽各文档只有笼统「拖拽调整」描述。Hoppscotch 集合支持拖成子集合([Collections](https://docs.hoppscotch.io/documentation/features/collections))。

## B. 用例详情/编辑工作台布局

### B1. 三栏是标准形态:左流程/步骤列表 + 右详情

**Apifox 编排模式**([文档](https://docs.apifox.com/5629565m0)):点步骤进入编排,左侧整个场景的步骤流程,右侧选中步骤详情;`⬆️⬇️` 切步骤;「全部保存」;**未保存步骤在左列表打小圆点**。步骤类型:从接口导入(手动/自动同步)、从用例导入(复制/引用)、自定义请求、cURL 导入、引用其它场景。

**MeterSphere 场景编辑**([v1.20](https://metersphere.io/docs/v1.20.x-lts/user_manual/api_test/api_automation/)、[v2.x](https://metersphere.io/docs/v2.x/user_manual/api_test/api_scenario/)):点步骤条目空白处展开详情(右侧区域);拖拽同时支持**调序与改层级**;每步可改名/调试/复制/删除。控制器体系:条件/循环(次数/ForEach)/等待/事务/自定义脚本/SQL。

**RunnerGo List 模式**([文档](https://wiki.runnergo.cn/docs/scene/list)):「列表 + 就地编辑」;接口可拖拽排序;`⊕` 在步骤或控制器下方插入相邻步骤;右上角基本设置区(环境选择/保存/调试场景)。

### B2. 步骤的控件载体

三家都是**可折叠列表/卡片**,没有一家用表格编排步骤 —— 表格适合 A 节的清单,不适合编排。

### B3. 请求/响应/断言/提取的分区摆法

**Apifox「运行」页**([发送请求](https://docs.apifox.com/send-request)):上半 = 你构造的请求;下半 = 实际发送的请求 + 响应 + 校验断言结果;右下角「分隔」按钮可切**上下/左右/不分屏**;参数列表有现代/经典两种外观,**屏幕宽度不足自动切紧凑模式**。

**Hoppscotch**([Clients](https://docs.hoppscotch.io/documentation/getting-started/clients)):Topbar/Menu/Sidebar/Main panel/Footer 五块;URL 栏+方法在顶,headers/body 在下 tab,响应在右(左右分栏)。

**Bruno**:请求面板 Body/Auth/Script 分 tab;响应面板 Pretty body/Headers/状态码/耗时/大小 + timeline(实际发送了什么)。

## C. 调试体验

### C1. 单步调试是标配,且都叫「调试」

- MeterSphere:每步操作菜单里「调试」= 单独执行该步;接口定义页有 `TEST` 按钮。
- RunnerGo:接口块即时调试([功能介绍](https://wiki.runnergo.cn/docs/UI))。
- Apifox:单接口「运行」页点发送,断言结果直接显示在响应 tab。
- Bruno/Hoppscotch:单请求 Send(Ctrl+Enter)+ Collection Runner 批量。

### C2. 请求-响应并排

Apifox 默认上下可切左右;Hoppscotch 默认左右;Bruno 上下。

### C3. 断言/提取结果展示位置

- MeterSphere:执行结果区有独立「断言」「提取」tab(最直接)。
- Apifox:单接口断言结果在响应面板(绿/红逐条);还有 **hover 实际响应字段一键生成 JSONPath 断言**([添加断言](https://docs.apifox.com/add-assertions))。
- Hoppscotch Tests tab:Chai 风格 matcher,Send 后逐条 pass/fail([Tests](https://docs.hoppscotch.io/documentation/getting-started/rest/tests))。

## D. 报告/结果页

### D1. 汇总:统计数字为主,图表只在压测场景

- MeterSphere 报告([文档](https://metersphere.io/docs/v2.x/user_manual/api_test/test_report/)):运行环境/资源池/成功失败数据/控制台日志;支持分享链接(时效)与 PDF 导出。
- RunnerGo:统计 + 明细;**多图表与实时监控只在压测报告**([版本对比](https://wiki.runnergo.cn/docs/contrast))。
- HttpRunner HTML 报告([文档](https://httprunner.com/docs/user-guide/api-test/report/)):Summary(时间/时长/环境/结果统计)+ Details 逐用例,`log` 按钮展开请求响应/校验/耗时,失败多一个 traceback。

### D2. 步骤明细:可展开列表,不是时间线

MeterSphere 点步骤名看响应、断言/提取独立 tab;HttpRunner 列表 + 展开按钮。**检索到的官方文档中没有时间线形态**;最接近的是 Bruno 响应区的发送 timeline。Allure 的 timeline/graphs 属于独立报告系统。

### D3. 分享/导出

MeterSphere 分享链接 + PDF;Apifox 导出 HTML;HttpRunner 静态 HTML;Allure 静态 HTML。静态 HTML + 分享链接是验证过的刚需(但涉及鉴权成本)。

## E. 顶级导航

| 平台 | 顶级导航 |
|---|---|
| MeterSphere | 左侧 8 模块(工作台/测试跟踪/接口测试/UI 测试/性能测试/报表统计/项目设置/系统设置),模块内顶部 Tab 切子功能 |
| Apifox | 接口管理/自动化测试/分享文档/请求历史/项目设置等;底部条:折叠树/回收站 |
| Hoppscotch | Topbar / Menu / Sidebar(随上下文)/ Main / Footer |
| YApi | 项目管理/接口管理/测试集合/Mock |
| RunnerGo | 场景管理/接口管理/计划任务/报告/压测/Mock/录制 |

共性:左侧固定模块菜单 + 模块内二级 Tab;「接口定义」与「自动化/场景」分属两个顶级模块(MeterSphere/Apifox/RunnerGo 一致)。

**附:Apifox 标签页模式**([文档](https://docs.apifox.com/7973260m0)):单击树节点 = 临时标签(斜体,再点别处覆盖);编辑后变常规,再点新建标签;双击固定。「route 导航」与「多开工作台」的折中,可渐进式抄。

## F. 抄什么 / 不抄什么

### ✅ 建议抄(低成本,Element Plus 可落地)

1. **左树右表**(MeterSphere/YApi):`el-tree` + `el-table`,树先 2–3 级,节点操作只做新建/重命名/删除/搜索 → issue #20
2. **左步骤列表 + 右详情面板**(Apifox 编排模式)→ issue #19
3. **步骤卡片拖拽排序 + 行内 ⊕ 插入**(RunnerGo/Apifox);**不抄** MeterSphere「拖拽同时改层级」—— 实现与理解成本都高
4. **未保存步骤小圆点**(Apifox)→ issue #19
5. **请求/响应分栏 + 布局切换**(Apifox「分隔」)→ issue #19
6. **单步「调试」按钮**(MeterSphere/RunnerGo)→ issue #19
7. **断言/提取独立 tab**(MeterSphere)→ issue #19/#21
8. **报告 = 统计卡 + 可展开明细**(MeterSphere + HttpRunner)→ issue #21
9. **从实际响应一键加断言**(Apifox):中等成本,单步调试落地后再做
10. **参数列表窄屏自动切紧凑模式**(Apifox)
11. **集合级变量/请求头继承**(Hoppscotch):子级默认继承、可覆盖 —— 若引入目录级变量再对齐

### ⚠️ 可选 / 看规模

- Apifox 临时/固定标签页:要一套 tab 状态管理 + 路由同步,等「同时看两个用例」的真实诉求
- 「复制/引用」两种导入模式:引用模式涉及引用计数/循环检测/删除保护,是数据模型层面的事;UI 先只做「复制」
- 静态 HTML 报告导出 + 分享链接:团队内使用时鉴权成本不低,先做导出/打印视图

### ❌ 不建议抄

1. **Flow 画布**(RunnerGo):要图布局引擎/连线路由/小地图,RunnerGo 开源版自己都只给 Flow 留了 List 给企业版;List + 拖拽完全覆盖
2. **MeterSphere 全套控制器**(条件/循环/ForEach/事务/脚本/SQL/DUBBO/TCP):JMeter 血统的范围爆炸,与「用例 = DSL 步骤列表」冲突;最多加「分组」「等待」
3. **Apifox 8 段式目录树**:一体化 API 产品的产物;测试平台目录树只放用例
4. **Bruno 本地文件集合**(git-first):与 DB 驱动多人协作直接冲突
5. **Allure 全套报告视图**:独立渲染栈,信息密度反而不如统计卡 + 展开列表
6. **Hoppscotch `pw.*` JS 脚本 + 50+ matcher**:引入 JS 等于与 JSON DSL 双轨维护
7. **YApi `islist` 双形态接口**:无「平铺视图」诉求就是双份分支

## 一句话结论

抄「列表 + 面板 + 分栏」这一层:左树右表、左步骤列表右详情、单步调试、请求响应分栏、未保存圆点;画布、控制器全集、一体化 API 管理侧栏是大厂规模产物,复杂度留在后端 DSL 里。

## 来源

- [MeterSphere 接口定义](https://metersphere.io/docs/v2.x/user_manual/api_test/api_definition/) / [接口自动化](https://metersphere.io/docs/v1.20.x-lts/user_manual/api_test/api_automation/) / [场景编排](https://metersphere.io/docs/v2.x/user_manual/api_test/api_scenario/) / [测试报告](https://metersphere.io/docs/v2.x/user_manual/api_test/test_report/) / [Quick start](https://metersphere.io/docs/v2.x/quick_start/quick_start/)
- [Apifox 页面布局](https://docs.apifox.com/page-layout) / [发送请求](https://docs.apifox.com/send-request) / [添加断言](https://docs.apifox.com/add-assertions) / [新建场景用例](https://docs.apifox.com/5629565m0) / [标签页](https://docs.apifox.com/7973260m0) / [接口自动化](https://apifox.com/help/articles/apifox-api-automation/)
- [Hoppscotch Clients](https://docs.hoppscotch.io/documentation/getting-started/clients) / [Collections](https://docs.hoppscotch.io/documentation/features/collections) / [Tests](https://docs.hoppscotch.io/documentation/getting-started/rest/tests)
- [Bruno Quick Start](https://docs.usebruno.com/introduction/quick-start) / [REST request](https://docs.usebruno.com/send-requests/REST/rest-api)
- [RunnerGo 版本对比](https://wiki.runnergo.cn/docs/contrast) / [List 模式](https://wiki.runnergo.cn/docs/scene/list) / [功能介绍](https://wiki.runnergo.cn/docs/UI)
- [YApi 教程](https://ued.qunar.com/yapi/documents/index.html)
- [HttpRunner 测试报告](https://httprunner.com/docs/user-guide/api-test/report/)
