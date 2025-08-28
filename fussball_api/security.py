from fastapi import Header, HTTPException, status

from .config import settings


async def get_api_key(api_key: str = Header(..., alias="X-API-Key")):
    """
    Dependency function to verify the API key from the request header.

    :param api_key: The API key passed in the 'X-API-Key' header.
    :raises HTTPException: If the API key is invalid.
    """
    if api_key != settings.API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key",
        )
