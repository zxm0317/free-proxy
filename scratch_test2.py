from python_scripts.response_normalizer import _normalize_tool_calls

text = """**正在写入开发表，请稍等。**🐾

{
"action": "file_write",
"path": "/home/ubuntu/.openclaw/workspace/business-groups/portable-pressure-washer-client-dev-v3.md",
"content": "# 锂电手持高压清洗机客户开发表\\n\\n| 公司名 | 官网 | 国家 | 客户类型 | 产品 Fit 证据 | 推荐开发角度 |\\n| --- | --- | --- | --- | --- | --- |\\n| Greenworks | greenworkstools.com | 美国 | 工具品牌 | 24V/40V 锂电平台，有便携清洗机 | 针对其 24V 平台推高性价比手持清洗机 |\\n| Sun Joe | sun-joe.com | 美国 | 工具品牌 | 户外轻清洁头部，多款便携清洗机 | 针对其入门级市场推差异化 OEM 方案 |\\n| EGO Power+ | egopowerplus.com | 美国 | 工具品牌 | 高端锂电户外工具，有便携清洗机 | 针对其高端用户推特定场景清洗机 |"
}"""

res = _normalize_tool_calls('foo', text)
print(res)
