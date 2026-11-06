from fastapi import APIRouter

from app.api.v1 import auth, categories, documents, groups, organization, users

api_router = APIRouter()
for module in (auth, users, groups, organization, categories, documents):
    api_router.include_router(module.router)
