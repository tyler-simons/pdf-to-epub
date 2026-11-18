from dataclasses import dataclass, field
import os
from pathlib import Path

@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv('LEAF_DATA_DIR', './data')).resolve())
    public: bool = field(default_factory=lambda: os.getenv('LEAF_PUBLIC', 'false').lower() == 'true')
    turnstile_site_key: str = field(default_factory=lambda: os.getenv('TURNSTILE_SITE_KEY',''))
    turnstile_secret_key: str = field(default_factory=lambda: os.getenv('TURNSTILE_SECRET_KEY',''),repr=False)
    public_hostname: str = field(default_factory=lambda: os.getenv('LEAF_PUBLIC_HOSTNAME',''))
    access_secret: str = field(default_factory=lambda: os.getenv('LEAF_ACCESS_SECRET',''),repr=False)
    requests_per_hour: int = 12
    ai_enabled: bool = field(default_factory=lambda: os.getenv('LEAF_AI_ENABLED','false').lower()=='true')
    api_key: str = field(default_factory=lambda: os.getenv('OPENAI_API_KEY',''),repr=False)
    max_ai_per_job_microusd: int = field(default_factory=lambda: int(os.getenv('LEAF_AI_PER_JOB_MICROUSD','100000')))
    max_upload_mb: int = 40
    max_pages: int = 1500
    retention_hours: int = 24
    job_timeout_seconds: int = field(default_factory=lambda: int(os.getenv('LEAF_JOB_TIMEOUT_SECONDS','300')))
    daily_jobs: int = field(default_factory=lambda: int(os.getenv('LEAF_DAILY_JOBS','20')))
    per_client_daily_jobs: int = field(default_factory=lambda: int(os.getenv('LEAF_PER_CLIENT_DAILY_JOBS','3')))
    daily_processing_seconds: int = field(default_factory=lambda: int(os.getenv('LEAF_DAILY_PROCESSING_SECONDS','3600')))
    max_pending_jobs: int = 10
    total_ai_microusd: int = field(default_factory=lambda: int(os.getenv('LEAF_AI_BUDGET_MICROUSD','10000000')))

    def __post_init__(self):
        if self.public and not (self.turnstile_site_key and self.turnstile_secret_key and self.public_hostname and len(self.access_secret)>=32):
            raise ValueError('Public access requires Turnstile keys, LEAF_PUBLIC_HOSTNAME and a stable LEAF_ACCESS_SECRET of at least 32 characters.')
        for key in ('max_upload_mb','max_pages','retention_hours','job_timeout_seconds','daily_jobs','per_client_daily_jobs','daily_processing_seconds','max_pending_jobs'):
            if getattr(self,key) <= 0: raise ValueError(f'{key} must be positive')
        if self.total_ai_microusd<0 or self.max_ai_per_job_microusd<0: raise ValueError('AI limits cannot be negative.')
        if self.ai_enabled and not self.api_key: raise ValueError('AI cleanup requires OPENAI_API_KEY.')
        self.data_dir=Path(self.data_dir).resolve()
