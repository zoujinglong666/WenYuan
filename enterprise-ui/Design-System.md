# 企业知识库 · Enterprise Blue Design System V1.0

## 1. 视觉定位
独立企业级 RAG 工作台。深蓝导航、科技蓝操作、白色卡片、浅蓝灰画布。禁止沿用万能图图紫色主题。以 reference.png 为视觉基准，1920×1080 CSS px，浏览器缩放100%。原图为生成稿，已等比例近似重采样至目标尺寸；以下参数为工程拟定值，并非原始分层设计测量值。

## 2. Design Tokens
```css
:root {
 --brand: #1677ff; --brand-hover: #4096ff; --brand-active: #0958d9;
 --nav-top: #092554; --nav-bottom: #214b91;
 --bg: #f5f8fc; --surface: #fff; --border: #e2e8f0;
 --text: #0f172a; --text-secondary: #475569; --muted: #64748b;
 --success: #16a34a; --warning: #d97706; --danger: #dc2626;
 --radius-sm: 8px; --radius-md: 12px; --radius-lg: 24px;
 --shadow-card: 0 6px 24px rgba(30,64,120,.07);
 --shadow-input: 0 4px 14px rgba(22,119,255,.14);
 --sidebar-width: 280px; --header-height: 92px;
 font-family: Inter, "PingFang SC", "Microsoft YaHei", sans-serif;
}
```
间距采用 4/8/12/16/24/32/40/48/64px。标题 56–60px/700（欢迎页专用），页面标题28px/600，卡片标题26px/600，正文18–20px，辅助14–16px。后台列表正文14px。按钮和图标交互区至少40px；焦点环2px。正文对比度目标 WCAG AA。

## 3. 1920×1080 页面定位
坐标以整个视口左上为原点，允许参考截图校正。
| 区域 | x | y | w | h |
|---|---:|---:|---:|---:|
| Sidebar | 0 | 0 | 280 | 1080 |
| Header | 280 | 0 | 1640 | 92 |
| Hero 标题 | 376 | 210 | 900 | 90 |
| Hero 副标题 | 376 | 324 | 900 | 48 |
| Hero 插画 | 1258 | 140 | 548 | 348 |
| Composer | 446 | 498 | 1304 | 102 |
| QuickAsk | 376 | 662 | 1456 | 76 |
| FeatureGrid | 342 | 806 | 1536 | 194 |

导航 Logo 44px，菜单行高68px，间隔12px，左右20px；选中蓝色圆角18px。Header 白色，右侧消息、用户区域只在已有真实功能时显示。Hero 背景使用 CSS 淡蓝渐变及低对比曲线，不使用整张截图。输入框白底、蓝色1.5px描边、胶囊圆角，发送按钮蓝底。三个快捷问题横排，白底浅描边。三张功能卡等宽、间距32px、圆角24px；图标块72px，文案左对齐。

## 4. 组件契约
| 组件 | Props | Events |
|---|---|---|
| AppSidebar | items, activeKey, collapsed | navigate, toggle |
| AppHeader | title, serviceStatus, user | themeChange, action |
| ChatWelcome | title, description, illustration | — |
| ChatComposer | modelValue, disabled, loading, attachments | update:modelValue, submit, stop, attach |
| QuickAsk | items, disabled | select |
| FeatureCard | title, description, icon, route | navigate |
| ConversationList | items, activeId, loading | select, create, delete |
| AnswerMessage | content, citations, status | retry, copy, openCitation |

## 5. 交互状态
导航 default/hover/active/focus/disabled；输入 empty/focus/typing/submitting/streaming/error；空白欢迎页在首条发送后切换至会话布局，输入框固定于会话底部，消息区域独立滚动。保留新对话、历史会话、服务就绪状态和主题切换。快捷问题点击填入输入框，是否自动发送沿用原业务。Enter 发送，Shift+Enter 换行，中文 IME composing 时禁止发送。流式输出支持停止（仅已有接口支持时），错误可重试且保留问题。引用展示真实文档标题、页码或片段；没有来源时明确提示，不虚构引用。附件、通知、管理员菜单及功能卡路由不得凭设计稿虚构能力。

## 6. 响应式
1920 基准；1440–1919 导航240px、内容按比例收敛，标题44–48px；1024–1439 导航可折叠至72px，快捷问题换行，插画缩小；小于1024 单列布局、抽屉导航。禁止整体 transform:scale，禁止横向溢出。聊天页使用 min-height:0、flex:1、overflow:auto，输入区不遮挡消息。

## 7. 资源规范
assets/hero-search.png 是参考稿矩形提取图，含原浅蓝背景，不能当透明插画；可用于相近背景的过渡版本。其他原稿提取图同样保留背景。icon-* 为独立重绘 RGBA PNG，128×128，蓝/白/灰三种状态，可显示24/32px。不是原稿无损分层资源。禁止 SVG 和 emoji 替代。普通文字、按钮、卡片、渐变、阴影均由 CSS 实现。禁止整页图片作为页面。

## 8. 验收
1920×1080 截图逐区域叠加；核心布局误差目标≤4px，文字基线≤3px，颜色对照 tokens；实际截图与设计稿均使用相同 DPR。测试1440×900、1366×768、1024×768，键盘焦点、中文输入、流式输出、历史会话、失败重试、真实引用及主题切换。未获得代码前不宣称已通过验收。
