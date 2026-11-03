# 邮箱注册与密码重置（QQ 邮箱）

应用账号与学校连接独立：学校会话过期不影响应用登录。新用户用邮箱验证码注册；已有邮箱/手机号账号仍可用密码登录。只有手机号、没有邮箱的历史账号不能通过邮箱找回密码。

## 推荐：配置文件

复制根目录 `.env.example` 为 `.env`（若已有 `.env` 请直接编辑，不要覆盖）。填写 `SMTP_USER` 和 `SMTP_FROM` 为同一个完整 QQ 邮箱地址，`SMTP_PASSWORD` 为 SMTP 授权码，`EMAIL_CODE_SECRET` 为至少 32 字符的随机密钥。

数据库地址 `DATABASE_URL`、模型密钥 `ZHIPU_API_KEY` 也可写在此文件里；已有终端变量优先于文件。文件使用 UTF-8 编码，值按字面读取，不进行 `${...}` 替换。修改后重启后端。真实 `.env` 已被 Git 忽略。

配置加载使用 [python-dotenv](https://bbc2.github.io/python-dotenv/)，固定读取项目根目录，不受启动工作目录影响。新环境先执行 `python -m pip install -r requirements.txt`。

## 本地启用

先停止后端，在已激活 `(agent)` 的项目根目录终端中运行数据库迁移：

```powershell
python -m alembic upgrade head
```

在 QQ 邮箱网页设置中开启 SMTP 服务并生成授权码。配置使用授权码而非 QQ 登录密码；不要把授权码发到聊天或提交进 Git。
[QQ 邮箱授权码说明](https://help.mail.qq.com/detail/106/985)。

在同一个后端终端中执行以下配置（`Read-Host` 会提示输入，不会回显授权码）：

```powershell
$env:SMTP_HOST = 'smtp.qq.com'
$env:SMTP_PORT = '465'
$env:SMTP_SECURITY = 'ssl'
$env:SMTP_USER = Read-Host '请输入发信 QQ 邮箱完整地址'
$env:SMTP_FROM = $env:SMTP_USER
$mailCredential = Get-Credential -UserName $env:SMTP_USER -Message '密码栏填写 QQ 邮箱 SMTP 授权码'
$env:SMTP_PASSWORD = $mailCredential.GetNetworkCredential().Password
$env:EMAIL_CODE_SECRET = python -c "import secrets; print(secrets.token_hex(32))"
$env:ISPACE_LOCAL_LOGIN = '1'
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

后端会自动读取项目根目录 `.env`，终端已有环境变量优先。以上环境变量只对当前终端及其子进程有效。正式部署应通过部署环境保存稳定的 `EMAIL_CODE_SECRET`（至少 32 字符）；更换该密钥会使尚未使用的验证码失效。SMTP 支持 `ssl` 或 `starttls`，均校验证书，不支持明文传输。

前端保持 `npm run dev`，刷新即可。在登录弹窗点击“立即注册”或“忘记密码？”。注册/重置均需邮箱、6 位验证码、两次输入一致的新密码（8–128 字符）。

## 接口与行为

- `POST /api/user/email-code`：`{"email":"student@example.com","purpose":"register"}` 或 `reset`。返回 202 表示请求已接收，不保证投递成功，不返回验证码。
- `POST /api/user/create`：`username`、`email`、`password`、`code`，以及选填的 `phone`。手机号留空按未填写处理；填写时接受 7–15 位数字及可选的前导 `+`，必须唯一。无论是否填写手机号，邮箱验证码均必填。手机号未做短信验证，可作为密码登录账号，密码找回仍使用邮箱。
- `POST /api/user/reset-password`：`email`、`password`、`code`。验证成功后更换密码，撤销该用户所有旧登录会话，发送通知邮件，要求重新登录。
- 验证码 10 分钟有效、最多猜错 5 次、一次性使用；注册与重置验证码不能混用。保存带密钥的摘要，不保存明文验证码。
- 同一邮箱 60 秒内最多一次发送请求、24 小时窗口内最多 10 次，同一来源地址每小时最多 30 次。注册与重置共享邮箱限额。
- 使用数据库行锁序列化验证码消费和限额；生产数据库使用 PostgreSQL。SMTP 失败不提交新验证码，只记不含凭据的错误类别日志。
- 发信在进程内后台任务中执行：后端重启可能中断投递，请等待冷却后重发。后续如需可靠投递可接入持久化队列。
- 来源限额使用后端看到的客户端地址。Vite 本地代理会共享来源限额；公网部署应正确配置可信代理，而非直接相信任意请求头。
- 未配置邮件服务时返回 503；已有账号登录仍可用。若长时间收不到邮件，检查垃圾邮件、SMTP 授权码及后端 `Email verification delivery failed` 日志。

参考：[OWASP 密码找回指南](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html)。
