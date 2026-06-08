from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    TFL_APP_KEY: str
    OPENWEATHER_API_KEY: str
    SUPABASE_URL: str
    SUPABASE_KEY: str
    APP_ENV: str = "development"

    class Config:
        env_file = ".env"

settings = Settings()
