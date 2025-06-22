from pydantic import BaseModel


class OpenTofuPayload(BaseModel):
    config: str  # Adjust fields as needed for your wrapper
