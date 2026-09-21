# DESIGN.md

> 暗色游戏风的机器人遥控台 — 精准、直觉、无干扰。视频优先，操控触手可及。

## 1. Visual Theme & Atmosphere

**Style**: Dark Gaming Control Panel
**Keywords**: dark, precise, tactile, focused, minimal-chrome, high-contrast, dashboard
**Tone**: 工业级遥控器 — NOT 花哨营销页、NOT 轻快社交应用
**Feel**: 像坐在驾驶舱里看监视器，手指放在摇杆上，一切尽在掌控。

**Interaction Tier**: L1 精致静态（工具类应用，不需要滚动叙事）
**Dependencies**: CSS only（零 JS 动画库依赖）

## 2. Color Palette & Roles

```css
:root {
  /* Backgrounds */
  --bg: #0b0f17;              /* 页面底色 */
  --surface: #141a24;         /* 面板/卡片 */
  --surface-alt: #1a2130;     /* 次级面板 */
  --surface-hover: #1f2838;   /* hover 态 */

  /* Borders */
  --border: #1e2a3a;          /* 默认边框 */
  --border-hover: #2d3d54;

  /* Text */
  --text: #e8edf5;            /* 主文字 */
  --text-secondary: #8899b4;  /* 辅助文字 */
  --text-tertiary: #55657a;   /* 三级文字 */

  /* Accent - teal/cyan 科技感 */
  --accent: #0fe0ba;
  --accent-hover: #0bc9a5;
  --accent-glow: rgba(15, 224, 186, 0.25);

  /* Semantic */
  --success: #22c55e;
  --error: #ef4444;
  --warning: #f59e0b;

  /* RGB for rgba() */
  --bg-rgb: 11, 15, 23;
  --accent-rgb: 15, 224, 186;
  --success-rgb: 34, 197, 94;
  --error-rgb: 239, 68, 68;
  --warning-rgb: 245, 158, 11;
}
```

**Color Rules:**
- 所有颜色通过 CSS 变量引用，禁止硬编码 hex
- 强调色仅用于关键交互元素（连接按钮、D-pad 方向键）
- 语义色仅用于状态指示灯，不用于大面积着色

## 3. Typography Rules

**Font Stack:**
```css
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

--font-sans: 'Inter', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif;
--font-mono: 'JetBrains Mono', 'SF Mono', 'Consolas', monospace;
```

| Role | Font | Size | Weight | Line Height |
|------|------|------|--------|-------------|
| Title | Inter | 20px | 700 | 1.3 |
| Panel Heading | Inter | 14px | 600 | 1.3 |
| Button | Inter | 15px | 600 | 1 |
| Body | Inter | 14px | 400 | 1.5 |
| Label | Inter | 11px | 500 | 1.3 |
| Status/Code | JetBrains Mono | 12px | 400 | 1.5 |

**Typography Rules:**
- 按钮文字全部 uppercase letter-spacing: 0.04em
- 中文按钮不加 letter-spacing
- NEVER use: 手写体、Comic Sans、装饰性字体

## 4. Component Stylings

### Buttons

```css
/* Primary action button */
.btn-primary {
  background: var(--accent);
  color: #000;
  border: 0;
  border-radius: 8px;
  padding: 10px 20px;
  font: 600 14px var(--font-sans);
  cursor: pointer;
  transition: all 0.15s ease;
}
.btn-primary:hover { background: var(--accent-hover); box-shadow: 0 0 20px var(--accent-glow); }
.btn-primary:active { transform: scale(0.97); }
.btn-primary:disabled { opacity: 0.4; cursor: not-allowed; box-shadow: none; }

/* D-pad directional button */
.btn-dpad {
  background: var(--surface-alt);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 12px;
  min-height: 56px;
  font: 600 15px var(--font-sans);
  cursor: pointer;
  touch-action: none;
  user-select: none;
  -webkit-tap-highlight-color: transparent;
  transition: all 0.08s ease;
}
.btn-dpad:active, .btn-dpad.pressed {
  background: var(--accent);
  color: #000;
  border-color: var(--accent);
  box-shadow: 0 0 16px var(--accent-glow);
  transform: scale(0.95);
}

/* Danger / E-Stop */
.btn-danger {
  background: rgba(var(--error-rgb), 0.15);
  color: var(--error);
  border: 1px solid rgba(var(--error-rgb), 0.3);
}
.btn-danger:active, .btn-danger.pressed {
  background: var(--error);
  color: #fff;
  box-shadow: 0 0 20px rgba(var(--error-rgb), 0.5);
}
```

### Connection Indicator

```css
.conn-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: var(--text-tertiary);
  transition: background 0.3s ease, box-shadow 0.3s ease;
}
.conn-dot.online { background: var(--success); box-shadow: 0 0 6px rgba(var(--success-rgb), 0.6); }
.conn-dot.connecting { background: var(--warning); box-shadow: 0 0 6px rgba(var(--warning-rgb), 0.6); animation: pulse 1.2s ease infinite; }
.conn-dot.offline { background: var(--error); box-shadow: 0 0 6px rgba(var(--error-rgb), 0.6); }

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
```

### Panels

```css
.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px;
}
```

### Inputs

```css
input[type="text"], input[type="password"] {
  background: var(--surface-alt);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  font: 14px var(--font-sans);
  transition: border-color 0.15s ease;
}
input:focus { outline: 0; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-glow); }
```

## 5. Layout Principles

**Grid System:**
- Desktop: 2-column — 视频左 (65%) | 控制面板右 (35%)
- Tablet (<860px): 单列 — 视频上 + 控制面板下
- Mobile (<560px): 紧凑单列，全宽利用

**Spacing Scale (8px base):**
- xs: 4px, sm: 8px, md: 16px, lg: 24px, xl: 32px

**Container**: max-width 1200px, 居中, 左右 margin 16px

**D-pad Grid:**
```
       [↑ Forward]
[← L]  [■ Stop]  [→ R]
       [↓ Back]
[↺ TL] [⚡E-Stop][↻ TR]
```

## 6. Depth & Elevation

```css
--shadow-sm: 0 1px 3px rgba(0,0,0,0.3);
--shadow-md: 0 4px 12px rgba(0,0,0,0.4);
--shadow-lg: 0 8px 30px rgba(0,0,0,0.5);
```

仅用于面板和按钮按下态。默认扁平，交互时浮现。

## 7. Animation & Interaction

**L1 级别动效：**
- 按钮：`:active` scale(0.95) + 强调色 glow
- 连接指示灯：pulse 动画（连接中）/ 静态常亮（已连接）
- 面板入场：`fadeIn 0.3s ease`（CSS only）
- 视频加载：骨架屏 → 实际画面切换
- Token 面板：连接后自动收起（slide-up 0.3s）

**Reduced motion:**
```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
```

## 8. Do's and Don'ts

**DO:**
1. 视频区域永远是最大的视觉焦点
2. D-pad 按钮按下时有即时视觉反馈（色变 + 缩放）
3. 连接状态用指示灯 + 简短文字，不用大段日志
4. 移动端 D-pad 按钮 ≥ 56px 触控区域
5. 所有文字颜色有足够对比度（WCAG AA 以上）
6. 键盘快捷键在焦点不在输入框内时才生效
7. 所有交互元素有明确的 hover/focus/active 三态
8. Token 输入框支持显示/隐藏切换

**DON'T:**
1. ❌ 不用弹窗/Modal 打断操作流
2. ❌ 不用大段文字显示状态（用指示灯 + 精简标签）
3. ❌ 不用自动播放音频
4. ❌ 不在移动端用 hover 依赖的功能
5. ❌ 不让连接面板占用超过一行的空间
6. ❌ 不用 3D/WebGL/重特效（这是工具，不是演示站）
7. ❌ 不急停按钮与其他按钮颜色相同（红色醒目区分）
8. ❌ 不在按钮上使用 emoji 图标（用 CSS/内联 SVG 图标）

## 9. Responsive Behavior

**Breakpoints:**
- Desktop: > 860px — 双列布局（视频 + 控制面板并排）
- Tablet: 540-860px — 单列布局（视频上，控制面板下，D-pad 水平居中）
- Mobile: < 540px — 紧凑单列（全宽视频，D-pad 占屏幕宽 90%，触控目标 ≥ 56px）

**Touch Targets:**
- 所有可点击元素最小 44×44px（WCAG 标准）
- D-pad 方向键最小 56×56px（拇指操作友好）
- 按钮间距 ≥ 8px（防误触）

**Mobile Adaptations:**
- 连接面板连接后自动收起（释放纵向空间）
- 语音助手面板默认折叠
- 速度滑块放大（方便拖拽）
- 全屏按钮在移动端显眼展示
- `viewport-fit=cover` 适配刘海屏
