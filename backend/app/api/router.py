from fastapi import APIRouter

from app.api.v1 import auth, categories, chat, documents, groups, organization, search, users

api_router = APIRouter()
for module in (auth, users, groups, organization, categories, documents, search, chat):
    api_router.include_router(module.router)
