# Q 轮批量提问

在项目根目录执行，`ProjectId` 填当前已上传完资料的项目 ID（不根据旧题集推断）：

```powershell
.\scripts\run_q_round.ps1 -ProjectId 14 -DryRun
.\scripts\run_q_round.ps1 -ProjectId 14
```

`14` 仅为示例，请替换为实际项目。默认读取 `C:\Users\imada\Desktop\Q.md`，服务地址为 `http://127.0.0.1:8000`。可用 `-Questions`、`-BaseUrl` 覆盖。脚本默认读取被 Git 忽略的 `test_files/qa_credentials.local.json`（`account`、`password`），可用 `-CredentialsFile` 指定其他本地配置；配置不存在时交互询问账号密码。密码不输出到控制台或结果文件。`-DryRun` 只检查 20 道题，不读取登录配置、不登录、不发送请求。

脚本直接使用项目已有文档，不上传或重建文档；项目为空、存在未完成或失败文档时停止。题目串行发送，每题 `conversation_id=null`，检索整个项目。不会根据题目标注的文档缩小范围，也不会把参考答案发送给接口。问答接口会按现有行为保存每题的会话。

结果保存在 `test_files/batch_runs/<时间_随机后缀>/`：

- `Q01.json` 等：原始请求、完整响应、参考答案、HTTP 状态和耗时。
- `results.jsonl`：全部请求记录，含失败记录；每题结束立即写入。
- `ragas.jsonl`：仅接口成功的样本，包含 `user_input`、`response`、`retrieved_contexts` 和题集 `reference`，尚未计算评估分数。
- `report.md`：回答、来源和参考答案，供人工核对。
- `run.json`、`questions.json`、`documents.json`：本轮参数、题集及项目文档快照。

接口成功不等于答案正确。参考答案保留原题集文字（包括“与 Q01 一致”等表述），正式自动评估前应核对或补全。网络超时不会自动重试，避免重复创建会话；已完成结果保留，脚本不提供断点续跑。

需要修改召回比例或超时，可以直接使用 Python 入口：

```powershell
.\agent\Scripts\python.exe -X utf8 scripts/batch_qa.py --questions "C:\Users\imada\Desktop\Q.md" --skip-upload --project-id 14 --candidate-percentage 0.4 --request-timeout 600
```
