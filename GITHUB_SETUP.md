# 推到 GitHub + 开启 Pages + 建个人主页

> **两种方式二选一**（2026-10-02 复核：沙箱到 GitHub 的网络**实测可连通**，
> 早期记录里「443 被拦」已过时）：
>
> - **自己推**：在你本机执行下面第 1–3 步，约 20 分钟。
> - **让我代推**：把仓库地址和一枚有 `repo` 权限的 PAT 给我，我直接执行第 2 步；
>   第 1、3 步（建仓库、开 Pages）仍需在网页上点，因为 GitHub 不支持用接口开 Pages 分支部署之外的全部设置。
>
> 无论哪种方式，第 4 步的 Profile README 都是网页操作。
>
> 已就绪：`README.md`（9 KB 仓库首页，含口径声明与来源三分）、`index.html`（126 行**纯静态** HR 展示页，
> 无 fetch / 无后端依赖，Pages 可直接托管）、`assets/demo_a_detected.png`（真实检测截图）、
> `docs/profile_readme.md`（个人主页草稿）、`.gitignore`（已排除权重与大产物）。

---

## 公开前三项 P0：已处理

| # | 事项 | 状态 | 处置 |
|---|---|---|---|
| 1 | `third_party/humanize-chinese` 上游无独立 LICENSE 文件 | 已处理 | 新增 `third_party/humanize-chinese/LICENSE.NOTICE.md`。据实记录上游 MIT 徽章声明与两处瑕疵（徽章仓库名与 remote 不一致、README 示例 72 分无法复现），附 MIT 全文并注明来源是徽章而非 LICENSE 文件。**未伪造上游 LICENSE，未改动任何上游文件** |
| 2 | `resume_claim`（服务 3 家工业园区 / 2 家 MCN / 99.8%）公开 | 决策：**保留对照** | 保留字段与 `unverifiable` 标注。这是主动区分「可复现实测」与「需原始材料佐证」的证据，与 README 顶部口径声明一致 |
| 3 | 未提交改动 | 待你执行第 2 步 | `PLAN.md` / `README.md` / `portal/*.json` 已改未提交；`index.html` / `assets/` / `docs/` 未跟踪。必须先 commit 再 push |

补充确认：`.gitignore` 已用 `git check-ignore -v` 逐条验证，`models/`(790 MB)、
`outputs/`(304 MB，含 190 MB whl)、`.venv/` 全部命中忽略规则，**`git add -A` 安全**。
仓库跟踪文件共 146 个 / 约 1.6 MB。`.env` 已忽略，`.env.example` 中 `DASHSCOPE_API_KEY=` 为空，**无密钥泄漏**。

---

## 第 1 步：新建项目仓库

1. 登录 github.com → New repository。
2. 仓库名：`interview-demo`，选 **Public**。
3. **不要**勾选 "Add a README"（我们已有）。
4. Create repository，记下地址：`https://github.com/<用户名>/interview-demo.git`

## 第 2 步：提交并推送

```powershell
cd E:\interview-demo
git status          # 确认没有 models/ outputs/ .venv/ 混进来
git add -A
git commit -m "docs: 展示页/素材入库 + 第三方许可说明 + 个人主页草稿"
git remote add origin https://github.com/<用户名>/interview-demo.git
git push -u origin master
```

> 鉴权：用 GitHub 个人访问令牌（PAT）当密码。若未配置过身份，先执行
> `git config --global user.name "你的名字"` 与 `git config --global user.email "你的邮箱"`。

## 第 3 步：开启 Pages（作品链接）

1. 仓库页 → **Settings → Pages**
2. Source 选 **Deploy from a branch**；Branch 选 **master**，目录 **/ (root)**
3. Save。约 1 分钟后访问：

```
https://<用户名>.github.io/interview-demo/
```

这就是填进简历「作品链接」的网址。

## 第 4 步：建个人主页（Profile README）

面试官点进你的 GitHub，第一眼看到的是这一层，比独立个人站更常被看到。

1. New repository，仓库名必须**与你的用户名完全相同**：`<用户名>/<用户名>`，Public。
2. 勾选 "Add a README"。
3. 把本地 `docs/profile_readme.md` 的内容整段复制进去，
   **把两处 `<用户名>` 替换成你真实的 GitHub 用户名**（一处是项目链接，一处是底部链接）。
4. Commit。

之后访问 `https://github.com/<用户名>` 即是你的个人主页。

> **不建议**再单独建一个 `username.github.io` 独立作品集站：
> 它和第 3 步的展示页内容重复，需另建内容与维护，约 1–2 天，收益增量最低。

---

## 投放前自检

- [ ] `https://<用户名>.github.io/interview-demo/` 能打开（不是 404）
- [ ] 展示页正常渲染（浅色、中文、四个模块卡片），检测截图显示正常
- [ ] `https://github.com/<用户名>` 个人主页渲染正常，两处项目链接可点通
- [ ] 仓库首页顶部能看到「口径声明」三段
- [ ] 简历里注明「技术验证版」，与仓库口径一致
- [ ] 随机抽一个数字（如 13.99 ms）能在 `portal/measurements.json` 里找到出处
