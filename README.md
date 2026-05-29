# WayinVideo Protocol Register Bot

WayinVideo (wayin.ai) 协议逆向 — 批量邀请注册器

## 功能

- 自动注册母号（邀请号）
- 每个母号自动邀请 N 个子号
- 临时邮箱集成（chatgptmail）
- 验证码自动收取
- 域名不支持时自动换域名重试
- 独立 session 保证邀请追踪有效

## 依赖

```bash
pip install curl_cffi
```

## 使用

1. 编辑 `wayin_batch.py` 顶部配置区：

```python
NUM_INVITERS = 10           # 注册多少个母号
INVITES_PER_INVITER = 10    # 每个母号邀请多少人
REGISTER_DELAY = 2          # 注册间隔（秒）
```

2. 如需代理，取消注释 `PROXY_CONFIG`：

```python
PROXY_CONFIG = {
    "http": "http://127.0.0.1:7890",
    "https": "http://127.0.0.1:7890",
}
```

3. 运行：

```bash
python3 wayin_batch.py
```

4. 结果保存在 `/tmp/wayin_batch_result.json`

## 博客文章

详细逆向分析过程见博客文章：[WayinVideo 协议逆向：从零到批量注册的完整实录](https://keggin.tech/archives/wayinvideo-register-protocol)

## 声明

本项目仅用于协议研究与技术学习，请遵守目标网站的使用规范。
