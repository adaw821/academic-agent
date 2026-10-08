# Docling 内置图片描述实验

分支：`codex/test-docling-picture-description`；依赖版本：`docling==2.119.0`。

## 当前流程

PDF/PPT → Docling 转换与内置图片描述 → 可选 PNG/manifest 留档 → Markdown/章节分片 → 向量化入库。

`app/services/docling_images.py` 只配置 `PictureDescriptionApiOptions` 和检查空描述，不自行发送 HTTP 请求或回填节点。模型仍为 `glm-4.6v-flash`，密钥仍为 `ZHIPU_API_KEY`；提示词改为直接输出 Markdown，要求有字完整转录、没字一句话概括。原 `image_ocr.py`、`image_enrichment.py` 不再参与上传执行路径。TXT、DOCX 的处理范围保持原样。

PDF 设置 `do_ocr=False`、`do_picture_description=True`、`generate_picture_images=True`。PPT/PPTX 使用 `ConvertPipelineOptions` 开启内置描述。识别不依赖图片输出目录是否存在。

## OCR 选择

本实验默认关闭 PDF OCR，以便观察单独使用图片描述的效果。原生文字层照常提取。扫描版 PDF 需单独验证：只有被 Docling 提取为图片且能取得像素的节点才会被描述，不能用这个实验承诺整页 OCR 覆盖。需要扫描件支持时再单独测试 `do_ocr=True`、识别语言和重复文本问题。

## 与旧流程的差异

- 内置描述配置：单批 1 张、并发 1、超时 120 秒、输出上限 8192 token。这里的并发限制不是跨文档、跨进程的全局服务商配额控制。
- `picture_area_threshold=0.0`，避免默认 5% 页面面积门槛漏掉小型文字图；可能增加装饰图调用量。原像素尺寸/灰度筛选现在只影响 PNG 留档，不影响已完成的内置描述请求。
- Docling API 助手自带 429/500/502/503/504 重试，遵守 `Retry-After`；不再使用原自定义重试和共享锁。
- Docling 对部分 API 异常返回空描述。本项目检查已生成但为空的描述并报错，阻止分片入库；没有像素或被内置规则跳过的节点没有描述，不能视为识别成功。
- 本版本内置描述回填不保留 API 的 `finish_reason`。因此本实验没有旧流程严格拒绝 `length` 截断输出的保证；空描述检查不能检测非空截断结果。测试长图时须对照原图检查完整性。
- 图片留档在转换之后；若转换或描述校验失败，本轮可能还没有生成 manifest。描述耗时计入 `chunk.docling_convert`，不再有原自定义逐图请求计时。

## 验证

重启后重新上传测试文件，已有数据库分片不会自动更新。使用普通文字 PDF、含文字截图的 PDF/PPTX、无文字图片、长表格图片分别检查分片内容和来源页码；扫描件独立记录。先用测试集的模拟 API 验证接线，再用实际文件评估模型效果。

```powershell
agent/Scripts/python.exe -m unittest discover -s tests -p test_docling_images.py
agent/Scripts/python.exe -m unittest discover -s tests -p test_image_extraction.py
agent/Scripts/python.exe -m unittest discover -s tests -p test_pdf_chunking.py
agent/Scripts/python.exe -m unittest discover -s tests -p test_ppt_chunking.py
agent/Scripts/python.exe -m unittest discover -s tests -p test_workflow_graphs.py
```

这些测试模拟视觉 API，不调用真实服务，也不证明真实文档的识别质量。
