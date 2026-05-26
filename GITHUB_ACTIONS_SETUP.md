# GitHub Actions 邮件推送设置

这个仓库已经包含每天北京时间 08:00 自动发送文献邮件的 workflow：

- `.github/workflows/daily-paper-digest.yml`
- `scripts/daily_paper_digest.py`

## 需要配置的 Secrets

在 GitHub 仓库页面进入：

`Settings -> Secrets and variables -> Actions -> New repository secret`

添加以下 secrets：

| Secret 名称 | 值 |
| --- | --- |
| `SMTP_USER` | `xiangyu526@126.com` |
| `SMTP_PASS` | 126 邮箱的 IMAP/SMTP 授权码 |
| `MAIL_FROM` | `xiangyu526@126.com` |
| `MAIL_TO` | `xiangyu526@126.com` |
| `NCBI_EMAIL` | 你的邮箱，建议填写，用于 NCBI E-utilities 请求标识 |
| `NCBI_API_KEY` | 可选；没有也能运行 |

不要把 SMTP 授权码写入仓库文件。

## 时间

GitHub Actions 的 cron 使用 UTC 时间。当前配置为：

```yaml
cron: "0 0 * * *"
```

也就是北京时间每天 08:00。

## 手动测试

配置完 secrets 后，可以在 GitHub 仓库页面进入：

`Actions -> Daily paper digest -> Run workflow`

手动运行一次。如果成功，你会收到邮件；仓库里会更新 `data/sent_pmids.json`，用于减少重复推送。

## 注意

GitHub Actions 不依赖你的电脑开机。只要仓库存在、Actions 启用、secrets 正确，云端会按计划运行。
