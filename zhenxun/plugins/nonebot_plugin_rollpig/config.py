from pydantic import BaseModel
from typing import Optional

class Config(BaseModel):
    # --- AI 烤猪配置 ---
    rollpig_ai_enabled: bool = False  # 是否开启 AI 生成
    rollpig_deepseek_key: Optional[str] = None  # DeepSeek API Key
    rollpig_deepseek_base: str = "https://api.deepseek.com" # Base URL
    rollpig_model: str = "deepseek-chat" # 模型名称
    rollpig_roast_cooldown_hours: float = 8.0  # 烤群友普通模式冷却时长（小时）
    rollpig_storage_backend: str = "local"  # local / cloud
    rollpig_cloud_api_url: Optional[str] = None
    rollpig_cloud_token: Optional[str] = None
    rollpig_cloud_timeout: float = 3.0
    rollpig_cloud_strict_mode: bool = True  # true=云端异常直接失败；false=读接口可安全兜底，写接口仍提示稍后重试

    # --- 代理设置 (可选，如果服务器在国内连不上API) ---
    rollpig_proxy: Optional[str] = None
