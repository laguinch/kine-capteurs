from pydantic import BaseModel

class Settings(BaseModel):
    app_name: str = "Kine Capteurs"
    database_url: str = "sqlite:///./storage/kine.db"

settings = Settings()
