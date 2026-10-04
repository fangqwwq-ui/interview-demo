# 许可证说明（本项目补充）

> **本文件由本项目（interview-demo）添加，不是上游仓库的文件。**
> 上游 `humanize-chinese` **未提供独立的 `LICENSE` 文件**，本文件用于补齐溯源与许可声明，
> 不改变、不替代上游原有的任何文件（上游 `README.md` 保持原样，未改写、未重命名）。

## 上游信息

| 项 | 值 |
|---|---|
| 组件名 | humanize-chinese |
| 上游仓库 | https://github.com/yukinotech/humanize-chinese |
| 引入 commit | `efdefaa0ead85cfde3fe36450a06cbe995792bda`（2026-04-02） |
| 声明版本 | `2.1.0`（见上游 `package.json`） |
| 引入日期 | 2026-10-01 |
| 本目录改动 | **无。** 原样复制，未修改任何上游文件；复制时排除了 `.git` 与 `__pycache__` |

## 许可声明

上游在 `README.md` 中以徽章形式声明 **MIT License**。按 MIT 的通行要求，
本目录应包含版权声明与许可全文；但上游仓库内确实不存在该文件。

本项目的处理：

1. **据实记录**，不伪造一份上游 LICENSE —— 伪造许可文件比缺失许可文件更严重。
2. 在下方完整附上 MIT 许可文本，并注明其来源为「上游 README 徽章声明」，
   而非来自上游的 LICENSE 文件。
3. 本仓库对该组件的使用范围：**仅作检测打分（度量仪器）**，未使用其改写能力
   （实测其规则改写会破坏中文语法，详见 `third_party/README.md`）。
4. 若需正式商用或再分发，建议先向上游作者确认许可。

## 已知瑕疵（如实披露）

- 上游 README 徽章指向的仓库名（`voidborne-d/humanize-chinese`）与
  实际 git remote（`yukinotech/humanize-chinese`）**不一致**。
- 上游 README 中标注的示例分数（72 分）在本 commit 上**无法复现**（实测 0 分）。

## MIT License 文本

以下为 MIT 许可的通行文本。其适用性依据为上游 README 的 MIT 徽章声明；
**上游仓库未提供独立 LICENSE 文件，这是上游的声明缺口，不是本项目的选择。**

```
MIT License

Copyright (c) 2025 humanize-chinese contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
