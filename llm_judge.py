"""LLM 危险等级评估 —— 智能判断权限请求是否需要人工审批"""

import json
import re
import asyncio
import aiohttp
from astrbot.api import logger

DANGER_ASSESSMENT_SYSTEM_PROMPT = """你是一个安全审批助手。根据工具名称和参数判断该操作是否危险。

# 危险操作 (dangerous)：
- 破坏性 Shell 命令：rm -rf, chmod, chown, sudo, pip/npm install -g, systemctl, docker rm, format/clean/wipe 磁盘
- 修改项目外系统文件（/etc, /boot, C:\\Windows, ~/.ssh, ~/.gnupg）
- Git force push / hard reset / 修改已推送的历史
- 安装系统级软件包（apt install, brew install, winget install）
- 修改环境变量、PATH、注册表
- 读取 .env / credentials / 私钥 / token / secret 等敏感文件
- 发起网络请求到内网 / localhost 非标准端口
- 删除项目外文件或批量删除无版本控制的文件
- 任何可能造成数据丢失或安全风险的操作

# 安全操作 (safe)：
- 读取项目内普通文件 (Read)
- 代码搜索 (Grep, Glob)
- Git 只读操作 (status, log, diff, show)
- 运行测试/检查 (npm test, pytest, cargo check, go vet)
- 在项目目录内写入/编辑/创建文件 (Write, Edit)
- 列出目录内容 (ls, dir, tree)
- 网页搜索 (WebSearch)
- 读取项目内配置文件 (tsconfig.json, package.json, Cargo.toml 等)
- npm/pip/cargo 的 --dry-run / --help 类查询
- 格式化/整理代码

# 规则：
1. 只输出一个词："safe" 或 "dangerous"
2. Bash 命令必须检查参数内容，不能仅凭工具名判断
3. 不确定时输出 "dangerous"
4. Write/Edit 检查目标路径是否在项目目录内
"""


def build_assessment_prompt(tool_name: str, arguments: dict) -> str:
    args_str = json.dumps(arguments, ensure_ascii=False, indent=2) if arguments else "（无参数）"
    return f"请判断以下工具调用是否安全：\n\n工具：{tool_name}\n参数：{args_str}"


def parse_verdict(response: str) -> str:
    """从 LLM 回复中提取判断结果，默认返回 dangerous"""
    text = response.strip().lower()
    # 直接前缀匹配
    if text.startswith("safe"):
        return "safe"
    if text.startswith("dangerous"):
        return "dangerous"
    # 正则搜关键词
    match = re.search(r'\b(safe|dangerous)\b', text)
    if match:
        return match.group(1)
    # 中文模糊匹配（兜底）
    if "安全" in text and "不安全" not in text and "危险" not in text:
        return "safe"
    return "dangerous"


async def call_llm_judge(
    endpoint: str,
    api_key: str,
    model: str,
    tool_name: str,
    arguments: dict,
    timeout: int = 10,
) -> str:
    """调用 OpenAI 兼容 API 评估危险等级。返回 "safe" 或 "dangerous"。"""
    prompt = build_assessment_prompt(tool_name, arguments)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": DANGER_ASSESSMENT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 10,
        "temperature": 0,
    }

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    url = endpoint.rstrip("/") + "/v1/chat/completions"

    try:
        timeout_obj = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=timeout_obj) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(f"[LLM法官] API {resp.status}: {body[:200]}")
                    return "dangerous"

                data = await resp.json()
                content = data["choices"][0]["message"]["content"]
                verdict = parse_verdict(content)
                logger.info(f"[LLM法官] {tool_name} → {verdict}")
                return verdict

    except asyncio.TimeoutError:
        logger.warning(f"[LLM法官] 超时 ({tool_name})，默认拒绝")
        return "dangerous"
    except Exception as e:
        logger.warning(f"[LLM法官] 调用异常: {e}")
        return "dangerous"
