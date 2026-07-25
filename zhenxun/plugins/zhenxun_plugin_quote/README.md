# 群聊语录

QQ群语录库插件，支持上传聊天截图、回复消息生成语录、关键词查询、统计和管理。

## 上传语录

- `上传语录 [图片] [tag/@用户 ...]`：上传当前消息中的图片。
- 回复图片后发送 `上传语录 [tag/@用户 ...]`：上传被回复图片。
- 回复合并转发后发送 `上传语录 [tag/@用户 ...]`：按节点顺序上传其中所有顶层图片。

批量上传会逐张执行大小校验、查重、文字识别和保存。单张失败或重复不会中断后续图片，命令结束后会汇总成功、重复和失败数量。命令中的手动 tag 会应用到本批次的所有图片。

## 文字识别配置

普通上传继续使用 `AI_ENABLED`、`OCR_AI_MODEL`、`OCR_ENGINE` 和 `OCR_USE_GPU` 控制的 AI 优先、本地 OCR 降级策略。

合并转发批量上传使用独立配置 `BATCH_UPLOAD_OCR_MODE`：

- `paddleocr`：仅使用 PaddleOCR，默认值。
- `easyocr`：仅使用 EasyOCR。
- `ai`：仅使用已配置的视觉模型。
- `inherit`：继承普通上传的 AI 优先策略。
- `disabled`：关闭批量上传的文字识别。

显式选择本地 OCR 时不会调用 AI。`paddleocr` 与 `easyocr` 共用 `OCR_USE_GPU` 配置。

## 依赖与限制

- 仅支持 OneBot V11 群聊。
- 合并转发只展开顶层节点，不递归读取节点中的嵌套合并转发。
- 每张图片独立受 `QUOTE_MAX_IMAGE_SIZE_MB` 限制。
- PaddleOCR 依赖由插件现有 `requirements.txt` 提供；EasyOCR 为可选依赖。
