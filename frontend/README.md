# SocialDog 前端

技术栈：React 19 + Vite + TypeScript + Tailwind CSS 4；数据请求用 TanStack Query；后端还没好之前用 MSW 在浏览器里模拟。

## 开发

```sh
cd frontend
npm install
npm run dev          # http://localhost:5173 ，默认使用模拟后端
```

模拟账号：`carrey / demo`（管理员）、`lin / demo`（研究员）。邀请页可以用 `/invite/demo-invite` 体验。模拟数据保存在浏览器的 localStorage 中，在控制台执行 `localStorage.removeItem("socialdog-mock-v1")` 可以清空。

模拟数据是程序生成的两年历史订单，截断日为 2023-06-30，不是真实经营数据。其中拿铁在截断前降过一次价，美式在截断后涨过一次价（作为样本外测评的“答案”）。模拟 agent 只读截断前的数据，按关键词选择工具，问题里包含“失败”时会模拟报错。

接入真实后端：

```sh
VITE_USE_MOCK=0 VITE_API_PROXY=http://127.0.0.1:8000 npm run dev
```

## 检查与构建

```sh
npm run typecheck
npm test             # 用 msw/node 跑接口契约测试和页面冒烟测试
npm run build        # 输出到 dist/，由 Caddy 直接提供
```

生产构建不包含模拟后端。

## 目录

| 路径 | 内容 |
| --- | --- |
| `src/api/types.ts` | 前后端接口约定（后端的 OpenAPI 须与之一致），说明见 `../docs/接口约定.md` |
| `src/api/client.ts` | 带 CSRF 头的请求封装 |
| `src/mocks/` | MSW 模拟后端：合成数据、模拟 agent、状态与接口 |
| `src/pages/` | 登录/邀请、研究对话、数据核对、测评、我的测评、管理后台 |
| `src/components/` | 布局、通用控件、回答与依据渲染、数据核对查询表 |
