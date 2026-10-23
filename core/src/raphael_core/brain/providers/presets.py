"""Provider presets, kept as data for the settings UI and compatible client.

Confidence describes the remembered documented endpoint, not live availability or account
entitlement. The kinds in VERIFIED had their default root checked against the provider's own
current documentation on 2026-10-23; the others are still to be checked.
"""

import importlib.util


def preset(kind, name, group, url, docs, *, fields=("key",), auth="bearer",
           confidence="high", available=True):
    return {"kind": kind, "name": name, "group": group, "fields": list(fields),
            "default_base_url": url, "available": available, "docs_url": docs,
            "verified": False, "local_context_hint": group == "local",
            "auth_style": auth, "url_confidence": confidence}


KINDS = [
    preset("openrouter", "OpenRouter", "gateway", "https://openrouter.ai/api/v1", "https://openrouter.ai/keys"),
    preset("openai", "OpenAI", "official", "https://api.openai.com/v1", "https://platform.openai.com/api-keys"),
    preset("anthropic", "Anthropic (Claude)", "official", "https://api.anthropic.com", "https://console.anthropic.com/", auth="x-api-key"),
    preset("google", "Google Gemini", "official", "https://generativelanguage.googleapis.com", "https://aistudio.google.com/apikey"),
    preset("azure_openai", "Azure OpenAI", "official", "", "https://learn.microsoft.com/azure/ai-services/openai/", fields=("key", "base_url", "api_version"), auth="api-key", confidence="account endpoint required"),
    preset("vertex", "Google Vertex AI", "official", "", "https://cloud.google.com/vertex-ai/generative-ai/docs/start/openai", fields=("service_account", "project", "location"), auth="oauth-service-account", confidence="derived from project and location", available=importlib.util.find_spec("cryptography") is not None),
    preset("bedrock", "Amazon Bedrock", "official", "", "https://docs.aws.amazon.com/bedrock/latest/userguide/", fields=("key_id", "secret", "region"), auth="sigv4", confidence="derived from region"),
    preset("openai_compatible", "Custom (OpenAI-compatible)", "gateway", "", "https://platform.openai.com/docs/api-reference/chat", fields=("base_url", "key"), confidence="owner endpoint required"),
    preset("litellm", "LiteLLM", "gateway", "", "https://docs.litellm.ai/docs/", fields=("base_url", "key"), confidence="owner endpoint required"),
    preset("bifrost", "Bifrost", "gateway", "", "https://docs.getbifrost.ai/", fields=("base_url", "key"), confidence="owner endpoint required"),
]

# kind, display name, compatible API root, documentation, endpoint confidence
HOSTED = [
    ("portkey", "Portkey", "https://api.portkey.ai/v1", "https://portkey.ai/docs/", "high"),
    ("vercel", "Vercel AI Gateway", "https://ai-gateway.vercel.sh/v1", "https://vercel.com/docs/ai-gateway", "high"),
    ("nebius", "Nebius Token Factory", "https://api.tokenfactory.nebius.com/v1", "https://docs.tokenfactory.nebius.com/", "high"),
    ("groq", "Groq", "https://api.groq.com/openai/v1", "https://console.groq.com/docs/openai", "high"),
    ("deepseek", "DeepSeek", "https://api.deepseek.com/v1", "https://api-docs.deepseek.com/", "high"),
    ("mistral", "Mistral", "https://api.mistral.ai/v1", "https://docs.mistral.ai/", "high"),
    ("xai", "xAI", "https://api.x.ai/v1", "https://docs.x.ai/", "high"),
    ("fireworks", "Fireworks AI", "https://api.fireworks.ai/inference/v1", "https://docs.fireworks.ai/", "high"),
    ("novita", "Novita AI", "https://api.novita.ai/v3/openai", "https://novita.ai/docs/", "high"),
    ("deepinfra", "DeepInfra", "https://api.deepinfra.com/v1/openai", "https://deepinfra.com/docs/", "high"),
    ("together", "Together AI", "https://api.together.xyz/v1", "https://docs.together.ai/", "high"),
    ("perplexity", "Perplexity", "https://api.perplexity.ai", "https://docs.perplexity.ai/", "high"),
    ("cerebras", "Cerebras", "https://api.cerebras.ai/v1", "https://inference-docs.cerebras.ai/", "high"),
    ("sambanova", "SambaNova", "https://api.sambanova.ai/v1", "https://docs.sambanova.ai/", "high"),
    ("hyperbolic", "Hyperbolic", "https://api.hyperbolic.xyz/v1", "https://docs.hyperbolic.xyz/", "high"),
    ("nvidia", "NVIDIA NIM", "https://integrate.api.nvidia.com/v1", "https://docs.api.nvidia.com/nim/", "high"),
    ("huggingface", "Hugging Face Router", "https://router.huggingface.co/v1", "https://huggingface.co/docs/inference-providers/", "high"),
    ("moonshot", "Moonshot (international)", "https://api.moonshot.ai/v1", "https://platform.moonshot.ai/docs/", "high"),
    ("moonshot_cn", "Moonshot (China)", "https://api.moonshot.cn/v1", "https://platform.moonshot.cn/docs/", "high"),
    ("dashscope", "DashScope (international)", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "https://www.alibabacloud.com/help/en/model-studio/", "high"),
    ("dashscope_cn", "DashScope (China)", "https://dashscope.aliyuncs.com/compatible-mode/v1", "https://help.aliyun.com/zh/model-studio/", "high"),
    ("zai", "Z.AI", "https://api.z.ai/api/paas/v4", "https://docs.z.ai/", "high"),
    ("minimax", "MiniMax (international)", "https://api.minimax.io/v1", "https://platform.minimax.io/docs/", "high"),
    ("minimax_cn", "MiniMax (China)", "https://api.minimaxi.com/v1", "https://platform.minimaxi.com/document/", "high"),
    ("stepfun", "StepFun", "https://api.stepfun.com/v1", "https://platform.stepfun.com/docs/", "high"),
    ("arcee", "Arcee AI", "https://api.arcee.ai/api/v1", "https://docs.arcee.ai/", "medium"),
    ("gmi", "GMI Cloud", "https://api.gmi-serving.com/v1", "https://docs.gmicloud.ai/", "medium"),
    ("ollama_cloud", "Ollama Cloud", "https://ollama.com/v1", "https://docs.ollama.com/cloud", "high"),
    ("meta", "Meta Llama API", "https://api.llama.com/compat/v1", "https://llama.developer.meta.com/docs/", "medium"),
    ("opencode_zen", "OpenCode Zen", "https://opencode.ai/zen/v1", "https://opencode.ai/docs/zen/", "high"),
    ("kilocode", "Kilo Code", "https://api.kilo.ai/api/gateway", "https://kilo.ai/docs/", "medium"),
    ("chutes", "Chutes", "https://llm.chutes.ai/v1", "https://chutes.ai/docs", "high"),
    # Two platforms with their own keys: siliconflow.com (global) and siliconflow.cn (China).
    ("siliconflow", "SiliconFlow", "https://api.siliconflow.com/v1", "https://docs.siliconflow.com/", "high"),
    ("siliconflow_cn", "SiliconFlow (China)", "https://api.siliconflow.cn/v1", "https://docs.siliconflow.cn/", "high"),
    ("baidu_qianfan", "Baidu Qianfan", "https://qianfan.baidubce.com/v2", "https://cloud.baidu.com/doc/qianfan/", "high"),
    ("volcengine_ark", "Volcengine Ark", "https://ark.cn-beijing.volces.com/api/v3", "https://www.volcengine.com/docs/82379", "high"),
    ("venice", "Venice", "https://api.venice.ai/api/v1", "https://docs.venice.ai/", "high"),
    ("featherless", "Featherless AI", "https://api.featherless.ai/v1", "https://featherless.ai/docs", "high"),
    ("inference_net", "Inference.net", "https://api.inference.net/v1", "https://docs.inference.net/", "high"),
    ("lambda", "Lambda", "https://api.lambda.ai/v1", "https://docs.lambda.ai/public-cloud/lambda-chat-api/", "medium"),
    ("scaleway", "Scaleway", "https://api.scaleway.ai/v1", "https://www.scaleway.com/en/docs/generative-apis/", "high"),
    ("cohere", "Cohere (compatibility API)", "https://api.cohere.ai/compatibility/v1", "https://docs.cohere.com/docs/compatibility-api", "high"),
    ("ai21", "AI21 Jamba", "https://api.ai21.com/studio/v1", "https://docs.ai21.com/", "high"),
]
KINDS.extend(preset(kind, name, "gateway", url, docs, confidence=confidence,
                    auth="x-portkey-api-key" if kind == "portkey" else "bearer")
             for kind, name, url, docs, confidence in HOSTED)

LOCAL = [
    ("ollama", "Ollama", "http://localhost:11434/v1", "https://docs.ollama.com/openai"),
    ("lmstudio", "LM Studio", "http://localhost:1234/v1", "https://lmstudio.ai/docs/developer/openai-compat"),
    ("vllm", "vLLM", "http://localhost:8000/v1", "https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html"),
    ("sglang", "SGLang", "http://localhost:30000/v1", "https://docs.sglang.ai/"),
    ("llamacpp", "llama.cpp", "http://localhost:8080/v1", "https://github.com/ggml-org/llama.cpp/tree/master/tools/server"),
    ("jan", "Jan", "http://localhost:1337/v1", "https://jan.ai/docs/"),
    ("koboldcpp", "KoboldCpp", "http://localhost:5001/v1", "https://github.com/LostRuins/koboldcpp/wiki"),
    ("localai", "LocalAI", "http://localhost:8080/v1", "https://localai.io/basics/getting_started/"),
    ("text_generation_webui", "Text Generation WebUI", "http://localhost:5000/v1", "https://github.com/oobabooga/text-generation-webui/wiki/12-%E2%80%90-OpenAI-API"),
]
KINDS.extend(preset(kind, name, "local", url, docs, fields=("base_url", "key"), auth="optional-bearer")
             for kind, name, url, docs in LOCAL)
# Checked against the provider's own documentation (2026-10-23).
VERIFIED = {"arcee", "lambda", "nebius", "novita", "ollama_cloud", "opencode_zen", "siliconflow", "siliconflow_cn"}
for item in KINDS:
    item["verified"] = item["kind"] in VERIFIED
BY_KIND = {item["kind"]: item for item in KINDS}
OPTIONAL_KEY_KINDS = {row["kind"] for row in KINDS if row["group"] == "local"} | {
    "openai_compatible", "litellm", "bifrost"}
