from fastapi import APIRouter

from app.api.v1 import auth, categories, chat, documents, groups, insights, organization, search, users

api_router = APIRouter()
for module in (auth, users, groups, organization, categories, documents, search, chat):
    api_router.include_router(module.router)
for router in (insights.comparisons, insights.evaluations, insights.analytics, insights.embeddings):
    api_router.include_router(router)
