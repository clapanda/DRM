[English](README.md) | 简体中文

# 文档需求维护工具（DRM）

> 面向既有 Word 需求文档、采用确定性处理并受审批约束的维护工具。

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-Apache--2.0-D22128)
![Status](https://img.shields.io/badge/status-MVP-blue)

Document Requirement Maintenance（DRM）是一款本地 Python 工具，用于检查、检索、比较、规划、审批、应用和验证 Word 需求文档中的小范围变更。DOCX 始终是事实来源；导出的 Markdown 仅用于辅助阅读。

当前 MVP 支持确定性的段落插入与替换。它不会定义 OFS ALM 业务规则，执行 `inspect`、`search` 或 `plan` 时也不会修改 DOCX。

## 仓库策略

本公开仓库只包含源代码、合成测试材料和文档。真实需求文档、文档提取结果、生成的 DOCX 以及可能包含敏感信息的变更计划默认不进入 Git。首次提交前请检查 [`.gitignore`](.gitignore)。

## 安装

需要 Python 3.11 或更高版本。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
```

如果只想在仓库内运行而不安装：

```powershell
$env:PYTHONPATH = "src"
python -m drm --help
```

## 使用流程

请将业务文档放在 `work/` 等本地目录中；DOCX 和生成结果默认会被 Git 忽略。

经过审阅且不含敏感信息的变更计划可以存放在 `plans/` 下并纳入 Git 历史。包含机密原文摘录的计划应保留在被忽略的 `work/` 目录中。

### 1. 检查文档

```powershell
drm inspect work\master.docx --output-dir work\master-inspection --profile ofs-alm
```

该命令会生成 `document.md`、`structure.json`、`media_index.json`、`extraction_report.json`，并将提取文件写入 `media/`。`structure.json` 中的锚点只对当前文档版本有效。

无需创建完整检查目录，也可以直接检索指标码或文本：

```powershell
drm search work\master.docx FE250 --profile ofs-alm --output work\fe250-search.json
```

比较两份既有需求文档，且不修改任何源文件：

```powershell
drm compare work\master-v1.docx work\master-v2.docx `
  --profile ofs-alm `
  --output work\comparison-details.json `
  --result-file work\comparison-result.json
```

### 机器可读结果

当 Agent 或其他程序调用 DRM 时，为任意操作增加 `--result-file <path>`。精简结果始终包含 `schema_version`、`run_id`、`operation`、`status`、`source_document`、`findings`、`artifacts`、`warnings` 和 `errors`。大段文档内容保留在对应的产物文件中。

```powershell
drm result-schema --output work\operation-result.schema.json `
  --result-file work\result-schema-run.json
```

### 2. 生成变更计划

参考[合成示例](examples/fe250_synthetic_proposal.json)创建提案 JSON，然后解析锚点并校验：

```powershell
drm plan work\master.docx `
  --proposal work\proposal.json `
  --output work\change-plan-draft.json `
  --document-version v1 `
  --profile ofs-alm
```

规划器会补充目标文件哈希和精确锚点。如果目标匹配零次或多次，该变更会被标记为 `conflict`，不能审批。接入其他规划器时，可以生成权威 JSON Schema：

提案中预先填写的任何 `approved` 值都会被重置为 `pending`；只有通过下一节所述的独立审阅步骤才能获得审批。

```powershell
drm schema --output work\change-plan.schema.json
```

每项变更都会记录来源、目标、操作、拟议内容、理由、证据、置信状态、审批状态和验证规则。如果业务依据尚未确认，应使用 `ambiguous_needs_confirmation` 或 `insufficient_evidence`。

### 3. 审阅与审批

审阅完整的草案 JSON，特别是证据、目标位置、置信状态和拟议文本。审批必须通过独立命令完成：

```powershell
drm approve work\change-plan-draft.json `
  --change-id FE250-DEMO-001 `
  --change-id FE250-DEMO-002 `
  --approver "reviewer-name" `
  --output work\change-plan-approved.json
```

只有明确指定的变更项会获得审批。存在冲突的项目不能审批。歧义或证据不足的项目需要显式使用 `--allow-uncertain`，通常应保持待审批状态，直至问题澄清。

`drm approve` 还会记录每个已审批变更的摘要。对于手工伪造的 `approved` 状态，或审批后被修改的受保护内容，`apply` 会拒绝执行。该审批记录提供确定性的完整性检查，但并非密码学身份签名。

### 4. 应用变更

```powershell
drm apply work\master.docx `
  --plan work\change-plan-approved.json `
  --output work\master-v2.docx `
  --audit-log work\master-v2.audit.json
```

源文件哈希必须与计划一致。DRM 会先保存临时 DOCX，验证其 ZIP 包结构并确认源文件未发生变化，然后才发布输出。除非明确使用 `--force`，否则不会覆盖已有输出。所有跳过项和待审批项都会写入审计日志。

### 5. 验证结果

```powershell
drm validate work\master.docx work\master-v2.docx `
  --plan work\change-plan-approved.json `
  --output work\master-v2.validation.json
```

验证通过表示：输出正文和样式等于原文加上已审批的 MVP 变更，原始文件哈希一致，DOCX 可以作为 ZIP 包正常打开，且已索引媒体资源得到保留。包级元数据差异会与实质内容差异分开报告。

## OFS ALM 配置

`src/drm/profiles/ofs_alm.json` 可以识别 FE 指标码，以及与明细展开、汇总、现金流事件、重定价、摊还、合约字段、公式和余额相关的术语。其 FE250 配置仅用于标记验收案例，`business_definition` 被有意设置为 null。

如需增加其他业务配置，可以复制该 JSON 结构，设置新的 `profile_id`，并定义识别模式和主题术语。不要把计算规则写入核心解析器。也可以通过 `--profile` 传入配置文件路径。

## 测试

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

测试套件使用合成 DOCX，覆盖多级标题、中文文本、合并单元格、图片、FE 指标码、精确插入与替换、歧义定位、未审批变更检测、原始文件保护、可追溯性和连续版本处理。

可以单独创建合成测试文件：

```powershell
python tools\create_test_fixture.py work\synthetic-master.docx
```

## 已知限制

- MVP 只写入段落。表格可以解析、检索和验证；行与单元格修改虽有模型定义，但当前会作为不支持的操作跳过。
- 浮动文本框文本、域指令、公式、书签和交叉引用会得到保留，但尚不能完整解释。
- 包含高风险 OOXML 的段落不会被替换；在不修改其内容的前提下，仍可在相邻位置插入内容。
- 编号、修订、批注、页眉、页脚及复杂样式继承需要经过更全面的第二阶段测试，才能开放写入支持。
- 视觉比较不是运行时必需依赖，但发布测试仍应将有代表性的修改前后 DOCX 渲染为 PNG，并逐页检查。
- 审批信息记录在 JSON 中，但没有使用密码学签名。

模块边界和验证设计见[架构说明](docs/architecture.md)。

## 许可证

DRM 使用 [Apache License 2.0](LICENSE) 发布。该许可证允许商业使用、修改和再分发，提供明确的专利授权，并要求保留许可证及署名声明。
